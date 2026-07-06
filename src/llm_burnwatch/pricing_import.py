"""Import pricing data from LiteLLM's `model_prices_and_context_window.json`
format into llm-burnwatch's own `pricing.json` schema.

`source` can be a local file path or an `http(s)://` URL. Fetching from a URL
is the only network access point llm-burnwatch has outside of its normally
network-free core -- see "Network boundaries" in ARCHITECTURE.md. It is never
triggered implicitly; it only runs when the user explicitly invokes
`llm-burnwatch pricing import <source>`.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

from ._messages import warn

_TIMEOUT_SECONDS = 10
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB
_CHUNK_SIZE = 64 * 1024


class PricingImportError(Exception):
    """Raised for any failure fetching or parsing a pricing source."""


def _reject_non_finite(value: str) -> float:
    raise PricingImportError(f"pricing source contains a non-finite number: {value}")


def _read_local_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _fetch_url(url: str) -> str:
    warn(f"fetching pricing from network: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "llm-burnwatch-pricing-import"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
            # `urlopen` follows redirects transparently, including an
            # https:// source redirecting to a plain http:// response --
            # silently downgrading a request the caller explicitly asked to
            # be encrypted. Refuse that specific downgrade (an http://
            # source redirecting elsewhere was never protected to begin
            # with, so there's nothing to downgrade there).
            final_url = response.geturl()
            if url.startswith("https://") and not final_url.startswith("https://"):
                raise PricingImportError(
                    f"refusing to follow a redirect from {url!r} to "
                    f"{final_url!r}: an https:// source must not be "
                    "downgraded to a non-https:// response"
                )
            chunks = []
            total = 0
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    raise PricingImportError(
                        f"response exceeded the {_MAX_RESPONSE_BYTES} byte limit"
                    )
                chunks.append(chunk)
    except HTTPError as exc:
        raise PricingImportError(f"HTTP error fetching {url}: {exc}") from exc
    except URLError as exc:
        raise PricingImportError(f"network error fetching {url}: {exc}") from exc
    return b"".join(chunks).decode("utf-8")


def fetch_source(source: str) -> str:
    """Read raw JSON text from `source`.

    `source` must be a local file path or an `http(s)://` URL -- any other
    URL scheme (`file://`, `ftp://`, ...) is rejected. This is the only place
    llm-burnwatch accepts a URL from the user, and it should only ever mean
    "fetch over HTTP(S)", not "read an arbitrary local/remote resource".
    """
    if source.startswith("http://") or source.startswith("https://"):
        return _fetch_url(source)
    if "://" in source:
        scheme = source.split("://", 1)[0]
        raise PricingImportError(
            f"unsupported source scheme {scheme!r}; use a local file path or an http(s):// URL"
        )
    return _read_local_file(source)


def parse_litellm_pricing(raw_json: str) -> dict:
    """Parse LiteLLM's `model_prices_and_context_window.json` format into
    llm-burnwatch's own `pricing.json` schema.

    Tolerant of unknown fields and of entries that don't look like a real
    model price (e.g. the `sample_spec` placeholder key LiteLLM ships):
    anything missing `input_cost_per_token`/`output_cost_per_token` is
    silently skipped rather than raising, so a future LiteLLM format change
    that adds new metadata fields doesn't break this parser.
    """
    try:
        data = json.loads(raw_json, parse_constant=_reject_non_finite)
    except json.JSONDecodeError as exc:
        raise PricingImportError(f"invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise PricingImportError("expected a top-level JSON object")

    models: dict[str, dict] = {}
    for model_name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        input_cost = entry.get("input_cost_per_token")
        output_cost = entry.get("output_cost_per_token")
        if not isinstance(input_cost, (int, float)) or isinstance(input_cost, bool):
            continue
        if not isinstance(output_cost, (int, float)) or isinstance(output_cost, bool):
            continue

        model_pricing = {
            "input_per_1m": input_cost * 1_000_000,
            "output_per_1m": output_cost * 1_000_000,
        }
        cached_cost = entry.get("cache_read_input_token_cost")
        if isinstance(cached_cost, (int, float)) and not isinstance(cached_cost, bool):
            model_pricing["cached_input_per_1m"] = cached_cost * 1_000_000
        models[model_name] = model_pricing

    if not models:
        raise PricingImportError(
            "no usable model entries found (expected fields like "
            "'input_cost_per_token'/'output_cost_per_token')"
        )

    return {
        "last_updated": datetime.now(timezone.utc).date().isoformat(),
        "models": models,
    }


def import_pricing(source: str, dest: Path) -> dict:
    """Fetch+parse `source` and atomically write the result to `dest`
    (creating parent directories as needed). Returns the parsed pricing dict.
    """
    raw = fetch_source(source)
    pricing = parse_litellm_pricing(raw)

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=".pricing-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(pricing, fh, indent=2)
        os.replace(tmp_path, dest)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise

    return pricing
