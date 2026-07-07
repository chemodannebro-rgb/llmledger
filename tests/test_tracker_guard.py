from __future__ import annotations

import pytest

from llm_burnwatch.tracker import BudgetExceededError, CostTracker


def test_guard_requires_at_least_one_limit(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with pytest.raises(ValueError, match="at least one of"):
        with tracker.guard():
            pass


def test_guard_yields_a_generated_trace_id_when_none_given(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(max_calls_per_trace=5) as trace_id:
        assert isinstance(trace_id, str) and trace_id


def test_guard_yields_back_an_explicit_trace_id(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(trace_id="my-trace", max_calls_per_trace=5) as trace_id:
        assert trace_id == "my-trace"


def test_calls_within_usd_limit_do_not_raise(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(max_usd_per_trace=1.0) as trace_id:
        # gpt-4o pricing: well under $1 for these small calls.
        tracker.log_call(
            label="x", model="gpt-4o", input_tokens=100, output_tokens=50, trace_id=trace_id
        )
        tracker.log_call(
            label="x", model="gpt-4o", input_tokens=100, output_tokens=50, trace_id=trace_id
        )


def test_call_exceeding_usd_limit_raises_budget_exceeded_error(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(max_usd_per_trace=1.0) as trace_id:
        tracker.log_call(
            label="x", model="x", input_tokens=0, output_tokens=0, cost=0.60, trace_id=trace_id
        )
        with pytest.raises(BudgetExceededError, match="max_usd_per_trace"):
            tracker.log_call(
                label="x",
                model="x",
                input_tokens=0,
                output_tokens=0,
                cost=0.60,
                trace_id=trace_id,
            )


def test_call_exactly_at_usd_limit_does_not_raise(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(max_usd_per_trace=1.0) as trace_id:
        tracker.log_call(
            label="x", model="x", input_tokens=0, output_tokens=0, cost=1.0, trace_id=trace_id
        )


def test_calls_within_call_count_limit_do_not_raise(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(max_calls_per_trace=3) as trace_id:
        for _ in range(3):
            tracker.log_call(
                label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id=trace_id
            )


def test_call_exceeding_call_count_limit_raises_budget_exceeded_error(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(max_calls_per_trace=3) as trace_id:
        for _ in range(3):
            tracker.log_call(
                label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id=trace_id
            )
        with pytest.raises(BudgetExceededError, match="max_calls_per_trace"):
            tracker.log_call(
                label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id=trace_id
            )


def test_call_that_exceeds_the_limit_is_still_logged(tmp_path):
    # The call itself already happened (and cost money) in the real world by
    # the time log_call() is invoked -- BudgetExceededError must not hide it
    # from the log.
    path = tmp_path / "calls.jsonl"
    tracker = CostTracker(path)
    with tracker.guard(max_calls_per_trace=1) as trace_id:
        tracker.log_call(
            label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id=trace_id
        )
        with pytest.raises(BudgetExceededError):
            tracker.log_call(
                label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id=trace_id
            )

    report = tracker.report()
    assert report["call_count"] == 2


def test_calls_without_a_matching_trace_id_are_invisible_to_the_guard(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(trace_id="guarded", max_calls_per_trace=1):
        # No trace_id passed -> not counted against the "guarded" limit.
        tracker.log_call(label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0)
        tracker.log_call(label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0)
        # A different trace_id -> also not counted against "guarded".
        tracker.log_call(
            label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id="other"
        )


def test_guard_state_is_isolated_per_trace_id(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(trace_id="task-a", max_usd_per_trace=1.0) as trace_a:
        with tracker.guard(trace_id="task-b", max_usd_per_trace=1.0) as trace_b:
            tracker.log_call(
                label="x", model="x", input_tokens=0, output_tokens=0, cost=0.9, trace_id=trace_a
            )
            # task-b's own spend is nowhere near its limit -- task-a's usage
            # must not leak into task-b's accounting.
            tracker.log_call(
                label="x", model="x", input_tokens=0, output_tokens=0, cost=0.1, trace_id=trace_b
            )


def test_guard_state_is_cleared_on_block_exit(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with tracker.guard(trace_id="task-a", max_usd_per_trace=1.0) as trace_id:
        tracker.log_call(
            label="x", model="x", input_tokens=0, output_tokens=0, cost=0.9, trace_id=trace_id
        )

    # Guard block has exited -- the same trace_id used outside of any guard
    # block is no longer tracked/enforced at all.
    tracker.log_call(
        label="x", model="x", input_tokens=0, output_tokens=0, cost=5.0, trace_id="task-a"
    )


def test_guard_state_is_cleared_even_when_block_raises(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    with pytest.raises(BudgetExceededError):
        with tracker.guard(trace_id="task-a", max_calls_per_trace=1) as trace_id:
            tracker.log_call(
                label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id=trace_id
            )
            tracker.log_call(
                label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id=trace_id
            )

    # Re-entering a fresh guard() with the same trace_id starts a clean count.
    with tracker.guard(trace_id="task-a", max_calls_per_trace=1) as trace_id:
        tracker.log_call(
            label="x", model="x", input_tokens=0, output_tokens=0, cost=0.0, trace_id=trace_id
        )


def test_adapters_are_enforced_by_guard_too(tmp_path):
    tracker = CostTracker(tmp_path / "calls.jsonl")
    response = {"model": "x", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    with tracker.guard(max_calls_per_trace=1) as trace_id:
        tracker.log_openai_response(response, label="chat", cost=0.0, trace_id=trace_id)
        with pytest.raises(BudgetExceededError):
            tracker.log_openai_response(response, label="chat", cost=0.0, trace_id=trace_id)
