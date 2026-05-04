# Architecture

WebHarvest is a polling system shaped around DevOps fundamentals: separate services, persistent state, cron scheduling, metrics, alerts, and an operational dashboard. The architecturally-distinct piece is that the LLM is invoked once per source (to derive a CSS-selector recipe) rather than on every poll.

## Service Graph

```text
React dashboard ──► scraper API ──► Postgres
                       └──► extracto (only when source has no anchors)

worker container ──► reads sources.refresh_cron from Postgres
                ──► fires per-source polls on APScheduler
                ──► exposes /metrics for Prometheus

Prometheus ──► scrapes scraper, worker, extracto, prometheus
          ──► evaluates 6 alert rules
          ──► Grafana renders the operational dashboard
```

## Run Lifecycle

1. Worker (or API trigger) loads source config from Postgres: URL, schema, identity_key, cached anchor recipe, selected anchoring model.
2. Fetch the URL with a single GET. Fetch metrics emit per status code, error class, response size, and redirect count. A consecutive-failure gauge bumps on each failure and resets on success.
3. If the source has cached anchors, run BeautifulSoup against the recipe (~50-100ms). This is the steady-state path — no LLM call.
4. If anchors are missing (first run, or operator-triggered re-anchor), call the selected LLM via extracto. The LLM returns a CSS-selector recipe + a bootstrap entity sample. The recipe is persisted on the source row.
5. Diff produced entities against `entities` for this source. Insert new identities. Update existing identities, recording per-field deltas to `entity_changes` ONLY for fields marked `volatile` in the schema. Mark identities not seen this run as `stale`.
6. Emit poll-, fast-path-, anchor-, and entity-level metrics. Insert the run into `runs` for postgres-backed Grafana panels.

## Tables

| Table | Purpose |
| --- | --- |
| `sources` | URL, schema, cron, selected model, cached anchor recipe (`anchors` JSONB), `last_anchored_at`. |
| `snapshots` | Full fetched HTML bodies. One row per fetch. |
| `runs` | Event log for every poll: backend (`fast-path` or model name), entity counts, cost, error. |
| `entities` | Current entity state keyed by `(source_id, identity)`. |
| `entity_changes` | Per-field volatile change history — every drift event. |

## Metrics By Operator Question

| Operator question | Metrics |
| --- | --- |
| Are services up? | `up{job=...}` (Prometheus built-in) |
| Is the system polling? | `webharvest_poll_total{source_id, path}` |
| What's failing? | `webharvest_fetch_errors_total{error_class}`, `webharvest_fetch_consecutive_failures` |
| Is cron healthy? | `webharvest_polls_skipped_total{reason}` |
| Is anything slow? | `webharvest_poll_duration_seconds_bucket`, `webharvest_fast_path_duration_seconds_bucket` |
| Is extraction working? | `webharvest_fast_path_total{outcome}`, `webharvest_anchor_extraction_count` |
| Is useful drift detected? | `webharvest_field_changes_total{source_id, field}`, `webharvest_scraper_run_entities_total{change}` |
| What did anchoring cost? | `webharvest_scraper_cost_usd_total{backend}`, `webharvest_anchor_re_anchor_total{reason}` |

## Alerts

| Alert | Triggers when | Remediation |
| --- | --- | --- |
| `ServiceDown` | `up == 0` for 1m | `docker compose ps && docker compose logs <job>` |
| `ConsecutiveFetchFailures` | gauge ≥ 5 for 1m | Check error_class breakdown; pause source or rotate UA |
| `AntiBotDetected` | error_class=anti_bot rate > 0 for 5m | Rotate User-Agent, slow cron, or pause source |
| `PollSilent` | poll rate drops to 0 on a previously-active source | `docker compose restart worker` |
| `AnchorBreakage` | extraction count < 50% of 1h moving average | `POST /sources/{id}/re-anchor` |
| `StaleEntitySpike` | ≥ 50 entities marked stale in 5m | Inspect snapshot; re-anchor if structure changed |

## Tradeoffs

**LLM once vs LLM every poll.** One-time LLM anchoring removes recurring LLM cost from cron and makes steady-state polls deterministic Python. The cost is manual recovery when selectors break — `AnchorBreakage` surfaces this; `re-anchor` resolves it.

**Compose vs full orchestration.** Docker Compose is local orchestration that makes the demo reproducible on one host. It is intentionally not Kubernetes — that scope was cut as adding complexity without rubric value.

**Prometheus metrics + Postgres SQL panels.** Prometheus carries rates, counters, gauges, and alerts. Postgres carries high-cardinality detail (per-entity history, per-run failure text) that would be awkward as Prometheus labels.

**Anchor + volatile field roles.** Splitting schema fields into `anchor` (stable identity) and `volatile` (watched for drift) makes the diff loop both more accurate (anchor differences are treated as extraction noise, not drift) and more meaningful (only volatile changes count as updates and surface in `entity_changes`).
