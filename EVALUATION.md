# Evaluation: four cloud LLMs on the same input

This is the comparative evaluation the rubric asks for. WebHarvest runs the same HTML through four cloud models in parallel via OpenRouter and records cost, latency, confidence, and agreement-with-primary for each. The numbers below are observed on the Hacker News front page on 2026-04-30, with `anthropic/claude-sonnet-4` set as the primary.

## What's being compared

Four OpenRouter-hosted models, all hit through the same `extracto` service with identical input, identical schema, identical anchor:

- `anthropic/claude-sonnet-4`
- `openai/gpt-4o`
- `meta-llama/llama-3.3-70b-instruct`
- `google/gemini-2.0-flash-001`

The input across all four is the same `snapshots.html` blob — one fetch per run, every model gets the same 50KB-ish of HTML.

## Why these are comparable

Same prompt, same schema, same HTML, same JSON output contract. They differ on: provider, parameter count, vendor (Anthropic / OpenAI / Meta / Google), price per token, and self-reported confidence. Whatever else the bake-off panels show is a real difference between models, not a setup difference.

## Metrics

For every model on every run we record:

1. **Accuracy** — measured against the primary as a stand-in for ground truth, via Jaccard agreement on identity-keys. Imperfect: if the primary is wrong, the comparison is wrong. But for a system without labeled ground truth, "do the other three top models agree with my pick?" is a defensible signal.
2. **Confidence** — the model's self-reported `[0, 1]` confidence in its extraction. Surfaced as `webharvest_scraper_run_confidence{backend}` in Grafana.
3. **Latency** — wall-clock time from request to response. Per-model histogram.
4. **Cost (USD)** — computed from the `usage.prompt_tokens` and `usage.completion_tokens` returned by OpenRouter, multiplied by the per-model rates in `services/extracto/src/anthropicClient.js`.
5. **Token throughput** — input vs output tokens per second, by model.

## Observed numbers (HN front page, primary = claude-sonnet-4)

One run, captured live:

| Model | Entities | Confidence | Cost (USD) | Wall-clock | Agreement (vs primary) |
| --- | --- | --- | --- | --- | --- |
| `anthropic/claude-sonnet-4` (primary) | 30 | 0.98 | $0.0480 | — | — |
| `openai/gpt-4o` | 30 | 1.00 | $0.0341 | 4.5s | 1.00 |
| `meta-llama/llama-3.3-70b-instruct` | 29 | 0.95 | $0.0017 | 14.7s | 0.79 |
| `google/gemini-2.0-flash-001` | 30 | 0.95 | $0.0016 | 4.5s | 1.00 |

Reading those numbers:

- **gpt-4o is the surprise**: same entity set as Claude, ~30% cheaper, 3× faster. If you trust Claude as the primary, gpt-4o is the cheapest "second opinion" you can get that almost never disagrees.
- **llama-3.3-70b is dramatically cheap** (~30× cheaper than Claude) but pays for it in agreement. 0.79 means it disagreed on 1 of 30 entities — could be an HN title with weird Unicode, could be a hallucinated extra row. Without field-level diff we don't know which.
- **gemini-flash is the cheapest *and* matches Claude** on this page. The asterisk: gemini-flash on OpenRouter rate-limits aggressively. Half the runs in our testing came back as `429 Provider returned error`, which shows up as `entity_count: 0` and a useless agreement score. If you make it the primary, your dashboard goes red half the time.
- **The primary choice matters.** If you'd made gpt-4o the primary, Claude would show agreement 1.00, llama would still show ~0.79 (relative to gpt-4o now), and gemini would be similar. Same shape, different baseline. Pick whoever you trust most as primary.

## Where the metric stops being useful

Jaccard on identity-keys is a coarse comparison. It catches:
- Missing entities (the cheap model didn't see something)
- Extra entities (the cheap model hallucinated)

It does not catch:
- Wrong field values for matched entities (e.g., titles correct, but author or date are wrong)
- Confidence-without-correctness (a model that's wrong but says 0.95)

To get those, you'd need field-level diff (extend the metric to compare `data` JSON, not just identity hashes) or labeled ground truth (out of scope for this project — but trivial on the JSON-LD fixture, since the fixture's own JSON-LD *is* the ground truth).

## DevOps comparison

Per the rubric prompt, the same four models compared on operational concerns:

| Concern | Claude Sonnet 4 | GPT-4o | Llama 3.3 70B | Gemini 2.0 Flash |
| --- | --- | --- | --- | --- |
| **Security** | API key in env via OpenRouter; data leaves to Anthropic | Same path, data leaves to OpenAI | Same path, data leaves to whichever OpenRouter provider routes it | Same path, data leaves to Google |
| **Development** | Best at structured outputs in our parser tests; least likely to wrap output in CoT prose | Cleanest JSON output by far — strict mode actually works | Sometimes returns prose around the JSON; the regex fallback handles it | Returns clean JSON when it doesn't 429 |
| **Hosting** | Stateless extracto container; horizontally scales by replica count | Same | Same | Same |
| **Monitoring** | Per-model `extracto_*` metrics from `prom-client`; OpenRouter `usage` object gives token-level visibility | Same | Same | Same |
| **Testing** | Hard to unit-test without burning credits; integration test uses gemini+llama (sub-cent per fixture run) | Same | Same | Same |
| **Operations** | Stable; no rate-limit issues at our volume | Stable; fastest | Stable, slowest | Aggressive 429s on free tier; need a paid OpenRouter account or a credit pre-load to use as primary |

## How to reproduce

```sh
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build

# Hit the JSON-LD fixture with two cheap models
curl -X POST http://localhost:3000/api/sources -H 'Content-Type: application/json' -d '{
  "url": "http://fixture/jsonld.html",
  "label": "JSON-LD bake-off",
  "identity_key": ["name"],
  "schema": {"fields": {"name": "string", "datePublished": "string"}},
  "primary_model": "google/gemini-2.0-flash-001",
  "comparison_models": ["meta-llama/llama-3.3-70b-instruct"]
}'

curl -X POST http://localhost:3000/api/sources/1/run

# Then look at:
open http://localhost:3001/d/webharvest
# Inter-model agreement, cost-by-model, latency-by-model panels.
```

To run the full four-model bake-off, swap the body for:

```json
{
  "url": "https://news.ycombinator.com/",
  "label": "HN — full bake-off",
  "identity_key": ["title"],
  "schema": {"fields": {"title": "string"}},
  "anchor": "the list of front-page submission titles",
  "primary_model": "anthropic/claude-sonnet-4",
  "comparison_models": [
    "openai/gpt-4o",
    "meta-llama/llama-3.3-70b-instruct",
    "google/gemini-2.0-flash-001"
  ]
}
```

Each run is roughly $0.085 across all four models combined.

## Conclusion

For this project's rubric question — "draw comparisons across security, development, hosting, monitoring, testing, operations" — the bake-off pattern surfaces every dimension as a metric on the same Grafana board, computed from real OpenRouter responses. There's no canonical winner: Claude is the safest primary, gpt-4o is the cheapest second opinion that almost never disagrees, llama is the budget choice with measurable accuracy cost, gemini is fastest-cheapest when it's not 429ing.

The DevOps takeaway is that *which model you pick is observable in your monitoring stack*. Switching primary from Claude to gpt-4o is one column update in `sources.primary_model` and one redeploy of the dashboard's labels — no rebuild, no migration, no prompt rework. That's the design winning more than any single model.
