# Rules detector

**Catches:** an explicit violation of a policy *you* configured — a model
that shouldn't be in use, a single call that cost more than it should
have, a whole trace (multi-step agent run) that cost more than it should
have. Unlike every other detector, this isn't a statistical judgment call;
it's "this violates a limit you set", so every alert it emits is
`severity="critical"`.

**Always available, no dependencies. Enabled by default** — but stays
completely silent unless you configure at least one rule. There's no safe
universal default for "which models are allowed" or "how much should a
call cost", so an unconfigured `RulesDetector` is a deliberate no-op, not a
detector with built-in defaults.

## The three checks

Configured via CLI flags on `detect`:

| Flag | Alert kind | What it checks |
|---|---|---|
| `--allowed-models <model> [<model> ...]` | `model_not_allowed` | Every record's `model` is in the allow-list |
| `--max-call-cost <usd>` | `call_cost_exceeded` | No single call's `cost_micros` exceeds this, per call |
| `--max-trace-cost <usd>` | `trace_cost_exceeded` | The sum of `cost_micros` across every record sharing a `trace_id` doesn't exceed this |

For `--max-trace-cost`, the alert points at the specific record whose cost
pushed the running trace total over the limit — not necessarily the single
most expensive call in the trace, but the point the cap was actually
crossed, so you can see which step broke it.

## Example

```bash
llm-burnwatch detect --log-file calls.jsonl \
  --allowed-models gpt-4o-mini claude-3-5-haiku \
  --max-call-cost 0.50 \
  --max-trace-cost 5.00
```

## Detection vs. enforcement

This detector is post-hoc: it flags a violation after the fact, in a
`report`/`detect` run over the log. If you want to stop a runaway loop
*before* it crosses a cost limit, see
[`CostTracker.guard()`](../budget-vs-guard.md) instead — the two solve
different problems and compose freely.

## Known limitations

- `--max-trace-cost` only sees records that share an explicit `trace_id` —
  calls logged without one are invisible to this check (they're still
  checked by `--allowed-models`/`--max-call-cost`, which don't need a
  `trace_id`).
- No config-file support — rules live on the `detect` command line (or
  wherever your own script constructs `RulesDetector` directly), not in a
  persisted policy file the way `budget set` is.
