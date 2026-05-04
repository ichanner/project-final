# WebHarvest

WebHarvest is my DevOps final project: a small web app that watches structured data on public pages and shows whether the polling system is healthy.

The user-facing flow is simple. Add a URL, describe the repeated thing on the page, build a schema, and mark fields as either stable **anchor** fields or changing **volatile** fields. The first run asks an LLM to build a DOM recipe. After that, cron jobs poll the page with conditional HTTP and use BeautifulSoup against the cached recipe. Runs, entities, anchors, HTTP validators, and field changes are all stored in Postgres.

The point of the project is not model comparison. The DevOps comparison is:

> naive full-page polling vs conditional HTTP polling with `ETag` / `Last-Modified`.

The unique piece is:

> the LLM is a one-time setup step, not a recurring polling dependency.

## What To Show In The Demo

1. Start the stack with Docker Compose.
2. Open the React dashboard.
3. Add a source with URL + schema + anchor/volatile fields.
4. Run it once so the LLM creates and stores DOM anchors.
5. Run it again or wait for cron.
6. Show that steady-state runs use either `conditional-304` or `fast-path`, not recurring LLM calls.
7. Open Grafana and show the operational dashboard.

## Services

The compose stack runs seven services:

| Service | Job |
| --- | --- |
| `postgres` | Stores sources, validators, anchors, snapshots, runs, entities, and field changes. |
| `scraper` | FastAPI app for source setup, manual runs, entities, snapshots, and metrics. |
| `worker` | APScheduler cron worker that polls sources from Postgres. |
| `extracto` | Node service that calls the selected LLM only for first-run or re-anchor. |
| `dashboard` | React/nginx UI for source setup and entity inspection. |
| `prometheus` | Scrapes metrics and evaluates alert rules. |
| `grafana` | Provisioned operational dashboard. |

## Quick Start

```sh
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```

Set `OPENROUTER_API_KEY` in `.env` before running real LLM anchoring.

Open:

- Dashboard: `http://localhost:3000`
- Grafana: `http://localhost:3001/d/webharvest`
- Prometheus: `http://localhost:9090`

## Operational Metrics

These metrics are the evidence for the dashboard and SWOT:

| Metric | Why it matters |
| --- | --- |
| `webharvest_fetch_requests_total{mode,result}` | Shows naive vs conditional polling outcomes. |
| `webharvest_fetch_bytes_total{mode}` | Shows bandwidth used by each polling strategy. |
| `webharvest_fetch_bytes_saved_total` | Shows bytes avoided by HTTP 304 responses. |
| `webharvest_extractions_skipped_total{reason="http_304"}` | Shows extraction work skipped because the page did not change. |
| `webharvest_poll_total{path}` | Shows whether a run was `conditional_skip`, `dom_fast_path`, or `llm_anchor`. |
| `webharvest_fast_path_total{outcome}` | Shows whether cached DOM anchors are still working. |
| `webharvest_cost_saved_usd_total` | Estimates avoided cost compared with calling the LLM every poll. |
| `webharvest_field_changes_total{field}` | Shows real drift on volatile fields. |
| `webharvest_fetch_errors_total{error_class}` | Breaks down network and source failures. |
| `webharvest_polls_skipped_total{reason}` | Shows cron misfires or blocked overlapping jobs. |

## Grafana Dashboard

The main dashboard is intentionally operational:

- System health: poll rate, fetch errors, cron skips, active sources.
- Polling comparison: conditional hit rate, bytes saved, skipped extractions, unsupported sources.
- Extraction efficiency: DOM fast-path hit rate, LLM spend, cost saved, anchor age.
- Entity drift: volatile field changes, entity transitions, recent runs.

## SWOT

`SWOT_ANALYSIS.md` is the only evaluation write-up. It is tied to the metrics above instead of generic tool pros and cons. The SWOT covers Docker Compose, Postgres, cron scheduling, Prometheus/Grafana, GitHub Actions/GHCR, naive polling, conditional polling, and LLM-bootstrapped DOM extraction.

## DevOps Pieces

- Docker Compose for the local environment.
- Postgres for operational state and history.
- APScheduler cron jobs in a separate worker container.
- Prometheus metrics and alert rules.
- Grafana dashboard provisioned from repo JSON.
- GitHub Actions for clean-runner checks, security scanning, and optional GHCR publishing.

That is the final story: define a source, anchor it once, poll it cheaply, persist the changes, and make the operation visible.
