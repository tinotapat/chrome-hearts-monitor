import json
import os
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

import requests
import cloud_worker as worker
import chrome_hearts_monitor as monitor
import notifier


def product(pid):
    return monitor.Product(pid, "Sample " + pid, "100", "shirt", "https://www.chromehearts.com/p/" + pid + ".html")


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "seen_products.json"
        self.patch = patch.object(monitor, "STATE_FILE", self.state)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_two_complete_baselines_merge_silently_then_restart_sends_nothing(self):
        with patch.object(monitor, "crawl", side_effect=[({"A": product("A")}, 0), ({"B": product("B")}, 0)]), \
                patch.object(monitor.time, "sleep"), patch.object(monitor, "_send") as send:
            worker.baseline(object())
            self.assertEqual(set(monitor.load_state()), {"A", "B"})
            send.assert_not_called()
        with patch.object(monitor, "crawl", return_value=({"A": product("A"), "B": product("B")}, 0)), \
                patch.object(monitor, "notify_new") as notify:
            monitor.sweep(object(), seed=False, dry_run=False)
            notify.assert_not_called()

    def test_old_product_disappears_then_returns_without_alert(self):
        monitor.save_state({"A": product("A"), "B": product("B")})
        with patch.object(monitor, "crawl", side_effect=[({"B": product("B")}, 0), ({"A": product("A")}, 0)]), \
                patch.object(monitor, "notify_new") as notify:
            monitor.sweep(object(), seed=False, dry_run=False)
            monitor.sweep(object(), seed=False, dry_run=False)
            notify.assert_not_called()
        self.assertEqual(set(monitor.load_state()), {"A", "B"})

    def test_new_product_alerts_once_and_is_persisted(self):
        monitor.save_state({"A": product("A")})
        with patch.object(monitor, "crawl", return_value=({"A": product("A"), "B": product("B")}, 0)), \
                patch.object(monitor, "notify_new") as notify:
            monitor.sweep(object(), seed=False, dry_run=False)
            monitor.sweep(object(), seed=False, dry_run=False)
            notify.assert_called_once()
            self.assertEqual(notify.call_args.args[0][0].pid, "B")

    def test_second_baseline_failure_writes_nothing(self):
        with patch.object(monitor, "crawl", side_effect=[({"A": product("A")}, 0), ({"B": product("B")}, 1)]), \
                patch.object(monitor.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "complete"):
                worker.baseline(object())
        self.assertFalse(self.state.exists())

    def test_empty_baseline_writes_nothing(self):
        with patch.object(monitor, "crawl", return_value=({}, 0)):
            with self.assertRaises(RuntimeError):
                worker.baseline(object())
        self.assertFalse(self.state.exists())

    def test_restart_in_baseline_mode_preserves_existing_history(self):
        monitor.save_state({"A": product("A")})
        before = self.state.read_bytes()
        with patch.object(monitor, "crawl") as crawl:
            worker.baseline(object())
            crawl.assert_not_called()
        self.assertEqual(self.state.read_bytes(), before)

    def test_missing_history_after_initialization_cannot_reseed(self):
        self.state.with_suffix(".initialized").write_text("{}")
        with patch.object(monitor, "crawl") as crawl:
            with self.assertRaisesRegex(RuntimeError, "missing"):
                worker.baseline(object())
            crawl.assert_not_called()

    def test_corrupt_history_cannot_reseed(self):
        self.state.write_text("broken")
        with patch.object(monitor, "crawl") as crawl:
            with self.assertRaises(json.JSONDecodeError):
                worker.baseline(object())
            crawl.assert_not_called()

    def test_wrong_state_shape_is_rejected(self):
        self.state.write_text('["A"]')
        with self.assertRaisesRegex(RuntimeError, "invalid state"):
            monitor.load_state()

    def test_seed_cannot_overwrite_existing_history(self):
        monitor.save_state({"A": product("A")})
        with patch.object(monitor, "crawl") as crawl:
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                monitor.sweep(object(), seed=True, dry_run=True)
            crawl.assert_not_called()

    def test_strict_sitemap_failure_prevents_baseline(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("offline")
        with self.assertRaisesRegex(RuntimeError, "sitemap refresh failed"):
            monitor.sweep(session, seed=True, dry_run=True)
        self.assertFalse(self.state.exists())

    def test_missing_render_mount_fails_before_requests(self):
        with patch.dict(os.environ, {"RENDER": "true", "CH_DATA_DIR": self.temp.name}), \
                patch.object(Path, "is_mount", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not mounted"):
                worker.check_storage()

    def test_storefront_rate_limit_stops_immediately_and_honors_retry_after(self):
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "900"
        session = Mock()
        session.get.return_value = response
        with self.assertRaises(monitor.CrawlBlocked) as caught:
            monitor.fetch(session, monitor.BASE)
        session.get.assert_called_once()
        self.assertEqual(caught.exception.retry_after, 900)

    def test_storefront_denial_stops_without_retrying(self):
        response = requests.Response()
        response.status_code = 403
        session = Mock()
        session.get.return_value = response
        with self.assertRaises(monitor.CrawlBlocked):
            monitor.fetch(session, monitor.BASE)
        session.get.assert_called_once()

    def test_webhook_secret_is_not_in_full_failure_traceback(self):
        url = "https://discord.com/api/webhooks/123/secret-token"
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": url}), \
                patch.object(requests, "post", side_effect=requests.ConnectionError(url)):
            try:
                notifier._send_discord("test")
            except RuntimeError:
                formatted = traceback.format_exc()
            else:
                self.fail("Expected delivery failure")
        self.assertNotIn("secret-token", formatted)
        self.assertNotIn("https://discord.com", formatted)

    def test_discord_response_body_cannot_leak_secret(self):
        response = Mock(status_code=400, text="secret-token")
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret-token"}), \
                patch.object(requests, "post", return_value=response):
            with self.assertRaises(RuntimeError) as caught:
                notifier._send_discord("test")
        self.assertNotIn("secret-token", str(caught.exception))

    def test_discord_waits_for_confirmation_and_disables_mentions(self):
        response = Mock(status_code=200)
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/test-token"}), \
                patch.object(requests, "post", return_value=response) as post:
            notifier._send_discord("test @everyone")
        self.assertEqual(post.call_args.kwargs["params"], {"wait": "true"})
        self.assertEqual(post.call_args.kwargs["json"]["allowed_mentions"], {"parse": []})
        self.assertFalse(post.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
