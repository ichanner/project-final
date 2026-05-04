# Modified SWOT

This SWOT is evidence-backed: strengths and weaknesses are tied to metrics, dashboard panels, workflow behavior, or database records from the running WebHarvest sample app.

## 1. Docker Compose Environment

**Strengths.** One command starts the whole DevOps environment: API, worker, LLM service, dashboard, Postgres, Prometheus, and Grafana. Healthcheck-gated dependencies make local startup reproducible enough for a demo.

**Weaknesses.** Compose is local orchestration, not production orchestration. It has restart policies but no rolling deploys, autoscaling, or multi-host scheduling.

**Opportunities.** Profiles could split demo/test/observability modes so the class demo starts only the services needed for the story.

**Threats.** Local volumes can hide schema changes unless migrations run. WebHarvest mitigates this with additive runtime schema checks for conditional polling columns.

**Evidence.** `docker-compose.yml`, `docker compose ps`, service healthchecks, Prometheus scrape targets.

## 2. Postgres Persistence

**Strengths.** Postgres holds the operational truth: source config, cron values, HTTP validators, cached DOM anchors, snapshots, runs, entities, and field-level change history. Grafana can query high-cardinality entity data directly through the Postgres datasource.

**Weaknesses.** `db/init.sql` only runs on first volume creation, so schema drift is a real local-dev issue without migrations.

**Opportunities.** Add a formal migration tool such as Alembic when this grows beyond a class project.

**Threats.** Large `snapshots` and `entity_changes` tables can grow quickly under high-frequency polling.

**Evidence.** `sources.etag`, `sources.last_modified`, `sources.anchors`, `runs.backend`, `entity_changes`, and the Grafana “Most volatile entities” / “Recent runs” panels.

## 3. Worker Cron Scheduling

**Strengths.** The worker container owns APScheduler jobs separately from the API container. UI/API writes `sources.refresh_cron`; the worker reconciles from Postgres every 30 seconds without restart.

**Weaknesses.** In-process scheduling is simple but not distributed. One worker process owns the schedule.

**Opportunities.** Add a worker heartbeat gauge and job count gauge for even clearer operator visibility.

**Threats.** Long-running polls can overlap cron ticks. `max_instances=1` prevents pileups but drops the overlapping tick.

**Evidence.** `webharvest_poll_total{path}`, `webharvest_polls_skipped_total{reason}`, worker `/metrics`, and the dashboard poll/cron panels.

## 4. Prometheus + Grafana

**Strengths.** Prometheus records the system over time; Grafana turns it into an operational dashboard. The panels are not decorative: they answer whether polling is healthy, efficient, cheap, and detecting drift.

**Weaknesses.** Metrics must be named and labeled carefully. Leftover model-comparison metrics made the old dashboard harder to explain.

**Opportunities.** Alertmanager or Grafana alert contact points could turn existing rules into Slack/email notifications.

**Threats.** If the worker scrape target is missing, cron-driven behavior disappears from observability even though the app is still running.

**Evidence.** `infra/prometheus/prometheus.yml`, `infra/prometheus/alerts.yml`, and dashboard panels for fetch failures, conditional hit rate, bytes saved, fast-path hit rate, cost saved, and drift.

## 5. GitHub Actions + GHCR

**Strengths.** Even for a solo project, Actions gives a clean-runner reproducibility gate: lint, tests, compose build, integration smoke test, security scans, and optional image publishing to GHCR.

**Weaknesses.** It is not evidence of team-scale CI/CD maturity; it is a submission/release verification path.

**Opportunities.** A tagged release can publish the three app images with SBOM/provenance, proving the app is packageable outside the laptop.

**Threats.** Docker builds on hosted runners are slower than local builds, and workflow caches are not shared perfectly across jobs.

**Evidence.** `.github/workflows/ci.yml`, `integration.yml`, `security.yml`, `deploy.yml`, GHCR tags, SBOM/provenance settings.

## 6. Naive HTTP Polling

**Strengths.** Works everywhere a normal GET works. It is simple, debuggable, and a reliable fallback when servers do not emit validators.

**Weaknesses.** It downloads the full body every time and runs extraction even when the source did not change.

**Opportunities.** Keep it as the fallback path for unsupported sources and for first-time validator discovery.

**Threats.** High-frequency naive polling increases bandwidth, CPU, and the chance of rate-limiting or anti-bot responses.

**Evidence.** `webharvest_fetch_requests_total{mode="naive"}`, `webharvest_fetch_bytes_total{mode="naive"}`, fetch error panels.

## 7. Conditional HTTP Polling

**Strengths.** Uses standard HTTP validators to avoid work. A `304 Not Modified` skips snapshot body storage, BS4 extraction, LLM calls, and diffing.

**Weaknesses.** It only helps when upstream servers return stable `ETag` or `Last-Modified` headers.

**Opportunities.** Use the dashboard to identify which sources are efficient enough for short cron intervals.

**Threats.** Some servers rotate weak validators or ignore conditional headers, producing low 304 hit rates despite stable visible content.

**Evidence.** `webharvest_fetch_requests_total{mode="conditional",result="not_modified|unsupported"}`, `webharvest_fetch_bytes_saved_total`, `webharvest_extractions_skipped_total{reason="http_304"}`, conditional hit-rate panel.

## 8. LLM Bootstrap + BS4 Fast Path

**Strengths.** The LLM is converted from a recurring runtime dependency into a one-time provisioning step. Once anchors exist, cron polls cannot call the LLM accidentally.

**Weaknesses.** CSS selectors can break when a page is redesigned; recovery requires manual re-anchor.

**Opportunities.** Combine CSS selectors with content-anchored matching for better resilience.

**Threats.** A bad initial anchor recipe can persist until an operator notices the fast-path miss or extraction-count drop.

**Evidence.** `webharvest_fast_path_total{outcome}`, `webharvest_anchor_age_seconds`, `webharvest_anchor_extraction_count`, `webharvest_anchor_re_anchor_total`, `webharvest_cost_saved_usd_total`.
