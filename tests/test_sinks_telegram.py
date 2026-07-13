from __future__ import annotations

import json

import pytest

from llm_burnwatch.detectors.protocol import Alert
from llm_burnwatch.sinks import webhook_sink
from llm_burnwatch.sinks.protocol import SinkError
from llm_burnwatch.sinks.telegram_sink import TelegramSink

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
    def __init__(
        self,
        status: int = 200,
        url: str = "https://api.telegram.org/bot123:ABC-TOKEN/sendMessage",
    ):
        self.status = status
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def geturl(self):
        return self._url


def test_send_posts_plain_text_to_telegram_bot_api(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        return _FakeResponse(200, url=request.full_url)

    monkeypatch.setattr(webhook_sink.urllib.request, "urlopen", fake_urlopen)

    TelegramSink("123:ABC-TOKEN", "-100987654321").send(_ALERT)

    assert captured["url"] == "https://api.telegram.org/bot123:ABC-TOKEN/sendMessage"
    assert captured["body"] == {
        "chat_id": "-100987654321",
        "text": (
            "\U0001f6a8 llm-burnwatch: rule violated: call cost limit exceeded "
            "-- call cost exceeded (record #3)"
        ),
    }


def test_send_raises_sink_error_on_non_2xx_status(monkeypatch):
    monkeypatch.setattr(
        webhook_sink.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse(500, url=request.full_url),
    )

    with pytest.raises(SinkError, match="HTTP 500"):
        TelegramSink("123:ABC-TOKEN", "-100987654321").send(_ALERT)


def test_send_error_message_omits_bot_token(monkeypatch):
    monkeypatch.setattr(
        webhook_sink.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse(500, url=request.full_url),
    )

    with pytest.raises(SinkError) as exc_info:
        TelegramSink("123456789:AAExampleSecretBotToken", "-100987654321").send(_ALERT)

    assert "AAExampleSecretBotToken" not in str(exc_info.value)
    assert "api.telegram.org" in str(exc_info.value)
