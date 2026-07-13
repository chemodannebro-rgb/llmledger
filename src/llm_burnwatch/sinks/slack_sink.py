"""Slack sink: posts an alert to a Slack incoming-webhook URL.

Slack's incoming webhooks accept a plain HTTP POST of `{"text": ...}` JSON --
the same transport `WebhookSink` already implements, just a different
payload shape -- so this composes `WebhookSink.post_json` instead of
reimplementing the HTTP POST/error handling.

The message text itself (B4) is built by `alert_text.format_alert_oneline`,
the same shared rendering layer `detect`'s console output (B1/B2/D1) uses --
a plain-language incident type, severity emoji, money-first detail, and
record number, all derived only from `Alert.evidence`/`message`. `Alert`
itself and `--json`/`--follow` output are unaffected by this formatting.
"""

from __future__ import annotations

from ..alert_text import format_alert_oneline
from ..detectors.protocol import Alert
from .webhook_sink import TIMEOUT_SECONDS, WebhookSink


class SlackSink:
    name = "slack"

    def __init__(self, webhook_url: str, timeout: float = TIMEOUT_SECONDS) -> None:
        self._webhook = WebhookSink(webhook_url, timeout=timeout)

    def send(self, alert: Alert) -> None:
        text = format_alert_oneline(alert)
        self._webhook.post_json({"text": text})
