# WebHarvest

WebHarvest is a small DevOps-flavored web app that watches structured data on public pages and surfaces an operational dashboard for whether the polling system is healthy.

The unique architectural angle is one sentence:

> the LLM is a one-time setup step, not a recurring polling dependency.

A user adds a URL, describes the repeated thing on the page, and marks fields as either stable **anchor** fields or changing **volatile** fields. The first run asks an LLM to derive a CSS-selector recipe. Every subsequent poll is BeautifulSoup against that cached recipe — no LLM call. The LLM's *output* (a tiny JSON recipe) is what gets cached, not the model itself.

## The Five Pieces

This project is built around five concrete deliverables, each one demonstrable in the running stack:

### 1. Environment

- **Docker Compose** brings the whole stack up with one command. Services: `postgres`, `scraper`, `worker`, `extracto`, `dashboard`, `prometheus`, `grafana`.
- **GitHub Actions** workflows (`ci.yml`, `integration.yml`, `security.yml`, `deploy.yml`) run lint + tests + compose build + integration smoke + security scans on every push, and publish images to GHCR on tagged release.
- **Security scanning** in CI: Trivy for image CVEs, pip-audit for Python deps, npm audit for the React UI, gitleaks for accidental secrets in commits. Dependabot opens dep-bump PRs weekly.

### 2. Sample app

- **React UI** (`http://localhost:3000`) for adding a source: paste a URL, write a one-line semantic anchor, build a schema, mark fields anchor vs volatile, pick a cron, hit Add+Run.
- **First run** calls the chosen LLM (default `google/gemini-2.0-flash-001`) which returns a CSS-selector recipe + the first batch of entities. The recipe is persisted on the source row.
- **Every later run** applies the recipe via BeautifulSoup in ~50-100ms, diffs against stored entities, writes per-field changes to Postgres. No LLM call. The fast-path hit rate is the operational SLI for the pattern.

### 3. Operational dashboard

Grafana at `http://localhost:3001/d/webharvest` is laid out as a true ops dashboard, not a thesis-narrative dashboard. Six rows, each answering one operator question:

| Row | Operator question |
| --- | --- |
| 1. Status | Is anything red right now? (services up, active alerts, active sources) |
| 2. Rate | Is the system doing work? (polls/min global + per source) |
| 3. Errors | What's failing? (fetch errors by class, consecutive failures, cron skips) |
| 4. Duration | Is anything slow? (poll p50/p95, fast-path latency) |
| 5. Activity | Is useful output being produced? (field changes, entity churn, entity counts) |
| 6. Diagnostic | Drill here when something's wrong (firing alerts table + Postgres failure tail + recent runs) |

Six Prometheus alerts back the dashboard: `ServiceDown`, `ConsecutiveFetchFailures`, `AntiBotDetected`, `PollSilent`, `AnchorBreakage`, `StaleEntitySpike`. Each carries `summary` + `description` + `remediation` annotations so an operator can act without leaving Grafana.

### 4. SWOT

`SWOT_ANALYSIS.md` is the project's evaluation write-up. Each tool used in the stack gets an evidence-backed SWOT (Docker Compose, Postgres, APScheduler in the worker container, Prometheus + Grafana, GitHub Actions / GHCR, the LLM bootstrap + BS4 fast-path pattern). Strengths and weaknesses are tied to specific metrics, panels, workflow runs, or DB rows from the actual running system.

### 5. Unique angle: LLM-as-one-time-setup

The architectural thing nobody else in the class is doing: turn a recurring runtime dependency on an LLM into a one-time provisioning step. Once the recipe is cached, every subsequent poll is deterministic Python against deterministic CSS selectors. The cost graph for a single source over time looks like a single spike at first-run, then a flat line at zero. The tradeoff is honest — selectors break when pages redesign, and the system surfaces this via `webharvest_anchor_extraction_count` dropping below its 1h moving average (the `AnchorBreakage` alert).

## Services

| Service | Job |
| --- | --- |
| `postgres` | Stores sources, anchor recipes, snapshots, runs, entities, and per-field change history. |
| `scraper` | FastAPI app for source CRUD, manual runs, /metrics. |
| `worker` | APScheduler container that fires per-source cron jobs. Same image as scraper, different entrypoint. |
| `extracto` | Node service that wraps OpenRouter for the one-time anchoring LLM call. |
| `dashboard` | React/nginx UI for source setup and entity inspection. |
| `prometheus` | Scrapes metrics every 15s and evaluates the alert rules. |
| `grafana` | Provisioned operational dashboard at `/d/webharvest`. |

## Quick Start

```sh
cp .env.example .env       # set OPENROUTER_API_KEY
docker compose up --build
```

Open:

- Dashboard: <http://localhost:3000>
- Grafana: <http://localhost:3001/d/webharvest> (anonymous viewer enabled)
- Prometheus: <http://localhost:9090>

## Demo flow

1. `docker compose up --build` — the whole stack comes up healthy.
2. Open the React dashboard, click a preset (DeFi / Steam / Lobsters), hit **Add + run**.
3. First run takes a few seconds — the LLM is generating the anchor recipe and bootstrapping entities. Watch `extracto` logs.
4. Subsequent cron polls (every minute for DeFi/Steam) take ~100ms each — pure BS4 on cached selectors. Watch `webharvest_poll_total{path="dom_fast_path"}` climb in Prometheus.
5. Open Grafana. Wait 10-20 minutes. Field-changes panels populate as DeFi prices drift, entity-count panels show source churn, the diagnostic row stays empty (no alerts firing).
6. (Optional) Manually re-anchor a source to demonstrate the cost spike: `POST /sources/{id}/re-anchor`. The `webharvest_scraper_cost_usd_total` counter ticks up, then goes flat again.

## Operational metrics (the ones that drive the dashboard)

| Metric | Drives |
| --- | --- |
| `up{job=...}` | Service health row (Prometheus built-in) |
| `webharvest_poll_total{source_id, path}` | Rate row + Polls-by-path activity panel |
| `webharvest_poll_duration_seconds_bucket` | Duration row p50/p95 |
| `webharvest_fetch_errors_total{error_class}` | Errors row + AntiBotDetected alert |
| `webharvest_fetch_consecutive_failures` | Per-source bargauge + ConsecutiveFetchFailures alert |
| `webharvest_polls_skipped_total{reason}` | Cron-skips stat |
| `webharvest_fast_path_total{outcome}` + `webharvest_fast_path_duration_seconds_bucket` | Fast-path SLI + duration |
| `webharvest_anchor_extraction_count` | Entity-count anomaly panel + AnchorBreakage alert |
| `webharvest_field_changes_total{source_id, field}` | Activity-proof drift panel |
| `webharvest_scraper_run_entities_total{change}` | Entity transitions panel + StaleEntitySpike alert |
