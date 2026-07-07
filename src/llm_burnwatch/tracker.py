"""CostTracker: logs LLM/agent call cost data to a local JSONL file and
produces cost reports. Zero third-party dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
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


def user_pricing_path() -> Path:
    """Path to the user-level pricing.json written by `llm-burnwatch pricing
    import` -- `$XDG_CONFIG_HOME/llm-burnwatch/pricing.json` if set, else
    `~/.config/llm-burnwatch/pricing.json` (this follows the XDG base directory
    spec on all platforms rather than special-casing macOS/Windows, since
    the file is a plain-text config, not a native-app resource)."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "llm-burnwatch" / "pricing.json"


def user_budget_path() -> Path:
    """Path to the user-level budget.json written by `llm-burnwatch budget
    set` -- `$XDG_CONFIG_HOME/llm-burnwatch/budget.json` if set, else
    `~/.config/llm-burnwatch/budget.json`. Exact copy of `user_pricing_path`'s
    resolution logic -- same directory, same XDG rules, just a different
    filename -- so both config files coexist under one `llm-burnwatch/`
    config directory."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "llm-burnwatch" / "budget.json"


def resolve_pricing(explicit_path: str | None = None) -> dict:
    """Resolve which pricing.json to use, in priority order:

    1. `explicit_path` (e.g. the CLI's `--pricing-file`), if given.
    2. The user-level config written by `llm-burnwatch pricing import`
       (`user_pricing_path()`), if it exists.
    3. The packaged default (`load_default_pricing()`).
    """
    if explicit_path:
        with open(explicit_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    user_path = user_pricing_path()
    if user_path.exists():
        with user_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    return load_default_pricing()


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


class BudgetExceededError(Exception):
    """Raised by `CostTracker.guard()` when a `log_call()` (or one of the
    SDK-response adapters, which all call `log_call()` internally) pushes a
    guarded trace over `max_usd_per_trace` or `max_calls_per_trace`.

    This is real-time, in-process enforcement -- see `guard()`'s docstring
    for exactly what it does and does not guarantee, most importantly that
    it is **not** the same mechanism as `budget`/`BudgetDetector`
    (`detectors/budget_detector.py`), which is a cross-process, month-long,
    post-hoc analysis of the log file. `guard()` only ever sees calls made
    through *this* `CostTracker` instance, in *this* process, inside the
    `with` block that raised.

    The call that pushed the trace over its limit has already been logged
    (its record is already on disk) by the time this is raised: the real
    API call already happened and already cost money, so silently dropping
    its record would misrepresent actual spend in `report`/`detect`. This
    exception is a signal to the caller to stop making *further* calls in
    this trace, not a way to undo the one that just happened.
    """


class _GuardState:
    """Per-trace_id in-memory accounting for an active `guard()` block."""

    __slots__ = ("max_usd_per_trace", "max_calls_per_trace", "spent_micros", "call_count")

    def __init__(self, max_usd_per_trace: float | None, max_calls_per_trace: int | None) -> None:
        self.max_usd_per_trace = max_usd_per_trace
        self.max_calls_per_trace = max_calls_per_trace
        self.spent_micros = 0
        self.call_count = 0


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
        self._guards: dict[str, _GuardState] = {}

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
        logger_name = f"llm-burnwatch.tracker.{path.resolve()}"
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

    @contextmanager
    def guard(
        self,
        *,
        trace_id: str | None = None,
        max_usd_per_trace: float | None = None,
        max_calls_per_trace: int | None = None,
    ):
        """Context manager enforcing an in-process spend/call-count limit on
        every `log_call()` (and adapter) made with a matching `trace_id`
        while the block is open. Raises `BudgetExceededError` from the
        `log_call()`/adapter invocation that pushes the trace over the
        limit -- typically used to break out of a runaway agent loop.

        This is **enforcement, not detection** -- the opposite trade-off
        from `budget`/`BudgetDetector` (`detectors/budget_detector.py`,
        `llm-burnwatch budget set`): `guard()` is in-memory, per-process,
        and scoped to a single trace/`with` block, so it stops a loop the
        instant it goes over, but two processes (or two `CostTracker`
        instances) sharing a `trace_id` are invisible to each other, and it
        forgets everything the moment the `with` block exits -- it is not a
        daily/monthly budget. Use `budget`/`BudgetDetector` to know your
        month is trending over budget across every process that writes to
        the log; use `guard()` to stop one in-process loop from spending
        past a limit right now. They compose (nothing stops using both) but
        neither substitutes for the other.

        `trace_id`, if not given, defaults to a freshly generated UUID4 hex
        string, yielded to the `with` block so the caller can pass it to
        `log_call(..., trace_id=<that value>)`/the adapters -- only calls
        logged with a matching `trace_id` count against this guard; calls
        without a `trace_id`, or with a different one, are invisible to it.
        At least one of `max_usd_per_trace`/`max_calls_per_trace` must be
        given -- calling `guard()` with neither would silently enforce
        nothing, which is far more likely a caller mistake than an
        intentional no-op.
        """
        if max_usd_per_trace is None and max_calls_per_trace is None:
            raise ValueError(
                "guard() requires at least one of max_usd_per_trace or "
                "max_calls_per_trace"
            )
        resolved_trace_id = trace_id if trace_id is not None else uuid.uuid4().hex
        self._guards[resolved_trace_id] = _GuardState(max_usd_per_trace, max_calls_per_trace)
        try:
            yield resolved_trace_id
        finally:
            self._guards.pop(resolved_trace_id, None)

    def _enforce_guard(self, trace_id: str | None, cost_micros: int) -> None:
        if trace_id is None:
            return
        state = self._guards.get(trace_id)
        if state is None:
            return

        state.spent_micros += cost_micros
        state.call_count += 1

        if (
            state.max_usd_per_trace is not None
            and state.spent_micros > round(state.max_usd_per_trace * 1_000_000)
        ):
            raise BudgetExceededError(
                f"trace {trace_id!r} spent ${state.spent_micros / 1_000_000:.6f}, "
                f"exceeding max_usd_per_trace=${state.max_usd_per_trace:.2f}"
            )
        if state.max_calls_per_trace is not None and state.call_count > state.max_calls_per_trace:
            raise BudgetExceededError(
                f"trace {trace_id!r} made {state.call_count} call(s), exceeding "
                f"max_calls_per_trace={state.max_calls_per_trace}"
            )

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

        Raises `BudgetExceededError` if `trace_id` matches an active
        `guard()` block whose `max_usd_per_trace`/`max_calls_per_trace` this
        call pushes over -- the record above is still written first (see
        `BudgetExceededError`'s docstring for why).
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
        self._enforce_guard(trace_id, cost_micros)
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

    def log_langchain_result(
        self,
        result: Any,
        *,
        label: str,
        model: str | None = None,
        trace_id: str | None = None,
        **extra: Any,
    ) -> dict:
        """Adapter for a LangChain chat model result (or an equivalent
        dict). Tries two shapes, in priority order:

        1. `result.usage_metadata` -- the modern, provider-standardized
           field LangChain populates on the returned `AIMessage` for every
           major provider as of the current `langchain-core`:
           `{"input_tokens": ..., "output_tokens": ..., "input_token_details":
           {"cache_read": ...}}`. Unlike `log_openai_response`'s
           `prompt_tokens`/`completion_tokens` naming, LangChain already
           normalizes the field names themselves across providers here, not
           just the shape -- `cache_read` (if present) is a *subset* of
           `input_tokens` (same subset convention as OpenAI/Gemini), so it is
           subtracted out, not added on top.
        2. `result.llm_output["token_usage"]` -- the older `LLMResult`
           shape from the `.generate()`/`.agenerate()` API, which nests
           usage under `llm_output` and did not normalize field names: it
           carries provider-specific keys (commonly OpenAI-style
           `prompt_tokens`/`completion_tokens`, since LangChain historically
           only normalized message content there, not every provider's
           token-usage field names). No cache-token accounting is attempted
           in this fallback path.

        `usage_metadata` is tried first since it's what current LangChain
        versions populate; the `llm_output` path is only a fallback for
        callers still on the older result object.
        """
        usage_metadata = _get(result, "usage_metadata")
        if usage_metadata is not None:
            raw_input_tokens = _get(usage_metadata, "input_tokens", 0) or 0
            output_tokens = _get(usage_metadata, "output_tokens", 0) or 0
            input_details = _get(usage_metadata, "input_token_details")
            cached_tokens = _get(input_details, "cache_read", 0) or 0
            input_tokens = max(raw_input_tokens - cached_tokens, 0)
            response_metadata = _get(result, "response_metadata")
            resolved_model = (
                model
                or _get(response_metadata, "model_name")
                or _get(response_metadata, "model")
            )
        else:
            llm_output = _get(result, "llm_output")
            token_usage = _get(llm_output, "token_usage")
            input_tokens = _get(token_usage, "prompt_tokens", 0) or 0
            output_tokens = _get(token_usage, "completion_tokens", 0) or 0
            cached_tokens = 0
            resolved_model = model or _get(llm_output, "model_name")

        return self.log_call(
            label=label,
            model=resolved_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
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
