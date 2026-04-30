# WebHarvest

A small system that watches structured data on the web — listings, tables, repeating cards — and tells you when it changes. Built as a course project for DevOps Principles and Practices, so the focus is the operational surface (containers, CI, security scanning, metrics) rather than research-grade extraction.

The interesting bit is the routing layer. A scrape goes through a heuristic extractor first (BeautifulSoup, JSON-LD parser, table detector). If that misses, the same HTML gets handed to one of four cloud LLMs through OpenRouter. Every call is timed, priced, and labeled by model, so the Grafana dashboard ends up being a real-world cost/latency/accuracy comparison across models on the same input.

## Reading order

| Document | What's in it |
| --- | --- |
| [README.md](./README.md) | This file. Setup, the four-model routing, DevOps stack. |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Service graph, the request flow, what each metric is for. |
| [EVALUATION.md](./EVALUATION.md) | Side-by-side: claude-sonnet-4, gpt-4o, llama-3.3-70b, gemini-2.0-flash, plus the heuristic. Real numbers from real runs. |
| [SWOT_ANALYSIS.md](./SWOT_ANALYSIS.md) | SWOT for each of the four models and each piece of the stack. |

## Architecture

```
       +-----------+      +-----------+      +-------------+
       | Dashboard | ---> |  Scraper  | -->  |  Heuristic  |  free, ~50ms
       | (React)   |      | (FastAPI) |      | (BS4 + lxml)|
       +-----------+      +-----+-----+      +------+------+
              |                 |                   |
              v                 |        confidence < 0.7
       +------+-------+         v                   |
       |   Grafana    |   +-----+-------+   +-------v---------+
       |  Prometheus  |   |   Postgres  |   |    Extracto     |
       +--------------+   +-------------+   |  (Node, OpenAI  |
                                            |  SDK -> OpenRtr)|
                                            +--------+--------+
                                                     |
                            +------------------------+------------------------+
                            |               |               |                |
                            v               v               v                v
                       claude-sonnet-4   gpt-4o    llama-3.3-70b    gemini-2.0-flash
```

| Service | Port (host) | Role |
| --- | --- | --- |
| `scraper` | — | Fetch, snapshot, orchestrate routing, diff, persist |
| `heuristic` | — | Fast/free extractor for pages with structured data |
| `extracto` | — | Cloud router. Talks OpenRouter; per-request model selection |
| `dashboard` | 3000 | React UI |
| `prometheus` | 9090 | Metrics |
| `grafana` | 3001 | The operational dashboard |
| `postgres` | — | Sources, runs, snapshots, entities |

The scraper runs the heuristic first because it's free and 50× faster than any LLM. The two cases where it fails are pages without structured data (HN, most news sites) and pages where the structure is custom enough that a generic heuristic can't anchor on it. Both fall through to the cloud, and the dashboard shows how often that happens.

## Quick start

```sh
cp .env.example .env
# edit .env, set OPENROUTER_API_KEY=sk-or-v1-...
docker compose up --build
```

To use the JSON-LD / HTML table fixtures (handy for demoing the heuristic path without paying for cloud calls):

```sh
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```

Then:
- Dashboard: http://localhost:3000
- Grafana:   http://localhost:3001 (admin/admin, or anonymous viewer)
- Prometheus: http://localhost:9090

## Adding a source

Each source picks one cloud model. The heuristic always runs first regardless. If you don't supply `model`, it falls back to `EXTRACTO_DEFAULT_MODEL`.

```sh
curl -X POST http://localhost:3000/api/sources -H 'Content-Type: application/json' -d '{
  "url": "https://news.ycombinator.com/",
  "label": "HN — claude-sonnet-4",
  "identity_key": ["title"],
  "schema": {"fields": {"title": "string"}},
  "anchor": "the list of front-page submission titles",
  "model": "anthropic/claude-sonnet-4"
}'

curl -X POST http://localhost:3000/api/sources/1/run
```

Models that have been wired up (others may work, but cost is only computed for these):

| Slug | Input $/1M | Output $/1M |
| --- | --- | --- |
| `anthropic/claude-sonnet-4` | 3.00 | 15.00 |
| `openai/gpt-4o` | 2.50 | 10.00 |
| `meta-llama/llama-3.3-70b-instruct` | 0.13 | 0.40 |
| `google/gemini-2.0-flash-001` | 0.10 | 0.40 |

(Pricing snapshot from OpenRouter, April 2026. Edit `services/extracto/src/anthropicClient.js` to update.)

## Scope cuts (vs. the original spec)

These were called out up front so the project would actually ship.

- **Distilled local model** dropped. The "local" service is a heuristic (JSON-LD, microdata, tables, repeating cards). The escalation path, the confidence threshold, and the dashboard panels still work — they just compare the heuristic to the cloud models instead of comparing one cloud model to a smaller one.
- **Wayback training pipeline** out of scope (the distilled model was its consumer).
- **Web search source discovery** out of scope. Sources are added by URL.
- **Pagination auto-detection** not implemented; pagination is recorded per source if you set it.

## DevOps surface

| Concern | Tool |
| --- | --- |
| Containers | One Dockerfile per service, multi-stage where it matters, non-root users, healthchecks |
| Orchestration | `docker-compose.yml` with healthcheck-gated dependencies |
| CI | `.github/workflows/ci.yml` — ruff + eslint + pytest + node:test, then `docker compose build` as a smoke gate |
| Image vulnerability scanning | `aquasecurity/trivy-action` against each built image and the repo filesystem; SARIF uploaded to GitHub code scanning |
| Dependency audit | `pip-audit` for the Python services, `npm audit --audit-level=high` for the Node services |
| Secret scan | `gitleaks-action` on every PR and push |
| Metrics | `prometheus_client` (Python) and `prom-client` (Node) on `/metrics` for every service |
| Visualization | Grafana auto-provisioned with the WebHarvest dashboard JSON |
| Integration test | `integration.yml` — full stack-up against fixture nginx, end-to-end create/run/re-run cycle on JSON-LD and HTML-table fixtures |
| Image publishing | `deploy.yml` — on `v*` tags, builds and pushes all four images to GHCR with SLSA provenance + SBOM |
| Dependency updates | Dependabot for actions, pip, npm, Docker base images (weekly) |

## What the dashboard surfaces

The Grafana dashboard at `:3001` is built around the multi-model comparison. The panels worth pointing at:

- **Cost by model (USD/hr)** — what each model is actually costing per source per hour
- **Confidence by backend** — heuristic, sonnet, gpt-4o, llama, gemini all overlaid
- **Escalation rate per model** — how often each source escalates, broken out by which model handled it
- **Tokens/sec by model** — input vs output token throughput
- **Extraction latency p95** — heuristic vs each cloud model, on identical inputs
- **Spend by model** — running total in USD, useful for a demo

This is the comparison the rubric calls for: same input, four cloud models plus a free fallback, every operational axis exposed as a metric.

## Local dev (without Docker)

```sh
(cd services/scraper && pip install -r requirements-dev.txt && uvicorn src.main:app --port 8080)
(cd services/extracto && npm install && npm start)
(cd services/heuristic && pip install -r requirements-dev.txt && uvicorn src.main:app --port 8082)
(cd services/dashboard && npm install && npm run dev)
```

You'll need a Postgres on `DATABASE_URL` and `OPENROUTER_API_KEY` in your environment.

## License

MIT.
