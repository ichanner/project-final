# WebHarvest

A small system that watches structured data on the web — listings, tables, repeating cards — and tells you when it changes. Built as a course project for DevOps Principles and Practices, so the focus is the operational surface (containers, CI, security scanning, metrics) and not so much research-grade extraction.

The interesting bit: every scrape runs through a **multi-model bake-off**. The same HTML gets handed to up to four cloud LLMs in parallel via OpenRouter — Claude Sonnet 4, GPT-4o, Llama 3.3 70B, Gemini 2.0 Flash. One of them is the "primary" (its entities are what gets persisted); the others are challengers, run on the same snapshot to measure cost, latency, and agreement against the primary. Grafana ends up being a real-world side-by-side comparison.

## Reading order

| Document | What's in it |
| --- | --- |
| [README.md](./README.md) | This. Setup, the bake-off pattern, DevOps stack. |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Service graph, request flow, what each metric is for. |
| [EVALUATION.md](./EVALUATION.md) | Side-by-side: real numbers from real runs across the four models. |
| [SWOT_ANALYSIS.md](./SWOT_ANALYSIS.md) | SWOT for each model and each piece of the stack. |
| [CHANGELOG.md](./CHANGELOG.md) | Why the design changed in 0.2.0. |

## Architecture

```
       +-----------+      +-----------+
       | Dashboard | ---> |  Scraper  | -- fetch + snapshot --> page HTML
       | (React)   |      | (FastAPI) |
       +-----------+      +-----+-----+
              |                 | one snapshot, fan-out:
              v                 v
       +------+------+   +------+--------------------+
       |   Grafana   |   |        Extracto           |
       | Prometheus  |   |  Node + OpenAI SDK ->     |
       +-------------+   |  OpenRouter (per model)   |
                         +------+--------+--------+--+
                                |        |        |
                       +--------v--+ +---v---+ +--v--------+ ...
                       | claude-4  | |gpt-4o | |llama-3.3  |
                       |  sonnet   | |       | |  70b      |
                       +-----+-----+ +---+---+ +---+-------+
                             \________|________/
                                      |
                              all results -> runs table
                              primary's entities -> entities table
                              challenger ⊆ ∩ primary -> agreement metric
```

| Service | Port (host) | Role |
| --- | --- | --- |
| `scraper` | — | Fetch, snapshot, fan out to models, diff primary, persist |
| `extracto` | — | Cloud router. One container, every OpenRouter model on demand. |
| `dashboard` | 3000 | React UI |
| `prometheus` | 9090 | Metrics |
| `grafana` | 3001 | The operational dashboard |
| `postgres` | — | Sources, runs (one row per model per snapshot), snapshots, entities |

## Quick start

```sh
cp .env.example .env
# edit .env, set OPENROUTER_API_KEY=sk-or-v1-...
docker compose up --build
```

For the JSON-LD / HTML table fixtures (no real network needed):

```sh
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```

Then:
- Dashboard: http://localhost:3000
- Grafana:   http://localhost:3001 (admin/admin, or anonymous viewer)
- Prometheus: http://localhost:9090

## Adding a source

The dashboard at `:3000` has a schema builder: paste a URL, optionally write an anchor description, add fields one at a time (name + type), pick a primary model and any challengers. The first schema field is used as the implicit identity for dedup, so you don't have to think about an `identity_key` separately.

There are three preset buttons (HN, S&P 500, Lobste.rs) that fill the form in one click. Hit **Add and run** to create the source and trigger the first bake-off in one shot.

If you'd rather scripted:

```sh
curl -X POST http://localhost:3000/api/sources -H 'Content-Type: application/json' -d '{
  "url": "https://news.ycombinator.com/",
  "label": "HN — bake-off",
  "anchor": "the list of front-page submissions with their points and authors",
  "schema": {"fields": {
    "title":    {"type": "string"},
    "points":   {"type": "number"},
    "user":     {"type": "string"},
    "comments": {"type": "number"}
  }},
  "primary_model": "anthropic/claude-sonnet-4",
  "comparison_models": [
    "openai/gpt-4o",
    "meta-llama/llama-3.3-70b-instruct",
    "google/gemini-2.0-flash-001"
  ]
}'

curl -X POST http://localhost:3000/api/sources/1/run
```

You'll get back something like:

```json
{
  "snapshot_id": 1,
  "primary_model": "anthropic/claude-sonnet-4",
  "primary": { "entity_count": 30, "new": 30, "cost_usd": 0.048, "confidence": 0.98 },
  "challengers": [
    { "model": "openai/gpt-4o",                     "entity_count": 30, "cost_usd": 0.034, "agreement": 1.00, "duration_s": 4.5 },
    { "model": "meta-llama/llama-3.3-70b-instruct", "entity_count": 29, "cost_usd": 0.002, "agreement": 0.79, "duration_s": 15.0 },
    { "model": "google/gemini-2.0-flash-001",       "entity_count": 30, "cost_usd": 0.002, "agreement": 1.00, "duration_s": 4.5 }
  ]
}
```

That object is the comparison.

## Models

Per-1M-token pricing (USD), used for cost computation in metrics. Snapshot from OpenRouter, April 2026 — edit `services/extracto/src/anthropicClient.js` to update.

| Slug | Input | Output | Notes |
| --- | --- | --- | --- |
| `anthropic/claude-sonnet-4` | 3.00 | 15.00 | Workhorse. Most reliable structured output. |
| `openai/gpt-4o` | 2.50 | 10.00 | Fastest in our tests; usually agrees with Claude. |
| `meta-llama/llama-3.3-70b-instruct` | 0.13 | 0.40 | 25× cheaper, 80% agreement. Good for cost-sensitive sources. |
| `google/gemini-2.0-flash-001` | 0.10 | 0.40 | Fast and cheap, but rate-limited aggressively on free tier (429s common). |

## Scope cuts vs. the original spec

- **Heuristic / distilled local model** — dropped entirely in 0.2.0. The original proposal had a llama.cpp-based distilled model trained on Wayback snapshots; it was scoped down to a Python heuristic in 0.1.0, and then dropped entirely once we moved to a cloud-vs-cloud comparison. The proposal's "GPT-4o vs Claude" comparison is now the headline.
- **Web search source discovery** — out of scope. Sources are added by URL.
- **Wayback Machine training pipeline** — out of scope (its consumer was the distilled model).
- **Pagination auto-detection** — not implemented; recorded per-source if you set it.

## DevOps surface

| Concern | Tool |
| --- | --- |
| Containers | One Dockerfile per service, multi-stage where it matters, non-root users, healthchecks |
| Orchestration | `docker-compose.yml` with healthcheck-gated dependencies |
| CI | `.github/workflows/ci.yml` — ruff + eslint + pytest + node:test, then `docker compose build` smoke gate |
| Image vulnerability scan | `aquasecurity/trivy-action` against each built image and the repo filesystem; SARIF uploaded to GitHub code scanning |
| Dependency audit | `pip-audit` (Python), `npm audit --audit-level=high` (Node) |
| Secret scan | `gitleaks-action` on every PR and push |
| Metrics | `prometheus_client` (Python) and `prom-client` (Node) on `/metrics` |
| Visualization | Grafana auto-provisioned with the WebHarvest dashboard |
| Integration test | `integration.yml` — full stack-up, end-to-end create/run/re-run on JSON-LD and HTML-table fixtures with a 2-model bake-off |
| Image publishing | `deploy.yml` — on `v*` tags, builds and pushes all three images to GHCR with SLSA provenance + SBOM |
| Dependency updates | Dependabot for actions, pip, npm, Docker base images (weekly) |

## What the dashboard surfaces

The Grafana board at `:3001` is the comparison panel. The flagship is **Field agreement matrix** — a colored grid of (challenger × field) cells showing, for the entities both models saw, how often they actually agreed on each field's value. That's where the cheap models earn or lose your trust.

Other panels:

- **Cost by model (USD/hr)** and **Spend by model** — what each model is costing you, per hour and total
- **Set-level agreement (Jaccard, vs primary)** — did the challenger see the same set of entities as the primary?
- **Per-field agreement (vs primary)** — time-series view of the same numbers in the matrix
- **Confidence by backend (p50/p95)** — model-reported confidence, distinct per model
- **Extraction latency p95 by model** — speed comparison under identical input
- **Tokens/sec by model** — throughput, input vs output
- **Escalation rate (by model)** — challenger call counts
- **Entities by change type** — new/updated/stale rates from the primary's diffs
- **Fetches/min by outcome** — fetch-side success rate per source

## Local dev (without Docker)

```sh
(cd services/scraper && pip install -r requirements-dev.txt && uvicorn src.main:app --port 8080)
(cd services/extracto && npm install && npm start)
(cd services/dashboard && npm install && npm run dev)
```

You'll need a Postgres reachable on `DATABASE_URL` and `OPENROUTER_API_KEY` in your environment.

## License

MIT.
