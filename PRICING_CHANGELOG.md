# Pricing changelog

`src/llm_burnwatch/pricing.json` carries a single `last_updated` date but no
record of *what* changed since the previous snapshot. Re-running `report`
or `dashboard` on the same log after a `pricing.json` update can silently
change historical totals, with no way to tell why just from the log or the
tool's output. This file is the append-only record of what changed and
when, so a `total_cost_usd` that shifts between two runs on an unchanged
log can always be traced back to a specific rate change here.

**Rule:** any commit that edits `src/llm_burnwatch/pricing.json` (a new model,
a changed rate, a removed model) must also update `last_updated` in that
file and add a new dated entry below in the same commit/PR.

## 2026-06-01 — initial snapshot

Baseline rates as of the `llm-burnwatch` v0.1.0 initial release (no prior
history exists before this point).

| Model | Input ($/1M) | Output ($/1M) | Cached input ($/1M) |
|---|---|---|---|
| `gpt-4o` | 2.50 | 10.00 | 1.25 |
| `gpt-4o-mini` | 0.15 | 0.60 | 0.075 |
| `o1` | 15.00 | 60.00 | 7.50 |
| `o3-mini` | 1.10 | 4.40 | 0.55 |
| `claude-sonnet-4` | 3.00 | 15.00 | 0.30 |
| `claude-opus-4` | 15.00 | 75.00 | 1.50 |
| `claude-haiku-3.5` | 0.80 | 4.00 | 0.08 |
