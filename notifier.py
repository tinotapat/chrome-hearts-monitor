#!/usr/bin/env python3
"""
Notification backends, chosen via the NOTIFY_METHOD env var:

  NOTIFY_METHOD=discord     Easiest. Free. No phone. Just a webhook URL.
  NOTIFY_METHOD=twilio      SMS. Reliable, paid (~$1/mo number + ~$0.008/text).
  NOTIFY_METHOD=email_sms   SMS via your carrier's email gateway. Free, flaky.

Discord/Twilio use plain HTTP (no extra dependency). Email uses stdlib smtplib.
"""

from __future__ import annotations

import os
import re
import smtplib
import sys
from email.mime.text import MIMEText

import requests


def _http_error(service: str, resp: requests.Response) -> RuntimeError:
    return RuntimeError(f"{service} returned HTTP {resp.status_code}: {resp.text[:200]!r}")


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"missing env var: {name}")
    return val


def _send_discord(body: str, *, suppress_embeds: bool = False) -> None:
    """
    POST to a Discord channel webhook. To get the URL:
      Discord -> Server Settings -> Integrations -> Webhooks -> New Webhook
      -> pick a channel -> Copy Webhook URL.
    The URL itself is the secret; anyone with it can post to that channel.
    """
    url = _env("DISCORD_WEBHOOK_URL")
    if not re.fullmatch(r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9._-]+", url):
        raise RuntimeError("DISCORD_WEBHOOK_URL must be a valid discord.com webhook URL")
    payload = {"content": body[:2000], "allowed_mentions": {"parse": []}}
    if suppress_embeds:
        payload["flags"] = 1 << 2  # Discord SUPPRESS_EMBEDS
    try:
        resp = requests.post(url, params={"wait": "true"}, json=payload,
                             timeout=30, allow_redirects=False)
    except requests.RequestException as exc:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            raise RuntimeError(f"Discord webhook returned HTTP {exc.response.status_code}") from None
        raise RuntimeError("Discord webhook request failed; delivery may be unconfirmed") from None
    if resp.status_code >= 300:
        raise RuntimeError(f"Discord webhook returned HTTP {resp.status_code}") from None
    print("[discord] sent")


def _send_twilio(body: str) -> None:
    sid = _env("TWILIO_ACCOUNT_SID")
    token = _env("TWILIO_AUTH_TOKEN")
    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"From": _env("TWILIO_FROM"), "To": _env("TWILIO_TO"), "Body": body},
            timeout=30,
        )
    except requests.RequestException as exc:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            raise _http_error("Twilio", exc.response) from exc
        raise RuntimeError("Twilio request failed") from exc
    if resp.status_code >= 300:
        raise _http_error("Twilio", resp)
    print("[twilio] sent")


def _send_email_sms(body: str) -> None:
    """
    Free path: email -> carrier SMS gateway. Set SMS_TO to your gateway address.
        AT&T  5551234567@txt.att.net   Verizon  5551234567@vtext.com
        T-Mobile 5551234567@tmomail.net   Google Fi 5551234567@msg.fi.google.com
    """
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = _env("SMTP_USER")
    password = _env("SMTP_PASS")
    to_addr = _env("SMS_TO")
    msg = MIMEText(body)
    msg["From"], msg["To"], msg["Subject"] = user, to_addr, ""
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
    print("[email_sms] sent")


def send_notification(body: str, *, suppress_embeds: bool = False) -> None:
    method = os.environ.get("NOTIFY_METHOD", "discord").lower()
    if method == "discord":
        _send_discord(body, suppress_embeds=suppress_embeds)
    elif method == "twilio":
        _send_twilio(body)
    elif method == "email_sms":
        _send_email_sms(body)
    else:
        print(f"[notifier] unknown NOTIFY_METHOD={method!r}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    # manual test: NOTIFY_METHOD=discord DISCORD_WEBHOOK_URL=... python notifier.py "hi"
    send_notification(sys.argv[1] if len(sys.argv) > 1 else "test from chrome hearts monitor")
