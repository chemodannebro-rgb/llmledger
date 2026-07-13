# Comparison

An honest answer to "why not just use X" — llm-burnwatch is deliberately
narrow, and the tools below are often the *better* choice for a
neighboring problem. See also [When NOT to use llm-burnwatch](https://github.com/chemodannebro-rgb/llm-burnwatch#when-not-to-use-llm-burnwatch)
in the README, which this page expands on.

## llm-burnwatch vs. Langfuse

**Use Langfuse when** you need full request/response traces or
LLM-specific evals — prompt diffing, golden datasets, human/LLM-graded
scoring. Langfuse is an observability platform built around the prompt and
completion content itself.

**Use llm-burnwatch when** you specifically don't want prompt/completion
content leaving the machine, or stored at all — llm-burnwatch only records
cost/token metadata per call (label, model, token counts, cost), never the
prompt or completion text, and never sends anything over the network
except the two explicit, opt-in exceptions documented in
[Security model](security.md). If you need both cost anomaly detection
*and* full trace evals, nothing stops you running both against the same
calls — they don't compete for the same log format or storage.

## llm-burnwatch vs. LiteLLM

**Use LiteLLM's proxy when** you need request routing — load balancing
across API keys/providers, centralized rate limiting, a unified
OpenAI-compatible endpoint in front of multiple providers. LiteLLM sits in
the request path.

**Use llm-burnwatch when** you don't want (or don't need) anything in the
request path at all — llm-burnwatch is a logging call you add after the
fact, not a proxy. If you're already using LiteLLM, note that
`litellm.completion(...)`'s `ModelResponse` already works directly with
`log_openai_response()` — no separate adapter needed — and
`pricing import` happens to reuse LiteLLM's own pricing-data *format* for
convenience. That shared format is the only connection between the two
projects; llm-burnwatch doesn't depend on LiteLLM's proxy or SDK.

## llm-burnwatch vs. Helicone

**Use Helicone when** you want a hosted gateway/proxy with a web dashboard,
request caching, and team-wide observability across API keys — it's a
managed service that sits in front of your LLM calls.

**Use llm-burnwatch when** you want the opposite trade-off: nothing hosted,
nothing in the request path, and a guarantee that your call data never
leaves the machine it's logged on (outside the explicit, opt-in
exceptions in [Security model](security.md)). The `dashboard` command
gets you a browsable summary without a server — a single static HTML file,
generated locally, openable from `file://`.

## What llm-burnwatch is not

- **Not a notification platform.** `detect --follow` ships webhook, Slack,
  Telegram, and local-command (exec) sinks, but nothing beyond that — no
  email, no paging/on-call integration. `detect`'s exit code and `--json`
  output are meant to be wired into your own cron/CI/monitoring for
  anything else.
- **Not a request-routing proxy.** It never sits between your code and the
  LLM API — every call happens exactly as your own code already makes it;
  llm-burnwatch only ever reads the response afterward.
- **Not a prompt/eval store.** No prompt or completion text is ever
  recorded, by design — see [FAQ](faq.md#does-llm-burnwatch-store-my-prompts-or-completions)
  for what that does and doesn't mean for PII.
