# SWOT Analysis

This SWOT is evidence-backed: every strength and weakness is tied to a specific metric, dashboard panel, workflow behavior, or database record from the running WebHarvest stack.

## 1. Docker Compose Environment

**Strengths.** One command starts the whole DevOps environment: API, worker, LLM service, dashboard, Postgres, Prometheus, and Grafana. Healthcheck-gated dependencies (`postgres: { condition: service_healthy }`) make local startup reproducible enough for a class demo. Splitting the API and the cron worker into separate containers off the same image is a clean API/worker pattern that mirrors what teams do at production scale.

**Weaknesses.** Compose is local orchestration, not production orchestration. It has restart policies but no rolling deploys, autoscaling, or multi-host scheduling. There is no service mesh, no secrets vault, and no per-container resource limits set.

**Opportunities.** Compose profiles could split demo / test / observability modes so the class demo starts only the services needed for the storyline. Adding `docker-compose.prod.yml` overlay with resource limits would model a real promotion path.

**Threats.** Local Postgres volumes can hide schema changes unless migrations run. WebHarvest mitigates this with an `ensure_runtime_schema()` hook for additive migrations, but that only papers over the deeper problem that there is no real migration tool in play.

**Evidence.** `docker-compose.yml`, `docker compose ps` output, service healthchecks, Prometheus scrape targets at `http://localhost:9090/targets`.

## 2. Postgres Persistence

**Strengths.** Postgres holds the operational truth: source config, cron values, cached DOM anchor recipes, snapshots, runs, entities, and field-level change history. Foreign keys with `ON DELETE CASCADE` mean a source delete reliably unwinds entities, snapshots, runs, and entity_changes. Grafana queries high-cardinality detail directly via the Postgres datasource (the diagnostic row's "recent failures" and "recent runs" tables) — Prometheus would not handle that cardinality well.

**Weaknesses.** `db/init.sql` only runs on first volume creation, so schema drift is a real local-dev hazard without a migration tool. The `entity_changes` table grows unbounded — every volatile-field change inserts a row. A high-frequency source can produce tens of thousands of rows per day.

**Opportunities.** Add Alembic (or any real migration tool) when the project grows beyond a class deliverable. Add a partitioning + retention policy on `entity_changes` so the table doesn't grow unbounded.

**Threats.** Cascade deletes on a source with millions of `entity_changes` rows can take 30+ seconds, which feels like a broken UI. The React UI mitigates this with optimistic deletion (the row disappears immediately, the DELETE finishes in the background).

**Evidence.** `db/init.sql`, the `entity_changes` table, the Grafana "Recent runs" / "Recent failures" panels (Postgres datasource queries).

## 3. Worker + APScheduler

**Strengths.** The worker container owns APScheduler jobs separately from the API container. UI/API writes `sources.refresh_cron`; the worker reconciles from Postgres every 30 seconds without needing a restart or any inter-process notification. Per-source `max_instances=1` prevents pile-ups when a poll runs longer than its cron interval. Misfire and max-instances events both increment `webharvest_polls_skipped_total{reason}` so cron health is observable.

**Weaknesses.** In-process scheduling is simple but not distributed. One worker process owns the schedule for every source — a worker crash means polling stops globally until restart.

**Opportunities.** Add a worker heartbeat gauge so an extended worker outage is visible directly without inferring it from `PollSilent`. For multi-host deployments, swap APScheduler for a real distributed scheduler (Temporal, Celery beat, or equivalent).

**Threats.** Long-running anchoring polls (LLM calls can take 10-30s) can overlap cron ticks for fast sources. `max_instances=1` prevents pileup, but the dropped tick is observable as `polls_skipped_total{reason="max_instances_blocked"}`.

**Evidence.** `services/scraper/src/worker.py`, `webharvest_poll_total{path}`, `webharvest_polls_skipped_total{reason}`, the Cron-skips stat panel, the PollSilent alert.

## 4. Prometheus + Grafana

**Strengths.** Prometheus records the system over time at 15s scrape intervals; Grafana turns it into a real operational dashboard organized around the RED method (Rate, Errors, Duration). The dashboard's six rows each answer one operator question — there are no decorative panels. Six alert rules (ServiceDown, ConsecutiveFetchFailures, AntiBotDetected, PollSilent, AnchorBreakage, StaleEntitySpike) carry both diagnostic queries and remediation steps in their annotations, so an operator can act without leaving Grafana.

**Weaknesses.** Metrics must be named, labeled, and curated carefully. Earlier iterations of this project carried thesis-narrative metrics (cumulative cost saved, anchor age) that didn't earn their dashboard real-estate; pruning them was a deliberate design decision.

**Opportunities.** Wire alerts into Alertmanager → Slack / email / PagerDuty contact points. Add cAdvisor and `postgres_exporter` for container-resource and DB-health metrics — currently the dashboard has no view of CPU / memory / connection-pool utilization.

**Threats.** If the worker scrape target is missing from `prometheus.yml`, cron-driven behavior disappears from observability even though the app is still polling. The `PollSilent` alert mitigates this — it fires when poll rate drops to 0 on a previously-active source.

**Evidence.** `infra/prometheus/prometheus.yml`, `infra/prometheus/alerts.yml`, `infra/grafana/dashboards/webharvest.json`, the dashboard screenshots.

## 5. GitHub Actions + GHCR

**Strengths.** Even for a solo project, Actions provides a clean-runner reproducibility gate: lint, unit tests, full Compose build, integration smoke test against an Nginx-served HTML fixture, and a security-scan workflow that runs Trivy (image CVEs), pip-audit (Python deps), npm audit (React deps), and gitleaks (commit secrets). On tagged releases, `deploy.yml` builds the three app images with SBOM + provenance attestations and pushes to GHCR.

**Weaknesses.** It is not evidence of team-scale CI/CD maturity — there is no required-reviewer gate, no canary, no environment promotion. It's a submission/release verification path.

**Opportunities.** Add Renovate or extend the existing Dependabot config to cover docker-compose image pins. Add a Lighthouse CI step on the React UI for accessibility / perf regressions.

**Threats.** Docker builds on GitHub-hosted runners are slower than local builds, and workflow caches are not shared perfectly across jobs — caching `pip` and `npm` is straightforward, caching multi-stage Docker builds is fragile.

**Evidence.** `.github/workflows/ci.yml`, `.github/workflows/integration.yml`, `.github/workflows/security.yml`, `.github/workflows/deploy.yml`, GHCR tags on the repo, `.github/dependabot.yml`.

## 6. LLM Bootstrap + BS4 Fast Path (the unique angle)

**Strengths.** The LLM is converted from a recurring runtime dependency into a one-time provisioning step. Once anchors exist, cron polls cannot accidentally call the LLM — there's no fallback in the fast-path code that reaches for a model. The recipe is small JSON (a root selector plus per-field selectors) so it's cheap to cache, cheap to inspect, and cheap to regenerate when it breaks. Steady-state cost per source is exactly $0.

**Weaknesses.** CSS selectors break when a page is redesigned. Recovery requires a manual re-anchor (or a schema change, which auto-invalidates the recipe). There is no automatic re-anchor on degradation — the system surfaces the degradation but waits for an operator decision because re-anchoring costs money and could mask a real upstream problem.

**Opportunities.** Combine CSS-position selectors with content-anchored matching (e.g. "the row containing this anchor field value") for resilience to layout reshuffles. Add a confidence threshold that auto-triggers re-anchor below a configurable floor.

**Threats.** A bad initial anchor recipe can persist undetected if a page is genuinely low-volatility — the system's "0 entities" or "low entity count" signals are the only feedback loop. The `AnchorBreakage` alert fires when extraction count drops below 50% of its 1h moving average, but a wholly-broken initial anchor (where there's no baseline yet) won't trip it.

**Evidence.** `webharvest_fast_path_total{outcome=hit|miss}`, `webharvest_anchor_extraction_count`, `webharvest_anchor_re_anchor_total{reason}`, `webharvest_scraper_cost_usd_total`, the AnchorBreakage alert, the "Polls by path" panel showing fast-path dominance in steady state.
