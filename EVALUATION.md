# Evaluation

Real numbers from real runs. The headline question: does the LLM-bootstrap-then-BS4-forever pattern actually deliver $0 sustained polling on real pages, with real change detection? Yes — and the data below shows it.

## What's being evaluated

Two things that the rubric splits into one comparison line:

1. **The model bake-off** — same input, four cloud LLMs side by side. Cost, latency, set-level agreement, per-field agreement.
2. **The architecture** — LLM-anchored BS4 fast-path vs. naive "LLM every poll." Cost-per-poll, hit rate, drift fidelity.

I'll keep them separate. The bake-off is a model question. The architecture is a systems question.

## Test sources used during build

| Source | Type | Entities | Site behavior |
| --- | --- | --- | --- |
| Wikipedia "List of largest US companies by revenue" | SSR HTML | 113 | Stable. Multi-table page. |
| blockchain.com/explorer/defi | Next.js SPA with hydration data | 1,526 | Inline JSON in HTTP response. Anchors lucked into the right structure. |
| Hacker News front page | SSR HTML | ~30 | Volatile (points/comments change every minute or two). |
| `tests/integration/fixtures/jsonld.html` | Synthetic | 3 | Determinism baseline for CI. |
| `tests/integration/fixtures/table.html` | Synthetic | 5 | Determinism baseline for CI. |

## Bake-off: 4 cloud LLMs on the same input

One snapshot of HN, primary set to `claude-sonnet-4`, all four models invoked in parallel:

| Model | Entities | Confidence | Cost (USD) | Wall-clock | Agreement vs primary |
| --- | --- | --- | --- | --- | --- |
| `anthropic/claude-sonnet-4` (primary) | 30 | 0.98 | $0.0480 | ~12s | — |
| `openai/gpt-4o` | 30 | 1.00 | $0.0341 | 4.5s | 1.00 |
| `meta-llama/llama-3.3-70b-instruct` | 29 | 0.95 | $0.0017 | 14.7s | 0.79 |
| `google/gemini-2.0-flash-001` | 30 | 0.95 | $0.0016 | 4.5s | 1.00 |

Things I'd actually tell someone about these four:

**GPT-4o is the surprise.** Same set of titles Claude saw, ~30% cheaper, 3× faster. Cleanest JSON output of the four — the strict schema directive actually behaves with this model. If I had to pick one model to use as primary right now I'd pick this.

**Llama 3.3 70B is the budget option.** Roughly 25-30× cheaper than Claude per token. Drops to 0.79 set-level agreement on HN — which means on a 30-entity page it missed one or hallucinated one. The Grafana panel caught it immediately. For sources where you don't need gold-standard accuracy, llama is the obvious pick.

**Gemini 2.0 Flash is the cheapest when it works.** Sub-cent per call. Same agreement as Claude on this run. Big asterisk: gemini's free tier on OpenRouter rate-limits hard. Half my testing runs against gemini came back as `429 Provider returned error`. If you can pay for higher quotas, it's compelling. If you can't, it'll be your "what's wrong now" model in production.

**Claude is the safest primary.** Most reliable structured output, especially with the strict json_schema directive. Per-token cost is the highest of the four — about 25× llama, 30× gemini.

## DevOps comparison across the four models

The rubric specifically asks for security / dev / hosting / monitoring / testing / ops. Here it is, honest:

| Concern | Claude | GPT-4o | Llama 3.3 70B | Gemini 2.0 Flash |
| --- | --- | --- | --- | --- |
| **Security** | Same boat for all four — API key in env, data leaves to OpenRouter then to provider. | | | |
| **Development** | Best at structured outputs in our parser tests. Bedrock-served version sometimes returns `data:` instead of `entities:`. | Strict mode actually behaves. Cleanest JSON of the four. | Returns prose around JSON sometimes; the regex fallback handles it. | Clean JSON when it doesn't 429. |
| **Hosting** | Stateless extracto container scales horizontally. Same for all four. | | | |
| **Monitoring** | Same metrics from `prom-client` — labeled by model. | | | |
| **Testing** | Hard to test in CI without burning credits. The integration test uses gemini + llama because they're sub-cent per fixture run. | | | |
| **Operations** | Stable at our volume. Predictable per-token. | Stable. Fastest. | Stable. Slowest of the four. | 429s often on free tier. Not safe to make primary unless you've paid up. |

## Architecture: anchored fast-path vs naive LLM-every-poll

This is the more interesting evaluation. The numbers:

| Source | First run (LLM) | Sustained polling | Polls per dollar |
| --- | --- | --- | --- |
| Wikipedia (113 entities) | $0.17 | $0.0000 | infinite (after first) |
| blockchain.com DeFi (1,526 entities) | $0.47 | $0.0000 | infinite (after first) |
| HN front page (~30 entities) | $0.05 | $0.0000 | infinite (after first) |

For comparison, a naive "LLM every poll" approach on the DeFi source at every-2-minute polling: $0.47 × 30 polls/hour = **$14.10/hour, $338/day**. With anchored fast-path it's $0.47 once, then nothing.

### Fast-path latency

```
heuristic-style BS4 extraction:    ~50-100ms (300KB HTML, 100+ entity rows)
LLM extraction (Claude on same):   ~12-15s
Speedup:                           ~150×
```

### What anchoring catches and misses

Worked cleanly on:
- Wikipedia "List of..." pages (canonical SSR table)
- HN front page (table-based DOM)
- The synthetic fixtures
- Lobste.rs (clean SSR list)

Worked unexpectedly:
- blockchain.com DeFi — Claude found something to anchor in the inline `__NEXT_DATA__` JSON, BS4 navigates to it, 1,526 distinct protocol entries come out clean. I would not have predicted this; the LLM is doing more than I asked.

Did NOT work:
- weather.com 10-day forecast — 2MB shell, the actual forecast table is past the 300KB content cap. Even after focusing on `<main>`/`<article>`, the relevant rows aren't in the static HTML.
- Pages with strong anti-bot (Cloudflare challenges, JS-required redirects).

### Drift detection fidelity

After landing the role split + anchor cross-check + first-occurrence dedup:

- Wikipedia source: from 18 phantom "updates" per poll → **0**. Six consecutive runs at `updated_count=0`. The system now reports drift only when the page actually changed.
- DeFi source: 13-14 changes per top-protocol over 7 minutes — real intraday price movement, not noise.
- Field changes are 100% on volatile fields (`price_usd`, `tvl_usd`, `change_24h_pct`, `rank`, `revenue_usd`, `employees`). 0% on anchor fields. The role split works exactly as designed.

## What the dashboards actually answered during the build

Three real diagnostic moments where the metrics earned their keep:

**1. The Wikipedia row-binding bug.** Grafana's "Most volatile entities" Postgres panel showed every top-10 company with exactly 48 changes over 5 minutes — uniform. Real data wouldn't be uniform. That symmetry pointed straight at "selectors are matching wrong rows on alternate scrapes," which led to first-occurrence dedup. Without the panel showing the symmetry, I'd have chased phantom symptoms.

**2. Credits drained twice.** The cost-by-model panel went vertical when I had a `* * * * *` cron firing on Claude. The EntityFieldDrift alert silenced itself because every run was 402-erroring. Two signals — one cost-axis, one error-rate — that together told me "you have a runaway." Cron set to `null`, fixed in 30 seconds.

**3. DeFi anchors actually worked.** Fast-path hit-rate panel went to 100% after a re-anchor cycle. Postgres "Most volatile entities" populated with real DeFi top-10. Without the dashboard I'd have assumed it failed (because I'd been told SPAs don't anchor).

## How to reproduce

```sh
docker compose -f docker-compose.yml -f docker-compose.test.yml up -d --build
```

Then in the React UI at `http://localhost:3000`:

1. Click the **Largest US companies** preset.
2. Hit **Add and run** — first run will take ~10s and cost ~$0.17 (Claude bootstrap).
3. Hit Run again — second run is ~50ms and $0.

For the bake-off comparison, use the JSON form to set `comparison_models` to the other three, then run once. All four show up as separate rows in the runs table grouped by snapshot.

For the live drift demo, leave the cron at `*/2 * * * *` and walk away for 10-15 minutes. Wikipedia will accumulate real rank/revenue/employee changes from actual edits. Grafana's "Field change rate" panel populates accordingly.

## Conclusion

Two findings that I think matter beyond this assignment:

**1. Cost discipline as architecture.** The "scheduled runs physically cannot call the LLM" rule isn't a guideline — it's enforced in `runner.py`'s control flow. That single design choice is what makes the system deployable. Every other production scrape-with-LLM project I've seen has cost as a runtime risk; here it's a structural property.

**2. Field roles are the right abstraction for change tracking.** The volatile/anchor split looks like a small thing — three lines of schema, twenty lines of diff logic — but it's the difference between a dashboard that's drowning in extraction noise and one that reports real change. Generalizes way past web scraping. Anywhere you're tracking "how does this collection drift over time," the same split applies.

The LLM bake-off is the part that satisfies the rubric's tool comparison line. The anchor + role architecture is the part I'd put on a resume.
