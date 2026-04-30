# WebHarvest

Semantic web extraction for reactive data systems. Five containerized services, GitHub Actions CI/CD with security scanning, Prometheus + Grafana monitoring.

This is a course project for DevOps Principles and Practices. The DevOps surface area (Docker, CI/CD, security scanning, observability) is the focus; the extraction logic is functional but intentionally scoped.

## Reading order

| Document                                  | What's in it                                           |
| ----------------------------------------- | ------------------------------------------------------ |
| [README.md](./README.md)                  | This file. Setup, scope, DevOps tooling overview.     |
| [ARCHITECTURE.md](./ARCHITECTURE.md)      | Service graph, request flow, CI pipeline diagram.     |
| [EVALUATION.md](./EVALUATION.md)          | Cloud vs. local extractor comparison: methodology, metrics, results. |
| [SWOT_ANALYSIS.md](./SWOT_ANALYSIS.md)    | Modified SWOT for each tool the project chose.        |

## Architecture

```
        +-----------+      +-----------+
        | Dashboard | ---> |  Scraper  | -- run/refresh -- pages
        | (React)   |      | (FastAPI) |
        +-----------+      +-----+-----+
              |                  |
              v                  v
       +------+-----+    +-------+-------+    +-------------+
       |   Grafana  |    |  Local model  |    |  Extracto   |
       | Prometheus |    |  (heuristics) |--->|  (Claude)   |
       +------------+    +---------------+    +-------------+
                            (low conf -> escalate)
                                 |
                                 v
                            +---------+
                            | Postgres|
                            +---------+
```

| Service       | Lang   | Port (host) | Role                                                |
| ------------- | ------ | ----------- | --------------------------------------------------- |
| `scraper`     | Python | -           | Fetch, snapshot, orchestrate extraction, diff, persist |
| `extracto`    | Node   | -           | Cloud LLM extractor (Claude Sonnet 4.6)             |
| `local-model` | Python | -           | Heuristic local extractor (JSON-LD, tables, cards)  |
| `dashboard`   | React  | 3000        | Monitoring UI                                       |
| `prometheus`  | -      | 9090        | Metrics collection                                  |
| `grafana`     | -      | 3001        | Dashboards (provisioned)                            |
| `postgres`    | -      | -           | Storage (entities, snapshots, runs)                 |

The scraper tries the local model first; if confidence is below `LOCAL_CONFIDENCE_THRESHOLD`, it escalates to the cloud extractor. Escalations are surfaced as a Grafana panel — the metric the spec specifically calls out.

## Quick start

```sh
cp .env.example .env
# edit .env, set ANTHROPIC_API_KEY
docker compose up --build
```

- Dashboard: http://localhost:3000
- Grafana:   http://localhost:3001 (anonymous read; admin/admin to edit)
- Prometheus: http://localhost:9090

Add a source through the dashboard or via the API:

```sh
curl -X POST http://localhost:3000/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://news.ycombinator.com/","label":"HN front page","identity_key":["title"]}'

curl -X POST http://localhost:3000/api/sources/1/run  # use the id returned by the create call above
```

## Scope simplifications vs. the original spec

These were called out up front so the project is finishable as a solo course assignment without losing the DevOps surface area.

- **Local distilled model** is replaced by a heuristic extractor (JSON-LD, microdata, table parsing, repeating-card detection) behind the same API a distilled model would expose. The escalation path, confidence scoring, and Grafana panels all still work.
- **Web search step** for source discovery is omitted. Sources are added by URL.
- **Wayback Machine training pipeline** is out of scope.
- **Pagination detection** is not auto-detected; pagination is recorded per source and could be wired up by extending the scraper.

## DevOps tooling

| Concern         | Tool / Approach                                                           |
| --------------- | ------------------------------------------------------------------------- |
| Containers      | One Dockerfile per service, multi-stage where it matters, non-root users, healthchecks |
| Orchestration   | `docker-compose.yml` with healthcheck-gated dependencies                  |
| CI              | `.github/workflows/ci.yml`: lint + test per service, then `docker compose build` |
| Vulnerability scan | `aquasecurity/trivy-action` against each built image and the repo filesystem; SARIF uploaded to GitHub code scanning |
| Dependency audit | `pip-audit` for Python services, `npm audit --audit-level=high` for Node services |
| Secret scan    | `gitleaks-action` on every PR and push                                    |
| Secrets        | `.env` (gitignored) for local; GitHub Secrets for CI                      |
| Metrics        | `prometheus_client` (Python) and `prom-client` (Node) on `/metrics`       |
| Visualization  | Grafana auto-provisioned with the WebHarvest dashboard JSON               |
| Tests          | `pytest` (scraper, local-model), `node --test` (extracto)                 |
| Lint           | `ruff` (Python), `eslint` (Node)                                          |
| Integration test | `integration.yml`: brings the full stack up with a fixture nginx, runs an end-to-end source-create / run / re-run cycle, asserts entity counts and idempotence |
| Image publishing | `deploy.yml`: on `v*` tags, builds and pushes all four images to GHCR with SLSA provenance + SBOM, then opens a GitHub Release |
| SBOM           | CycloneDX SBOMs generated by Trivy and uploaded as build artifacts        |

## Tradeoffs measured by the design

- **Cloud vs. local**: routed dynamically per page based on local confidence. Both backends emit metrics so the Grafana dashboard shows escalation rate, latency, and cost side by side — exactly the comparison the spec calls for.
- **Semantic anchoring vs. CSS selectors**: the cloud extractor is given a textual `anchor` rather than a DOM path. Resilient to redesigns; pays in latency and tokens.
- **Cost vs. accuracy**: prompt caching is enabled on the system prompt so repeated extractions amortize the prefix cost. Per-token pricing is rolled into a Prometheus counter.

## File layout

```
db/init.sql                        Postgres schema + seed
docker-compose.yml                 Service graph
infra/grafana/                     Provisioned datasource + dashboard JSON
infra/prometheus/prometheus.yml    Scrape config
services/scraper/                  Python FastAPI: fetch, orchestrate, diff
services/extracto/                 Node Express: cloud extractor (Claude)
services/local-model/              Python FastAPI: heuristic extractor
services/dashboard/                Vite + React monitoring UI
.github/workflows/ci.yml           Lint + test + compose build
.github/workflows/security.yml     Trivy + audits + secret scan
```

## Local dev (without Docker)

```sh
# In separate terminals:
(cd services/scraper && pip install -r requirements-dev.txt && uvicorn src.main:app --port 8080)
(cd services/extracto && npm install && npm start)
(cd services/local-model && pip install -r requirements-dev.txt && uvicorn src.main:app --port 8082)
(cd services/dashboard && npm install && npm run dev)
```

You'll need a local Postgres reachable at `DATABASE_URL` and `ANTHROPIC_API_KEY` in your environment.

## License

MIT.
