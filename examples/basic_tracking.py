"""Minimal CostTracker usage: no anomaly detection, no scikit-learn.

Run with only the core package installed (`pip install llmledger`, no
extras) -- this is the "zero-dependency core" guarantee in action.

    python examples/basic_tracking.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from llmledger.tracker import CostTracker


def main() -> None:
    log_dir = Path(tempfile.mkdtemp(prefix="llmledger-basic-"))
    log_file = log_dir / "calls.jsonl"
    tracker = CostTracker(log_file)

    # A plain log_call(): you already have token counts from wherever your
    # LLM client puts them, and pricing.json already knows this model.
    tracker.log_call(
        label="summarize",
        model="gpt-4o-mini",
        input_tokens=812,
        output_tokens=143,
    )

    # A call with prompt-cache reuse: cached_input_tokens are billed at the
    # cheaper cached rate instead of the standard input rate.
    tracker.log_call(
        label="chat",
        model="claude-sonnet-4",
        input_tokens=250,
        output_tokens=180,
        cached_input_tokens=4_000,
    )

    # A call billed some other way entirely (e.g. a flat-rate image/audio
    # call) -- pass cost= directly and skip the pricing.json lookup.
    tracker.log_call(
        label="thumbnail",
        model="some-image-model",
        input_tokens=0,
        output_tokens=0,
        cost=0.008,
    )

    # If you're calling the OpenAI/Anthropic/Gemini/Ollama SDKs directly, the
    # matching log_*_response() adapter extracts usage (including cache
    # fields, where the provider has them) straight from the SDK's response
    # object -- no need to add openai/anthropic/google-genai/ollama as a
    # dependency of llmledger, or to hand-map fields yourself:
    #
    #   response = openai_client.chat.completions.create(...)
    #   tracker.log_openai_response(response, label="chat")
    #
    #   response = anthropic_client.messages.create(...)
    #   tracker.log_anthropic_response(response, label="chat")
    #
    #   response = gemini_client.models.generate_content(...)
    #   tracker.log_gemini_response(response, label="chat")
    #
    #   response = ollama_client.chat(...)  # pass the final chunk if streaming
    #   tracker.log_ollama_response(response, label="chat", cost=0.0)  # local model: no pricing.json entry

    report = tracker.report()
    print(f"log file: {log_file}")
    print(f"calls logged: {report['call_count']}")
    print(f"total cost: ${report['total_cost_usd']:.6f}")
    print("by label:")
    for label, micros in sorted(report["by_label_micros"].items()):
        print(f"  {label}: ${micros / 1_000_000:.6f}")


if __name__ == "__main__":
    main()
