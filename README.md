# WebHarvest

A small system for watching structured data on the web — listings, tables, repeating cards — and telling you when fields actually change. Built as a course project for DevOps Principles and Practices, so the focus is the operational surface (containers, CI/CD, security scanning, metrics, alerts) rather than research-grade extraction.

The thing that makes it interesting, in one sentence: **the LLM runs once per source to learn a CSS-selector recipe; every poll after that is BeautifulSoup against the cached recipe — sub-second, zero cost.**

The Wikipedia source in this repo polls 113 entities every minute for $0. The DeFi source polls **1,526 entities** every minute for $0. There's no caching trick or distillation — the LLM just teaches the system what to grab once, and a deterministic CSS engine takes over forever.

## Reading order

| File | What's in it |
| --- | --- |
| [README.md](./README.md) | This. Setup, architecture, DevOps stack, scope cuts. |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Service graph, request lifecycle, what each metric measures, the trade-offs. |
| [EVALUATION.md](./EVALUATION.md) | Real numbers from real runs: 4 cloud models compared, fast-path SLI, cost per source. |
| [SWOT_ANALYSIS.md](./SWOT_ANALYSIS.md) | One engineer's modified SWOT for every tool the project picked. |
| [CHANGELOG.md](./CHANGELOG.md) | What changed across 0.1, 0.2, 0.3. |

## Architecture

```
                          OpenRouter (one HTTPS endpoint, 4 models)
                          ├── anthropic/claude-sonnet-4
                          ├── openai/gpt-4o
                          ├── meta-llama/llama-3.3-70b-instruct
                          └── google/gemini-2.0-flash-001
                                       ▲
                                       │  HTTP /extract  (only on first run / re-anchor)
                                       │
   ┌──────────┐   /api  ┌──────────┐  ┌────────┐
   │ Dashboard│────────▶│ Scraper  │──│Extracto│
   │  React   │         │ FastAPI  │  │ Node   │
   └────┬─────┘         └────┬─────┘  └────────┘
        │ proxies            │
        │                    │ scheduled run from worker
        │              ┌─────┴────────┐
        │              ▼              ▼
        │       ┌──────────┐    ┌──────────┐
        │       │ Postgres │    │ Worker   │  separate container
        │       │ sources, │    │ APSched  │  reconciles cron from DB
        │       │ runs,    │    │ every 30s│
        │       │ entities,│    └──────────┘
        │       │ entity_  │
        │       │ changes  │
        │       └─────┬────┘
        │             │
        │       ┌─────▼──────┐    ┌──────────┐
        └──────▶│ Prometheus │───▶│ Grafana  │  Prom + Postgres datasources
                └────────────┘    └──────────┘
```

Six application services in compose: `scraper`, `extracto`, `worker`, `dashboard`, `postgres`, `prometheus`, `grafana`. The worker is a deliberate API/worker split — the scraper handles HTTP, the worker owns the cron schedule and polls Postgres every 30s for changes.

## Quick start

```sh
cp .env.example .env
# edit .env, set OPENROUTER_API_KEY=sk-or-v1-...
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```

Open `http://localhost:3000`. Hit a preset (HN / DeFi / Lobsters / Largest US companies), or paste any URL.

The schema builder is the front door: name your fields, pick types, mark which ones are **volatile** (the values you actually want to watch — price, score, count). Everything else is treated as anchor signal — used to identify the entity, not counted as drift.

## Adding a source

```sh
curl -X POST http://localhost:3000/api/sources -H 'Content-Type: application/json' -d '{
  "url": "https://en.wikipedia.org/wiki/List_of_largest_companies_in_the_United_States_by_revenue",
  "label": "Largest US companies",
  "anchor": "the main table of largest US companies by revenue",
  "schema": {"fields": {
    "name":         {"type": "string", "role": "anchor"},
    "industry":     {"type": "string", "role": "anchor"},
    "headquarters": {"type": "string", "role": "anchor"},
    "rank":         {"type": "string", "role": "volatile"},
    "revenue_usd":  {"type": "string", "role": "volatile"},
    "employees":    {"type": "string", "role": "volatile"}
  }},
  "primary_model": "anthropic/claude-sonnet-4",
  "comparison_models": [],
  "refresh_cron": "*/2 * * * *"
}'

curl -X POST http://localhost:3000/api/sources/1/run
```

First run calls the LLM (~$0.17 for Claude on a 160KB Wikipedia page). Every run after that uses BS4 against the cached recipe (~50ms, $0).

## Models

Per-1M-token pricing (USD). Pricing table lives in `services/extracto/src/anthropicClient.js` — update it when OpenRouter changes rates.

| Slug | Input | Output | What I actually use it for |
| --- | --- | --- | --- |
| `anthropic/claude-sonnet-4` | 3.00 | 15.00 | Default primary. Most reliable structured output of the four. |
| `openai/gpt-4o` | 2.50 | 10.00 | Fast challenger. ~30% cheaper than Claude, agreed 100% on HN. |
| `meta-llama/llama-3.3-70b-instruct` | 0.13 | 0.40 | Budget pick. ~25× cheaper, drops to ~80% agreement on harder pages. |
| `google/gemini-2.0-flash-001` | 0.10 | 0.40 | Cheapest when it works. Free-tier 429s often during the project. |

## What's actually new in 0.3.0

The big architectural pieces that landed this release:

- **LLM-bootstrapped DOM anchoring + BS4 fast-path.** First run produces a CSS-selector recipe. Every subsequent poll is BeautifulSoup. The LLM physically cannot be invoked by the scheduled cron — it's only callable when `sources.anchors IS NULL` (first run or explicit re-anchor).
- **Field roles**: every schema field is `anchor` (default) or `volatile`. Drift only counts on volatile fields. Anchor flicker (whitespace, footnote markers, BS4 binding noise) is silently absorbed.
- **Anchor cross-check**: when BS4 produces a row whose anchor field values disagree with the stored entity's anchors, the row is rejected as a wrong-bind. Caught the Wikipedia multi-table flicker that was producing 18 phantom updates per poll.
- **First-occurrence dedup**: when the root selector matches the same identity in multiple page regions (common on pages with companion tables), only the first DOM occurrence is kept. Stable across re-scrapes.
- **Worker container** owns the scheduler. Real DevOps API/worker split. The scraper API handles HTTP; the worker reconciles `sources.refresh_cron` from Postgres every 30 seconds.
- **Schema builder UI** in the React app — type a URL, add fields with name/type/volatile, click a model preset, optionally hit "add and run."
- **Per-entity history**: click any entity in the dashboard → expanded inline view with sparklines for numeric volatile fields and a full change log.

## Scope cuts (be honest)

These were called out in the original spec; some shipped, some didn't.

- **Distilled local model on llama.cpp** — replaced by a Python heuristic in 0.1, then dropped entirely in 0.2. The cloud-vs-local comparison the original proposal pitched isn't the headline anymore. The cloud-model bake-off is.
- **Wayback Machine training pipeline** — never built. Its consumer was the distilled model.
- **Web search source discovery** — never built. Sources are added by URL.
- **Pagination auto-detection** — never built. Single-page extraction only.
- **JS-rendered SPAs** — partial. The system happily anchors any page where the data is in the HTTP response. For pages that render data client-side (most modern dashboards), our `httpx.get()` returns a shell and the LLM has nothing to anchor. Two ways out, neither built: a Playwright-based fetcher for those sources, or smart detection + warning.

## DevOps surface

| Concern | Tool |
| --- | --- |
| Containers | One Dockerfile per service, multi-stage where it matters, non-root, healthchecks. |
| Orchestration | `docker-compose.yml` with healthcheck-gated dependencies. Worker container splits scheduler from API. |
| CI | `.github/workflows/ci.yml` — ruff + eslint + pytest + node:test, then `docker compose build` smoke gate. |
| Image vulnerability scan | Trivy against each built image and the repo filesystem. SARIF uploaded to GitHub code scanning. |
| Dependency audit | `pip-audit` (Python services) + `npm audit --audit-level=high` (Node services). |
| Secret scan | Gitleaks on every PR and push. |
| Metrics | `prometheus_client` (Python) + `prom-client` (Node), `/metrics` on every service. |
| Visualization | Grafana auto-provisioned with two dashboards (main + entity-history drilldown), Prom + Postgres datasources. |
| Alerting | 5 Prometheus rules: drift, model agreement, cost surge, stale spike, fast-path hit-rate floor. |
| Integration test | `integration.yml` — full stack-up against fixture nginx, end-to-end create / run / re-run. |
| Image publishing | `deploy.yml` — on `v*` tags, builds and pushes all images to GHCR with SLSA provenance + SBOM. |
| Dependency updates | Dependabot for actions, pip, npm, Docker base images (weekly). |

## What the dashboard surfaces

Two Grafana dashboards under `/grafana/`:

**WebHarvest (`/d/webharvest`)** — main board.
- Stat tiles: field changes (1h), updated entities (1h), active sources, total spend.
- **Field change rate per source × field** — the headline drift signal.
- **Top changing fields (last 1h)** — bar gauge of where churn is concentrated.
- **Most volatile entities (Postgres)** — table sortable by recent change activity, color-coded.
- **Fast-path hit rate** — per-source SLI. Healthy = 95%+. Drops mean anchors are failing and you should re-anchor.
- **Bake-off section**: cost-by-model, latency p95 by model, set-level agreement (Jaccard), per-field agreement, agreement matrix.
- Source health: fetches/min, entities-by-change-type, recent runs.

**WebHarvest — Entity history (`/d/webharvest-entity`)** — drilldown. Pick an entity, see its full timeline + numeric field history + change log.

## Local dev (without Docker)

```sh
(cd services/scraper && pip install -r requirements-dev.txt && uvicorn src.main:app --port 8080)
(cd services/scraper && python -m src.worker)
(cd services/extracto && npm install && npm start)
(cd services/dashboard && npm install && npm run dev)
```

Postgres at `DATABASE_URL`, `OPENROUTER_API_KEY` in env.

## License

MIT.
