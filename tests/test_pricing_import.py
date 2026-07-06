from __future__ import annotations

import json
import socket

import pytest

from llm_burnwatch import pricing_import
from llm_burnwatch.pricing_import import (
    PricingImportError,
    fetch_source,
    import_pricing,
    parse_litellm_pricing,
)

LITELLM_SAMPLE = {
    "gpt-4o": {
        "input_cost_per_token": 0.000005,
        "output_cost_per_token": 0.000015,
        "cache_read_input_token_cost": 0.0000025,
        "litellm_provider": "openai",
    },
    "claude-3-5-sonnet": {
        "input_cost_per_token": 0.000003,
        "output_cost_per_token": 0.000015,
    },
    "sample_spec": {
        "some_metadata_only_field": True,
    },
}


# --- fetch_source: scheme allowlist / local file reading -------------------


def test_fetch_source_reads_local_file(tmp_path):
    path = tmp_path / "pricing.json"
    path.write_text('{"k": "v"}', encoding="utf-8")
    assert fetch_source(str(path)) == '{"k": "v"}'


def test_fetch_source_rejects_missing_local_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        fetch_source(str(tmp_path / "does-not-exist.json"))


def test_fetch_source_rejects_non_http_scheme(tmp_path):
    with pytest.raises(PricingImportError, match="unsupported source scheme 'ftp'"):
        fetch_source("ftp://example.com/pricing.json")


def test_fetch_source_rejects_file_scheme(tmp_path):
    with pytest.raises(PricingImportError, match="unsupported source scheme 'file'"):
        fetch_source("file:///etc/passwd")


# --- fetch_source: http(s):// path, via a mocked urlopen --------------------


class _FakeResponse:
    def __init__(self, payload: bytes, url: str = "https://example.com/pricing.json"):
        self._chunks = [payload]
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, size):
        if not self._chunks:
            return b""
        chunk, rest = self._chunks[0][:size], self._chunks[0][size:]
        if rest:
            self._chunks[0] = rest
        else:
            self._chunks.pop(0)
        return chunk

    def geturl(self):
        return self._url


def test_fetch_url_returns_decoded_body(monkeypatch):
    payload = json.dumps(LITELLM_SAMPLE).encode("utf-8")

    captured_request = {}

    def fake_urlopen(request, timeout):
        captured_request["url"] = request.full_url
        captured_request["headers"] = dict(request.header_items())
        captured_request["timeout"] = timeout
        return _FakeResponse(payload)

    monkeypatch.setattr(pricing_import.urllib.request, "urlopen", fake_urlopen)

    result = fetch_source("https://example.com/pricing.json")

    assert json.loads(result) == LITELLM_SAMPLE
    assert captured_request["url"] == "https://example.com/pricing.json"
    assert captured_request["headers"]["User-agent"] == "llm-burnwatch-pricing-import"
    assert captured_request["timeout"] == pricing_import._TIMEOUT_SECONDS


def test_fetch_url_enforces_response_size_cap(monkeypatch):
    oversized = b"x" * (pricing_import._MAX_RESPONSE_BYTES + 1)

    def fake_urlopen(request, timeout):
        return _FakeResponse(oversized)

    monkeypatch.setattr(pricing_import.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PricingImportError, match="exceeded the .* byte limit"):
        fetch_source("https://example.com/pricing.json")


def test_fetch_url_wraps_http_error(monkeypatch):
    from urllib.error import HTTPError

    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(pricing_import.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PricingImportError, match="HTTP error fetching"):
        fetch_source("https://example.com/pricing.json")


def test_fetch_url_rejects_https_to_http_redirect_downgrade(monkeypatch):
    # `urlopen` follows redirects transparently -- an https:// source that
    # redirects to a plain http:// response would otherwise be fetched
    # (and trusted) without the caller ever knowing encryption was dropped
    # partway through.
    def fake_urlopen(request, timeout):
        return _FakeResponse(b"{}", url="http://example.com/pricing.json")

    monkeypatch.setattr(pricing_import.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PricingImportError, match="downgraded to a non-https"):
        fetch_source("https://example.com/pricing.json")


def test_fetch_url_allows_https_to_https_redirect(monkeypatch):
    payload = json.dumps(LITELLM_SAMPLE).encode("utf-8")

    def fake_urlopen(request, timeout):
        return _FakeResponse(payload, url="https://cdn.example.com/pricing.json")

    monkeypatch.setattr(pricing_import.urllib.request, "urlopen", fake_urlopen)

    result = fetch_source("https://example.com/pricing.json")
    assert json.loads(result) == LITELLM_SAMPLE


def test_fetch_url_wraps_network_error(monkeypatch):
    from urllib.error import URLError

    def fake_urlopen(request, timeout):
        raise URLError(socket.gaierror("name resolution failed"))

    monkeypatch.setattr(pricing_import.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PricingImportError, match="network error fetching"):
        fetch_source("https://example.com/pricing.json")


# --- parse_litellm_pricing ---------------------------------------------------


def test_parse_converts_per_token_to_per_1m_and_skips_placeholder_entries():
    pricing = parse_litellm_pricing(json.dumps(LITELLM_SAMPLE))

    assert set(pricing["models"]) == {"gpt-4o", "claude-3-5-sonnet"}
    assert pricing["models"]["gpt-4o"] == {
        "input_per_1m": pytest.approx(5.0),
        "output_per_1m": pytest.approx(15.0),
        "cached_input_per_1m": pytest.approx(2.5),
    }
    assert pricing["models"]["claude-3-5-sonnet"] == {
        "input_per_1m": pytest.approx(3.0),
        "output_per_1m": pytest.approx(15.0),
    }
    assert "cached_input_per_1m" not in pricing["models"]["claude-3-5-sonnet"]


def test_parse_sets_last_updated_to_today_utc():
    from datetime import datetime, timezone

    pricing = parse_litellm_pricing(json.dumps(LITELLM_SAMPLE))
    assert pricing["last_updated"] == datetime.now(timezone.utc).date().isoformat()


def test_parse_rejects_invalid_json():
    with pytest.raises(PricingImportError, match="invalid JSON"):
        parse_litellm_pricing("{not valid json")


def test_parse_rejects_non_object_top_level():
    with pytest.raises(PricingImportError, match="expected a top-level JSON object"):
        parse_litellm_pricing(json.dumps([1, 2, 3]))


def test_parse_rejects_infinity():
    with pytest.raises(PricingImportError, match="non-finite number"):
        parse_litellm_pricing('{"m": {"input_cost_per_token": Infinity}}')


def test_parse_rejects_nan():
    with pytest.raises(PricingImportError, match="non-finite number"):
        parse_litellm_pricing('{"m": {"input_cost_per_token": NaN}}')


def test_parse_rejects_when_no_usable_models_found():
    with pytest.raises(PricingImportError, match="no usable model entries found"):
        parse_litellm_pricing(json.dumps({"sample_spec": {"some_field": True}}))


def test_parse_skips_entries_with_bool_costs():
    # bool is a subclass of int in Python; must not be silently accepted as a price.
    data = {
        "weird-model": {"input_cost_per_token": True, "output_cost_per_token": 0.00001},
        "good-model": {"input_cost_per_token": 0.00001, "output_cost_per_token": 0.00002},
    }
    pricing = parse_litellm_pricing(json.dumps(data))
    assert set(pricing["models"]) == {"good-model"}


def test_parse_skips_non_dict_entries():
    data = {"gpt-4o": LITELLM_SAMPLE["gpt-4o"], "some_string_value": "not a model"}
    pricing = parse_litellm_pricing(json.dumps(data))
    assert set(pricing["models"]) == {"gpt-4o"}


# --- import_pricing: end-to-end local-file import + atomic write -----------


def test_import_pricing_writes_dest_file(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(LITELLM_SAMPLE), encoding="utf-8")
    dest = tmp_path / "config" / "llm-burnwatch" / "pricing.json"

    result = import_pricing(str(source), dest)

    assert dest.exists()
    on_disk = json.loads(dest.read_text(encoding="utf-8"))
    assert on_disk == result
    assert set(on_disk["models"]) == {"gpt-4o", "claude-3-5-sonnet"}


def test_import_pricing_creates_parent_directories(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(LITELLM_SAMPLE), encoding="utf-8")
    dest = tmp_path / "config" / "llm-burnwatch" / "pricing.json"

    assert not dest.parent.exists()
    import_pricing(str(source), dest)
    assert dest.parent.is_dir()


def test_import_pricing_leaves_no_tmp_file_behind_on_success(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(LITELLM_SAMPLE), encoding="utf-8")
    dest = tmp_path / "config" / "llm-burnwatch" / "pricing.json"

    import_pricing(str(source), dest)

    leftovers = [p for p in dest.parent.iterdir() if p.name.startswith(".pricing-")]
    assert leftovers == []


def test_import_pricing_does_not_write_dest_on_parse_failure(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{not valid json", encoding="utf-8")
    dest = tmp_path / "config" / "llm-burnwatch" / "pricing.json"

    with pytest.raises(PricingImportError):
        import_pricing(str(source), dest)

    assert not dest.exists()


def test_import_pricing_overwrites_existing_dest(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(LITELLM_SAMPLE), encoding="utf-8")
    dest = tmp_path / "config" / "llm-burnwatch" / "pricing.json"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"last_updated": "2020-01-01", "models": {}}', encoding="utf-8")

    import_pricing(str(source), dest)

    on_disk = json.loads(dest.read_text(encoding="utf-8"))
    assert set(on_disk["models"]) == {"gpt-4o", "claude-3-5-sonnet"}
