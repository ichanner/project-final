# Architecture

WebHarvest is a polling system with a DevOps-first shape: separate services, persistent state, cron scheduling, metrics, alerts, and an operational dashboard.

## Service Graph

```text
React dashboard
  -> scraper API
       -> Postgres
       -> extracto only when source has no anchors

worker container
  -> reads sources.refresh_cron from Postgres
  -> runs source polls on APScheduler
  -> exposes /metrics

Prometheus
  -> scrapes scraper, worker, extracto, prometheus
  -> evaluates alert rules
  -> Grafana dashboard
```

## Run Lifecycle

1. Load source config from Postgres: URL, schema, cron, HTTP validators, cached anchors, and selected anchoring model.
2. Fetch the URL.
   - If conditional polling is enabled and the source has validators, send `If-None-Match` / `If-Modified-Since`.
   - If the server returns `304 Not Modified`, insert a `conditional-304` run row, touch existing entities as still seen, and skip extraction.
   - If the server returns a full body, store a snapshot and update `etag`, `last_modified`, and `last_content_bytes`.
3. If cached anchors exist, run BeautifulSoup against the DOM recipe.
4. If anchors are missing, call the selected LLM once to create anchors and a bootstrap entity sample.
5. Persist entities and record field-level changes only for schema fields marked `volatile`.
6. Emit Prometheus metrics for fetch health, polling path, skipped extraction, anchor health, cost saved, and drift.

## Tables

| Table | Purpose |
| --- | --- |
| `sources` | URL, schema, cron, conditional polling flag, HTTP validators, selected model, cached anchors. |
| `snapshots` | Full fetched HTML bodies for modified responses. 304 skips do not store a body. |
| `runs` | Event log for every poll path: `conditional-304`, `fast-path`, or selected LLM model. |
| `entities` | Current entity state keyed by source + identity. |
| `entity_changes` | Per-field volatile change history. |

## Metrics By Claim

| Claim | Metrics |
| --- | --- |
| Polling efficiency | `webharvest_fetch_requests_total`, `webharvest_fetch_bytes_total`, `webharvest_fetch_bytes_saved_total`, `webharvest_extractions_skipped_total`. |
| Extraction efficiency | `webharvest_poll_total`, `webharvest_fast_path_total`, `webharvest_cost_saved_usd_total`, `webharvest_anchor_re_anchor_total`. |
| Stability | `webharvest_anchor_age_seconds`, `webharvest_anchor_extraction_count`, `webharvest_anchor_field_population_ratio`, `webharvest_anchor_phantom_update_ratio`. |
| Self-awareness | `webharvest_fetch_errors_total`, `webharvest_fetch_consecutive_failures`, `webharvest_polls_skipped_total`. |
| Drift detection | `webharvest_field_changes_total`, `webharvest_scraper_run_entities_total`, Postgres `entity_changes`. |

## Tradeoffs

**Conditional HTTP vs naive GET.** Conditional polling saves bandwidth and extraction work when servers support validators. It gracefully falls back to full-body polling when they do not.

**LLM once vs LLM every poll.** One-time LLM anchoring keeps the flexible setup experience while removing recurring LLM cost from cron. The cost is manual recovery when selectors break.

**Compose vs full orchestration.** Docker Compose makes the final project reproducible and demoable. It is not Kubernetes; that is an intentional scope choice.

**Prometheus metrics + Postgres SQL.** Prometheus is used for rates, counters, gauges, alerts, and time-series panels. Postgres is used for high-cardinality entity history that would be awkward as Prometheus labels.
