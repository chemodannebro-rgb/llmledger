# Connecting to an existing app

If you already have code calling an LLM SDK, you don't need to compute
tokens or cost yourself — `CostTracker` has an adapter per provider that
reads usage straight off the response object.

## SDK adapters

```python
# OpenAI
response = openai_client.chat.completions.create(...)
tracker.log_openai_response(response, label="chat")

# Anthropic
response = anthropic_client.messages.create(...)
tracker.log_anthropic_response(response, label="chat")

# Gemini (google-genai)
response = gemini_client.models.generate_content(...)
tracker.log_gemini_response(response, label="chat")

# Ollama — local models usually have no pricing.json entry, so pass cost=0.0
# (or your own pricing=); only pass the final chunk if you're streaming.
response = ollama_client.chat(...)
tracker.log_ollama_response(response, label="chat", cost=0.0)

# LangChain — reads AIMessage.usage_metadata (current langchain-core), or
# falls back to the older LLMResult.llm_output["token_usage"] shape.
result = chat_model.invoke(...)
tracker.log_langchain_result(result, label="chat")
```

**LiteLLM**: no separate adapter needed — `litellm.completion(...)` returns
a `ModelResponse` that normalizes every provider to the same
OpenAI-compatible shape, so `log_openai_response(response, label="chat")`
already works as-is.

None of these adapters add the provider's SDK as a dependency of
`llm-burnwatch` — they read fields off whatever response object your own
code already has, at call time.

Each adapter accounts for that provider's own cache-token billing rules
(subset vs. additive counters), so `cached_input_tokens` always means
"billed at the cheaper cached rate", regardless of provider.

### Missing or stale pricing

If the packaged `pricing.json` is missing a model or has a stale rate, pass
point overrides instead of hand-copying the whole file:

```python
tracker = CostTracker(
    "calls.jsonl",
    pricing_overrides={"my-model": {"input_per_1m": 3.0, "output_per_1m": 9.0}},
)
```

`pricing_overrides` is merged on top of the packaged defaults (everything
else stays as shipped); pass `pricing=` instead if you want to replace the
whole pricing table — the two are mutually exclusive.

You can also pull a community-maintained pricing file over the network,
explicitly and on demand:

```bash
llm-burnwatch pricing import https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
```

This is the **only** llm-burnwatch command that ever makes a network call,
and only when given an `http(s)://` URL — a local file path never touches
the network. Only import from a source you trust; see
[Security model](security.md#pricing-import--trust-boundary) for exactly
what that does and doesn't protect against.

## Already emitting OpenTelemetry GenAI traces?

If your app already emits [OpenTelemetry GenAI semantic-convention](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
spans (e.g. via OpenLLMetry or another GenAI instrumentation), you don't
need to add `CostTracker` calls at all — import the export you already
have:

```bash
llm-burnwatch import otel traces.json --log-file calls.jsonl
```

- Accepts the raw OTLP JSON export shape (`resourceSpans` → `scopeSpans` →
  `spans`), as a single JSON object, a JSON array of such objects, or JSONL
  (one object per line — what an OTel Collector's file exporter typically
  writes).
- **Local file path only** — unlike `pricing import`, this does not accept
  an `http(s)://` URL. It's a one-time batch import against an export you
  already have on disk, not a second network boundary.
- Tolerant of both attribute-naming generations the spec has had in the
  wild: current (`gen_ai.request.model`, `gen_ai.usage.input_tokens`/
  `output_tokens`) and older/OpenLLMetry-style
  (`gen_ai.usage.prompt_tokens`/`completion_tokens`).
- Tolerant of spans that carry no recognizable `gen_ai.*` attributes at
  all — a real trace export is expected to contain plenty of non-GenAI
  spans (HTTP handlers, DB calls, ...), which are silently skipped rather
  than treated as an error.
- A model missing from `pricing.json` imports at `cost_micros=0` with a
  one-time warning, rather than aborting the whole batch over one
  unrecognized model.

## End-to-end example

[`examples/e2e_actions_demo.py`](https://github.com/chemodannebro-rgb/llm-burnwatch/blob/main/examples/e2e_actions_demo.py)
wires a LangChain adapter, a monthly budget, `detect --follow`, and a
webhook sink together against a real local HTTP receiver:

```bash
python examples/e2e_actions_demo.py
```

## Log format

Each line of the log is one JSON object; the full contract (required
fields, types, optional fields like `cached_input_tokens`/`trace_id`) is
`src/llm_burnwatch/schema.json`, also available via `llm-burnwatch schema`. This is
the source of truth for any non-Python client (Node.js, Go, ...) that wants
to write a compatible log — every record also carries `schema_version` for
future format changes, plus a UTC `timestamp` (ISO 8601) of when the call
happened.

Every record needs a `label` (your own name for the call site, e.g.
`"retrieval"`/`"summarize"`) and a `model` identifier as billed, alongside
`input_tokens`/`output_tokens`/`cost_micros`. An optional free-form `extra`
object lets you attach your own metadata (e.g. `workflow_id`) without
changing the schema.

`cost_micros` is an integer (1 micro = $0.000001), not a float dollar
amount, to avoid rounding a $0.0025 call down to $0.00 and to avoid
float-accumulation drift when summing a large log.

Reasoning tokens (o1/o3-style models) aren't a separate field — bill them
into `output_tokens`, at the same rate.

## Where to go next

- Want detection to catch runaway loops/cost spikes/model swaps as they
  happen? See the [Detectors](detectors/baseline.md) pages.
- Want to stop a loop mid-flight instead of just detecting it after the
  fact? See [budget vs guard()](budget-vs-guard.md).
