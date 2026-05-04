"""Pricing helper for the cost_saved metric.

Mirrors the table in services/extracto/src/anthropicClient.js. When OpenRouter
prices change, update both files. We keep this duplicated rather than reading
extracto's table over HTTP because:

  1. The cost_saved metric is computed inside the scraper process on every
     fast-path hit (potentially thousands of times per hour); a synchronous
     HTTP call to extracto for each hit would be absurd.
  2. A small static dict is cheap to keep in sync at deploy time.

The estimate_extraction_cost function returns a deliberately ROUGH estimate of
what an LLM extraction WOULD have cost on this HTML if we'd called the LLM
instead of using cached anchors. It's an "opportunity cost saved" number, not
a precise accounting figure.
"""

from __future__ import annotations

PRICING_USD_PER_M: dict[str, dict[str, float]] = {
    "anthropic/claude-sonnet-4":         {"input": 3.00,  "output": 15.00},
    "openai/gpt-4o":                     {"input": 2.50,  "output": 10.00},
    "meta-llama/llama-3.3-70b-instruct": {"input": 0.13,  "output": 0.40},
    "google/gemini-2.0-flash-001":       {"input": 0.10,  "output": 0.40},
}

ESTIMATED_OUTPUT_TOKENS = 3500

CHARS_PER_TOKEN = 4


def estimate_extraction_cost(html_bytes: int, model: str) -> float:
    """Rough USD cost an LLM-every-poll baseline would have charged.

    Used by the cost_saved_usd_total counter on every fast-path hit — that
    counter is "money the system did not spend because anchors are cached."
    Returns 0.0 if the model isn't in the pricing table (defensive: better to
    under-report savings than over-report).
    """
    rate = PRICING_USD_PER_M.get(model)
    if not rate:
        return 0.0
    input_tokens = max(1, html_bytes // CHARS_PER_TOKEN)
    output_tokens = ESTIMATED_OUTPUT_TOKENS
    return (input_tokens * rate["input"] + output_tokens * rate["output"]) / 1_000_000
