"""Shared plain-language alert rendering (B1/B2/D1/B4).

Used by both `detect`'s plain-text console output (`cli.py`'s `cmd_detect`)
and the Slack/Telegram sinks (`sinks/slack_sink.py`/`sinks/telegram_sink.py`)
-- a single source of truth so the two surfaces can't drift out of sync with
each other.

`Alert.message`/`Alert.evidence` (and therefore every `--json` payload, plus
`WebhookSink`/`ExecSink`, which both send the full alert as machine-readable
JSON on purpose) are a frozen contract -- see ARCHITECTURE.md/docs/api.md --
and are never touched by anything in this module. Everything here only ever
builds an additional, separate human-readable string from the same
`evidence` dict. `explain N` doesn't exist yet (planned for v1.1, see
CHANGELOG's v1.0 entry) so hints below only ever point at commands that
already exist today (`report --json`, `budget show`, the dashboard).
"""

from __future__ import annotations

from .anomaly.constants import FREQUENCY_WINDOW_SECONDS
from .detectors.protocol import Alert

_HUMAN_FEATURE_NAMES = {
    "input_tokens": "input size",
    "output_tokens": "response length",
    "cost_micros": "cost",
    "cached_input_tokens": "cached input size",
}

_CONSOLE_NEXT_STEP_HINTS = {
    "baseline": (
        "run `llm-burnwatch report --json` for this label/model's normal "
        "range, or open the dashboard for a visual trend"
    ),
    "cusum": (
        "run `llm-burnwatch report --json` to see whether the rise is "
        "still ongoing"
    ),
    "frequency": (
        "check your agent/orchestration code for a retry loop, or open "
        "the dashboard for call-volume trends"
    ),
    "budget": "run `llm-burnwatch budget show` for the full month-to-date breakdown",
    "rules": (
        "raise the limit with `detect --max-call-cost`/`--max-trace-cost`/"
        "`--allowed-models` if this call was expected"
    ),
}

# B2: a user thinks in incidents, not algorithms -- this maps each
# (detector, kind) pair to the plain-language incident type shown in
# `detect`'s console text (and, per B4, the Slack/Telegram one-liner),
# instead of the internal snake_case `Alert.kind` (e.g. "model_not_allowed",
# "budget_pace_warning") that's meant for `--json` consumers, not human eyes.
# `Alert.detector`/`Alert.kind` themselves are unchanged and still appear
# verbatim in every `--json` payload -- this dict only affects human-facing
# text.
#
# `("cusum", "level_shift")` -> "gradual cost increase" is a deliberate
# rewording of the term `docs/detectors/cusum.md`(+ru) already uses
# ("level shift") for the same phenomenon -- both docs pages carry an
# explicit terminology-bridge note pointing back to this label so the two
# names are never presented as if they were different things.
_INCIDENT_TYPE_LABELS = {
    ("baseline", "zscore_outlier"): "cost/usage spike",
    ("cusum", "level_shift"): "gradual cost increase",
    ("frequency", "frequency_spike"): "unusually frequent calls",
    ("rules", "model_not_allowed"): "rule violated: model not allowed",
    ("rules", "call_cost_exceeded"): "rule violated: call cost limit exceeded",
    ("rules", "trace_cost_exceeded"): "rule violated: trace cost limit exceeded",
    ("budget", "budget_exceeded"): "budget exceeded",
    ("budget", "budget_pace_warning"): "budget pace warning",
}

# B4: severity as an emoji instead of the word `[severity]`, for a chat
# surface (Slack/Telegram) where a glance matters more than a label --
# `"info"` has no emoji today because no detector currently emits an `info`-
# severity alert that reaches a sink (only baseline's `insufficient_data`,
# which `cmd_detect` never surfaces as an alert line), but the mapping
# covers it defensively so a future `info` alert still renders (blank
# prefix, not a KeyError).
SEVERITY_EMOJI = {
    "critical": "\U0001f6a8",  # rotating light
    "warning": "\u26a0\ufe0f",  # warning sign
    "info": "\u2139\ufe0f",  # information
}


def _incident_type_label(a: Alert) -> str:
    """Plain-language incident type for one alert -- falls back to the raw
    `kind` for any (detector, kind) pair not yet in the table above, so a
    new detector/kind never renders a crashing KeyError, just an
    un-translated (but still functional) label."""
    return _INCIDENT_TYPE_LABELS.get((a.detector, a.kind), a.kind)


def _human_feature_name(feature: str) -> str:
    return _HUMAN_FEATURE_NAMES.get(feature, feature)


def _format_baseline_score_for_console(score: dict) -> str:
    """One baseline (z-score) anomaly feature, in money/comparison terms
    instead of `anomaly.baseline.format_score`'s `z=`/`MAD=` notation
    (which keeps being used for `--json`'s `"reason"` field, unchanged).
    `cost_micros` is rendered as a dollar amount (money language, not raw
    micros) -- the other three features have no currency, so they're
    rendered as plain counts."""
    feature = _human_feature_name(score["feature"])
    value = score["value"]
    median = score["median"]
    if score["feature"] == "cost_micros":
        value_str = f"${value / 1_000_000:.4f}"
        median_str = f"${median / 1_000_000:.4f}"
    else:
        value_str = f"{value:g}"
        median_str = f"{median:g}"
    if score["is_extreme"]:
        return f"{feature} was {value_str} -- every previous call in this group was exactly {median_str}"
    z = score["z_score"]
    direction = "higher" if z is not None and z > 0 else "lower"
    if median:
        ratio = abs(value / median)
        return f"{feature} was {value_str} -- {ratio:.1f}x {direction} than usual (usually ~{median_str})"
    return f"{feature} was {value_str} -- unusually {direction} (usually ~{median_str})"


def _format_cusum_for_console(evidence: dict) -> str:
    feature = _human_feature_name(evidence["feature"])
    if evidence["feature"] == "cost_micros":
        reference = f"~${evidence['reference_median'] / 1_000_000:.4f}/call"
    else:
        reference = f"~{evidence['reference_median']:g}/call"
    return (
        f"{feature} has been creeping up since record "
        f"{evidence['shift_started_at_record']} and is now well above its "
        f"usual level for this group (was {reference})"
    )


def _format_frequency_for_console(evidence: dict) -> str:
    n = evidence["window_calls"]
    expected = evidence.get("expected_calls")
    window_minutes = FREQUENCY_WINDOW_SECONDS // 60
    if expected is not None:
        return (
            f"{n} calls in {window_minutes} minute(s) -- normally about "
            f"{expected:.0f} for this group"
        )
    return f"{n} calls in {window_minutes} minute(s) -- unusually high call volume"


def _render_alert_for_console(a: Alert) -> str:
    """Human-readable rendering of one non-baseline `Alert.message`.
    Budget/rules alerts already read naturally (money-first, already in the
    target style per the unified plan's B1 scope note) so they pass through
    `Alert.message` unchanged."""
    if a.detector == "cusum" and a.kind == "level_shift":
        return _format_cusum_for_console(a.evidence)
    if a.detector == "frequency" and a.kind == "frequency_spike":
        return _format_frequency_for_console(a.evidence)
    return a.message


def _oneline_detail(a: Alert) -> str:
    """The single most relevant detail line for `a`, collapsed to one line
    for a chat message (unlike `detect`'s console output, a Slack/Telegram
    message doesn't have room for a per-feature breakdown)."""
    if a.detector == "baseline" and a.kind == "zscore_outlier":
        scores = a.evidence.get("scores", [])
        if scores:
            worst = max(
                scores,
                key=lambda s: abs(s["z_score"]) if s["z_score"] is not None else 0.0,
            )
            return _format_baseline_score_for_console(worst)
        return a.message
    return _render_alert_for_console(a)


def format_alert_oneline(a: Alert) -> str:
    """One-line, chat-friendly rendering of `a` for Slack/Telegram (B4):
    severity as an emoji instead of the word `[severity]`, the plain-
    language incident type (B2) instead of raw `detector/kind`, and the
    same money-first detail `detect`'s console output already gives
    (B1/D1) -- collapsed to a single line since a chat message doesn't have
    `detect`'s multi-line sections. `Alert.message`/`evidence`/`--json` are
    unaffected; `WebhookSink`/`ExecSink` keep sending the full alert as JSON.
    """
    emoji = SEVERITY_EMOJI.get(a.severity, "")
    prefix = f"{emoji} " if emoji else ""
    label = _incident_type_label(a)
    detail = _oneline_detail(a)
    return f"{prefix}llm-burnwatch: {label} -- {detail} (record #{a.record_ref})"
