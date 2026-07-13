from __future__ import annotations

import json

import pytest

from llm_burnwatch.detectors.protocol import Alert
from llm_burnwatch.sinks import webhook_sink
from llm_burnwatch.sinks.protocol import SinkError
from llm_burnwatch.sinks.slack_sink import SlackSink

_ALERT = Alert(
    detector="rules",
    severity="critical",
    kind="call_cost_exceeded",
    group_key=("chat", "gpt-4o"),
    record_ref=3,
    evidence={"call_cost_usd": 1.5},
    message="call cost exceeded",
)


class _FakeResponse:
    def __init__(self, status: int = 200, url: str = "https://hooks.slack.com/services/T/B/X"):
        self.status = status
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def geturl(self):
        return self._url


def test_send_posts_slack_compatible_text_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        return _FakeResponse(200, url=request.full_url)

    monkeypatch.setattr(webhook_sink.urllib.request, "urlopen", fake_urlopen)

    SlackSink("https://hooks.slack.com/services/T/B/X").send(_ALERT)

    assert captured["url"] == "https://hooks.slack.com/services/T/B/X"
    assert captured["body"] == {
        "text": (
            "\U0001f6a8 llm-burnwatch: rule violated: call cost limit exceeded "
            "-- call cost exceeded (record #3)"
        )
    }


def test_send_raises_sink_error_on_non_2xx_status(monkeypatch):
    monkeypatch.setattr(
        webhook_sink.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse(500, url=request.full_url),
    )

    with pytest.raises(SinkError, match="HTTP 500"):
        SlackSink("https://hooks.slack.com/services/T/B/X").send(_ALERT)


def test_constructor_rejects_non_http_schemes():
    # SlackSink composes WebhookSink internally, so it inherits the same
    # scheme validation -- this asserts that inheritance actually holds.
    with pytest.raises(ValueError, match="http"):
        SlackSink("file:///etc/passwd")


def test_send_error_message_omits_secret_webhook_path(monkeypatch):
    monkeypatch.setattr(
        webhook_sink.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse(500, url=request.full_url),
    )

    with pytest.raises(SinkError) as exc_info:
        SlackSink("https://hooks.slack.com/services/T000/B000/SECRETSECRETSECRET").send(_ALERT)

    assert "SECRETSECRETSECRET" not in str(exc_info.value)
    assert "hooks.slack.com" in str(exc_info.value)
