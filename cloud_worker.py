"""Render entry point: explicit baseline, persistent history, one worker."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time

import requests
import chrome_hearts_monitor as monitor


def check_storage() -> Path:
    root = Path(os.environ.get("CH_DATA_DIR", "/data")).resolve()
    if os.environ.get("RENDER") == "true" and not root.is_mount():
        raise RuntimeError("persistent disk is not mounted; refusing to run")
    if monitor.STATE_FILE.resolve().parent != root:
        raise RuntimeError("CH_STATE_FILE must be directly inside CH_DATA_DIR")
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".write-check"
    probe.write_text("ok")
    probe.unlink()
    return root


def baseline(session: requests.Session) -> None:
    marker = monitor.STATE_FILE.with_suffix(".initialized")
    if monitor.STATE_FILE.exists():
        state = monitor.load_state()
        if not state:
            raise RuntimeError("existing history is empty; restore from backup")
        monitor.log(f"Baseline already exists: {len(state)} remembered products; left unchanged.")
        return
    if marker.exists():
        raise RuntimeError("history is missing after initialization; restore from backup")
    catalog = {}
    for attempt in range(2):
        started = time.monotonic()
        products, errors = monitor.crawl(session, strict=True)
        if errors or not products:
            raise RuntimeError("baseline requires two complete, nonempty crawls; no state saved")
        catalog.update(products)
        if attempt == 0:
            time.sleep(max(2, monitor.POLL_SECONDS - (time.monotonic() - started)))
    monitor.save_state(catalog)
    marker.write_text(json.dumps({"products": len(catalog), "initialized_at": time.time()}))
    monitor.log(f"BASELINE READY: {len(catalog)} products saved; zero product alerts sent.")


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-webhook", action="store_true")
    args = parser.parse_args()
    if args.test_webhook:
        from notifier import send_notification
        send_notification("TEST ONLY — Chrome Hearts monitor delivery check. This is not a product drop.")
        return 0
    root = check_storage()
    lock = (root / ".monitor.lock").open("a")
    # Held for the process lifetime; a second worker must never share this history.
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    mode = os.environ.get("CH_RUN_MODE", "baseline")
    if monitor.POLL_SECONDS < 30:
        raise RuntimeError("CH_POLL_SECONDS must be at least 30")
    if mode == "baseline":
        with requests.Session() as session:
            session.headers.update(monitor.HEADERS)
            baseline(session)
        monitor.log("Baseline review mode: waiting. Set CH_RUN_MODE=monitor to enable alerts.")
        while True:
            time.sleep(60)
    if mode != "monitor":
        raise RuntimeError("CH_RUN_MODE must be baseline or monitor")
    state = monitor.load_state()
    if not state:
        raise RuntimeError("baseline is empty; monitoring cannot start")
    from notifier import _env
    _env("DISCORD_WEBHOOK_URL")
    monitor.log(f"Loaded persistent history: {len(state)} products.")
    sys.argv = ["chrome_hearts_monitor.py", "--loop"]
    return monitor.main()


def main() -> int:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        return run()
    except Exception as exc:
        # Never print chained request tracebacks or credential-bearing URLs.
        detail = str(exc).replace(os.environ.get("DISCORD_WEBHOOK_URL") or "\x00", "[redacted]")
        monitor.log(f"Worker stopped ({type(exc).__name__}): {detail}")
        if isinstance(exc, monitor.CrawlBlocked):
            time.sleep(exc.retry_after)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
