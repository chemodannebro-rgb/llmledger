"""CostTracker: logs LLM/agent call cost data to a local JSONL file and
produces cost reports. Zero third-party dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from ._messages import warn
from .anomaly.constants import PII_FIELD_LENGTH_THRESHOLD
from .logreader import iter_log_records

SCHEMA_VERSION = "1.0"

_DEFAULT_PRICING_PATH = Path(__file__).with_name("pricing.json")


def load_default_pricing() -> dict:
    with _DEFAULT_PRICING_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def merge_pricing_overrides(base: dict, overrides: dict[str, dict]) -> dict:
    """Return a new pricing dict: `base` (e.g. `load_default_pricing()`)
    with each `overrides[model]` rate dict layered on top of `base`'s
    `models` map, one model at a time.

    This is the "point override" path for a new/unlisted model or a
    custom negotiated rate: without it, adding one model's rate requires
    hand-copying the *entire* pricing file (all other built-in models)
    into a custom file/dict just to add one entry, since passing a whole
    replacement dict to `CostTracker(pricing=...)` discards every model
    `base` doesn't repeat. `base` itself is never mutated.
    """
    merged_models = dict(base.get("models", {}))
    for model, rates in overrides.items():
        merged_models[model] = rates
    return {**base, "models": merged_models}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get(obj: Any, name: str, default=None):
    """Read a field from either an attribute-style object (e.g. an SDK
    response model) or a plain dict, whichever `obj` happens to be.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class CostTracker:
    """Logs one JSONL record per `log_call()` and can summarize the
    resulting log via `report()`.

    Performance note: `log_call()` (and the `log_openai_response`/
    `log_anthropic_response` adapters) are fully synchronous. Each call
    appends exactly one short line to a local file via a single OS `write()`
    (through the stdlib `logging` module). A typical agent loop spends whole
    seconds waiting on the LLM API call itself; this local disk write costs
    microseconds and is not a bottleneck in that hot path.
    """

    _warned_about_extra_length = False

    def __init__(
        self,
        log_file,
        *,
        pricing: dict | None = None,
        pricing_overrides: dict[str, dict] | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ) -> None:
        """`pricing`, if given, replaces the built-in `pricing.json` entirely
        (you're responsible for every model you'll log against). For adding
        or overriding just one or two models on top of the built-in rates
        (e.g. a new/unlisted model, or a custom negotiated rate) without
        hand-copying the whole file, pass `pricing_overrides` instead, e.g.
        `pricing_overrides={"my-model": {"input_per_1m": 3.0, "output_per_1m": 9.0}}`
        -- every other built-in model keeps working unchanged. Passing both
        is an error: `pricing` already fully determines the rate table, so
        layering `pricing_overrides` on top of an explicit `pricing` would
        silently ignore whichever one the caller didn't expect to lose.
        """
        if pricing is not None and pricing_overrides is not None:
            raise ValueError(
                "pass either pricing= (full replacement) or pricing_overrides= "
                "(point overrides on top of the built-in pricing.json), not both"
            )
        if pricing_overrides is not None:
            self.pricing = merge_pricing_overrides(load_default_pricing(), pricing_overrides)
        else:
            self.pricing = pricing if pricing is not None else load_default_pricing()
        self._read_path = Path(log_file)
        self._write_path = self._resolve_write_path(self._read_path)

        try:
            self._write_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError(
                f"cannot create log directory {self._write_path.parent}: {exc}"
            ) from exc

        self._logger = self._build_logger(self._write_path, max_bytes, backup_count)

        try:
            os.chmod(self._write_path, 0o600)
        except OSError as exc:
            raise OSError(
                f"cannot set permissions on log file {self._write_path}: {exc}"
            ) from exc

    @staticmethod
    def _resolve_write_path(read_path: Path) -> Path:
        is_directory_mode = read_path.is_dir() or str(read_path).endswith(os.sep)
        if is_directory_mode:
            unique_name = f"{os.getpid()}-{uuid.uuid4().hex[:8]}.jsonl"
            return read_path / unique_name
        return read_path

    @staticmethod
    def _build_logger(path: Path, max_bytes: int, backup_count: int) -> logging.Logger:
        logger_name = f"llmledger.tracker.{path.resolve()}"
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            handler = RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        return logger

    def _check_extra_for_pii(self, extra: dict) -> None:
        if CostTracker._warned_about_extra_length:
            return
        for key, value in extra.items():
            if isinstance(value, str) and len(value) > PII_FIELD_LENGTH_THRESHOLD:
                warn(
                    f"extra field '{key}' is {len(value)} characters long; "
                    "logging raw prompt/response content risks leaking sensitive "
                    "data into the log file. Consider logging a summary or hash "
                    "instead."
                )
                CostTracker._warned_about_extra_length = True
                return

    def _resolve_cost_micros(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
        cost: float | None,
        pricing: dict | None,
    ) -> int:
        """Return cost as an integer number of micros (1 micro = $0.000001).

        Whole cents are too coarse a unit for LLM token pricing: a single
        call is often sub-cent (e.g. 1000 input tokens on gpt-4o is
        $0.0025), so rounding per call to the nearest cent would silently
        round most individual calls down to zero. Micros give six decimal
        digits of precision — realistic per-token rates land on whole or
        near-whole micros, so there is no meaningful rounding loss even
        summed across a very large log, while still being an exact integer
        (no float-accumulation drift when aggregating in `report()`).
        """
        if cost is not None:
            return round(cost * 1_000_000)

        rates = pricing if pricing is not None else self.pricing.get("models", {}).get(model)
        if rates is None:
            raise ValueError(
                f"no pricing found for model {model!r}. Pass cost=<dollars> or "
                "pricing={'input_per_1m':..., 'output_per_1m':...} to log_call(), "
                "or add this model to your pricing.json"
            )

        input_rate = rates.get("input_per_1m", 0.0)
        output_rate = rates.get("output_per_1m", 0.0)
        cached_rate = rates.get("cached_input_per_1m", input_rate)

        # rate is $ per 1M tokens => rate is also exactly micros per token,
        # so this sum is already in micros without any further scaling.
        micros = (
            input_tokens * input_rate
            + cached_input_tokens * cached_rate
            + output_tokens * output_rate
        )
        return round(micros)

    def log_call(
        self,
        *,
        label: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cost: float | None = None,
        pricing: dict | None = None,
        trace_id: str | None = None,
        **extra: Any,
    ) -> dict:
        """Log one LLM/agent call.

        `input_tokens` are tokens billed at the standard input rate;
        `cached_input_tokens` are counted separately (additively) and billed
        at the cheaper cached-input rate. `cost`, if given, is a direct
        dollar amount that skips pricing lookup entirely (e.g. for
        image/audio/embedding calls not billed per-token). `pricing`, if
        given, overrides the pricing.json lookup for this call only.
        """
        if not isinstance(label, str) or not label:
            raise ValueError("label must be a non-empty string")
        if not isinstance(model, str) or not model:
            raise ValueError("model must be a non-empty string")
        for name, value in (
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
            ("cached_input_tokens", cached_input_tokens),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative int, got {value!r}")

        cost_micros = self._resolve_cost_micros(
            model, input_tokens, output_tokens, cached_input_tokens, cost, pricing
        )

        self._check_extra_for_pii(extra)

        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "timestamp": _utc_now_iso(),
            "label": label,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cost_micros": cost_micros,
        }
        if trace_id is not None:
            record["trace_id"] = trace_id
        if extra:
            record["extra"] = extra

        self._logger.info(json.dumps(record, separators=(",", ":")))
        return record

    def log_openai_response(
        self,
        response: Any,
        *,
        label: str,
        model: str | None = None,
        trace_id: str | None = None,
        **extra: Any,
    ) -> dict:
        """Adapter for an OpenAI SDK response object (or an equivalent
        dict). Extracts usage fields (including cached-token details)
        without adding `openai` as a package dependency: fields are read
        via getattr/dict access, never by importing the SDK.
        """
        usage = _get(response, "usage")
        total_prompt_tokens = _get(usage, "prompt_tokens", 0) or 0
        output_tokens = _get(usage, "completion_tokens", 0) or 0
        details = _get(usage, "prompt_tokens_details")
        cached_tokens = _get(details, "cached_tokens", 0) or 0
        input_tokens = max(total_prompt_tokens - cached_tokens, 0)
        resolved_model = model or _get(response, "model")

        return self.log_call(
            label=label,
            model=resolved_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
            trace_id=trace_id,
            **extra,
        )

    def log_anthropic_response(
        self,
        response: Any,
        *,
        label: str,
        model: str | None = None,
        trace_id: str | None = None,
        **extra: Any,
    ) -> dict:
        """Adapter for an Anthropic SDK response object (or an equivalent
        dict). Anthropic reports `input_tokens`, `cache_creation_input_tokens`
        and `cache_read_input_tokens` as three separate additive counts (not
        subsets of one another). Only `cache_read_input_tokens` is genuinely
        cheaper, so it is passed as `cached_input_tokens`; `cache_creation_input_tokens`
        is conservatively folded into `input_tokens` at the full input rate
        (cache creation is billed at a *premium* over the base rate on the
        real API, so treating it as standard-rate rather than cached-rate
        never underestimates cost).
        """
        usage = _get(response, "usage")
        base_input_tokens = _get(usage, "input_tokens", 0) or 0
        cache_creation_tokens = _get(usage, "cache_creation_input_tokens", 0) or 0
        cache_read_tokens = _get(usage, "cache_read_input_tokens", 0) or 0
        output_tokens = _get(usage, "output_tokens", 0) or 0
        resolved_model = model or _get(response, "model")

        return self.log_call(
            label=label,
            model=resolved_model,
            input_tokens=base_input_tokens + cache_creation_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cache_read_tokens,
            trace_id=trace_id,
            **extra,
        )

    def log_gemini_response(
        self,
        response: Any,
        *,
        label: str,
        model: str | None = None,
        trace_id: str | None = None,
        **extra: Any,
    ) -> dict:
        """Adapter for a Gemini (`google-genai`) SDK response object (or an
        equivalent dict). `usage_metadata` can be entirely absent -- e.g. when
        the response was blocked by a safety filter -- `_get` handles that
        `None` case the same way it handles a missing field. Like OpenAI,
        `cached_content_token_count` is a *subset* of `prompt_token_count`
        (not an additional count), so it is subtracted out of `input_tokens`
        rather than added on top. `model` falls back to `response.model_version`
        only when the caller doesn't pass one explicitly, since not every
        client wrapper populates that field.
        """
        usage = _get(response, "usage_metadata")
        prompt_tokens = _get(usage, "prompt_token_count", 0) or 0
        cached_tokens = _get(usage, "cached_content_token_count", 0) or 0
        input_tokens = max(prompt_tokens - cached_tokens, 0)
        output_tokens = _get(usage, "candidates_token_count", 0) or 0
        resolved_model = model or _get(response, "model_version")

        return self.log_call(
            label=label,
            model=resolved_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
            trace_id=trace_id,
            **extra,
        )

    def log_ollama_response(
        self,
        response: Any,
        *,
        label: str,
        model: str | None = None,
        trace_id: str | None = None,
        **extra: Any,
    ) -> dict:
        """Adapter for an Ollama response object (or an equivalent dict).
        Ollama exposes `prompt_eval_count`/`eval_count` directly on the
        response, not nested under a `usage` object, and does not track
        cached/reused input tokens at all, so `cached_input_tokens` is always
        0.

        Two things to know before using this adapter:

        - Local models typically have no entry in `pricing.json`; pass
          `cost=0.0` (or your own `pricing=`) to `log_call`/this adapter's
          underlying cost resolution, otherwise it raises `ValueError` for an
          unpriced model -- this is expected, not a bug.
        - When calling Ollama with `stream=True`, only the final chunk (the
          one with `done: true`) carries `prompt_eval_count`/`eval_count`;
          intermediate chunks don't have them. Pass that final chunk here, not
          the intermediate ones.
        """
        input_tokens = _get(response, "prompt_eval_count", 0) or 0
        output_tokens = _get(response, "eval_count", 0) or 0
        resolved_model = model or _get(response, "model")

        return self.log_call(
            label=label,
            model=resolved_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=0,
            trace_id=trace_id,
            **extra,
        )

    def _read_records(self):
        return iter_log_records(self._read_path)

    def report(self) -> dict:
        """Return a structured cost summary read back from the log.

        Returns zeros / empty breakdowns (not an error) if the log has no
        records yet.
        """
        return build_report(self._read_records(), self.pricing)

    def total_cost(self) -> float:
        """Convenience shortcut for `report()["total_cost_usd"]`."""
        return self.report()["total_cost_usd"]


def build_report(records, pricing: dict) -> dict:
    """Aggregate an iterable of log records into the same structured cost
    summary as `CostTracker.report()`.

    Factored out as a free function so callers that only need to *read* an
    existing log (e.g. the `report`/`detect` CLI commands) can do so via
    `logreader.iter_log_records()` directly, without instantiating a
    `CostTracker` -- which would create/chmod a log file as a side effect,
    which is wrong for a read-only operation on a log that may not even
    exist yet.
    """
    call_count = 0
    total_cost_micros = 0
    by_label: dict[str, int] = {}
    by_model: dict[str, int] = {}

    for record in records:
        call_count += 1
        micros = record.get("cost_micros", 0)
        total_cost_micros += micros
        label = record.get("label", "?")
        model = record.get("model", "?")
        by_label[label] = by_label.get(label, 0) + micros
        by_model[model] = by_model.get(model, 0) + micros

    return {
        "call_count": call_count,
        "total_cost_micros": total_cost_micros,
        "total_cost_usd": total_cost_micros / 1_000_000,
        "by_label_micros": by_label,
        "by_model_micros": by_model,
        "pricing_last_updated": pricing.get("last_updated"),
    }
