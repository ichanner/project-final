# Architecture

A one-page reference for how WebHarvest is wired up. For higher-level intent, see [README.md](./README.md). For per-tool tradeoffs, see [SWOT_ANALYSIS.md](./SWOT_ANALYSIS.md). For comparative measurements between cloud and local extractors, see [EVALUATION.md](./EVALUATION.md).

## 1. Service graph

```
                    +-------------+
                    |  Dashboard  |  :3000
                    | (React/Vite |
                    |   + nginx)  |
                    +------+------+
                           | /api -> scraper
                           v
+----------+         +------------+         +-------------+
|  Browser | <-----> |  Scraper   | <-----> |  Postgres   |
+----------+   :80   | (FastAPI)  |   5432  |   16        |
                     |   :8080    |         +-------------+
                     +-+--------+-+
                       |        |
              local    |        |  cloud
              path     |        |  path
                       v        v
              +-----------+   +-----------+
              | local-    |   | extracto  |
              | model     |   | (Node)    |
              | (FastAPI) |   |           |
              | :8082     |   | :8081     |
              +-----------+   +-----+-----+
                                    |
                                    v
                          +-------------------+
                          | api.anthropic.com |
                          | (Claude Sonnet)   |
                          +-------------------+

Observability plane (orthogonal to the data plane):

  scraper:8080/metrics  -+
  extracto:8081/metrics -+--> Prometheus :9090 --> Grafana :3001
  local-model:8082/met. -+
```

Every service exposes both `/health` (liveness) and `/metrics` (Prometheus). Compose `depends_on` uses `condition: service_healthy` so the scraper never starts before Postgres is accepting connections.

## 2. Request flow — one scrape

```
client                 scraper            local-model        extracto         postgres
  |  POST /sources/{id}/run  |                  |                |               |
  | -----------------------> |                  |                |               |
  |                          | SELECT source    |                |               |
  |                          | -----------------|----------------|-------------> |
  |                          | <----------------|----------------|-------------- |
  |                          | GET url (httpx)  |                |               |
  |                          |--> external <----|                |               |
  |                          | INSERT snapshot, run                              |
  |                          | -----------------|----------------|-------------> |
  |                          |                  |                |               |
  |                          | POST /extract    |                |               |
  |                          | ---------------> |                |               |
  |                          | <--- {entities, confidence} ----  |               |
  |                          |                                                   |
  |          if confidence < LOCAL_CONFIDENCE_THRESHOLD (default 0.7):           |
  |                          | POST /extract                                     |
  |                          | --------------------------------> |               |
  |                          |                                   | Claude API    |
  |                          |                                   | --> Anthropic |
  |                          | <--- {entities, confidence, cost_usd} ----        |
  |                          |                                                   |
  |                          | for each entity:                                  |
  |                          |   identity_for(entity, identity_key)              |
  |                          |   if exists & data differs -> UPDATE              |
  |                          |   if exists & data same    -> touch last_seen     |
  |                          |   else                     -> INSERT (new)        |
  |                          | UPDATE entities SET stale=TRUE                    |
  |                          |   WHERE last_run_id != run_id                     |
  |                          | UPDATE runs SET finished_at, counts, cost         |
  |                          | -----------------|----------------|-------------> |
  |  {run_id, backend,       |                                                   |
  |   confidence, counts,    |                                                   |
  |   cost}                  |                                                   |
  | <----------------------- |                                                   |
```

Things worth noting:

- HTML is **stored** before extraction. If a schema changes later, the extractor can be re-run on the existing snapshot without re-fetching.
- Diffing is **identity-keyed**, not content-hashed. Updates carry the entity forward; missing entities are flagged stale, never deleted (could be pagination).
- The local→cloud escalation is the load-bearing piece of the cost story. Every escalation is counted in `webharvest_scraper_escalations_total`.

## 3. Data lifecycle

```
sources    1---*  snapshots   (raw HTML, kept indefinitely)
   1---*   runs        (one per scrape attempt; FK to snapshot)
   1---*   entities    (identity-keyed; first_seen/last_seen/stale)
```

State transitions for an entity:

```
   (new)  -- INSERT --> live (first_seen=now, stale=FALSE)
   live   -- same data, seen again --> live (last_seen bumped)
   live   -- new data, same identity --> live (data updated, stale=FALSE)
   live   -- not seen this run -> stale=TRUE (data preserved)
   stale  -- seen again --> live (stale=FALSE, last_seen bumped)
```

There is no DELETE path. Disappearing entities go stale in case the disappearance is just pagination flakiness; recovery is automatic.

## 4. CI / security pipeline

```
        push to PR / main                                  push tag v*
              |                                                 |
              v                                                 v
+----------------------------+                +--------------------------------+
|  ci.yml                    |                |  deploy.yml                    |
|  - ruff (scraper, local)   |                |  - docker login ghcr.io        |
|  - eslint (extracto, dash) |                |  - docker buildx + push        |
|  - pytest (scraper, local) |                |    each of 4 services to       |
|  - node --test (extracto)  |                |    ghcr.io/owner/web-harvest-* |
|  - vite build (dashboard)  |                |  - SBOM + provenance attached  |
|  - docker compose build    |                |  - GitHub Release with notes   |
+--------------+-------------+                +--------------------------------+
               |
               v
+----------------------------+
|  integration.yml           |
|  - compose up + fixture    |
|  - hit /health each svc    |
|  - POST source -> run      |
|  - assert entity counts    |
|  - re-run -> assert idemp. |
+--------------+-------------+
               |
               v
+----------------------------+
|  security.yml              |
|  per-service:              |
|    - pip-audit / npm audit |
|    - Trivy image scan      |
|       -> SARIF to code     |
|          scanning          |
|       -> CycloneDX SBOM    |
|          as artifact       |
|  repo-wide:                |
|    - Trivy fs scan         |
|    - gitleaks (secrets)    |
|  schedule: weekly          |
+----------------------------+
```

Three workflows, three concerns. CI gates correctness, integration gates the wiring, security runs continuously and weekly.

## 5. Observability

| Layer        | Source                                       | Where it surfaces                              |
| ------------ | -------------------------------------------- | ---------------------------------------------- |
| Per-fetch    | `webharvest_scraper_fetch_total{outcome}`    | "Fetches/min by outcome" stat                  |
| Per-run      | `webharvest_scraper_run_entities_total{change}` | "Entities by change type" timeseries           |
| Confidence   | `webharvest_scraper_run_confidence_bucket{backend}` | p50/p95 confidence by backend                  |
| Cost         | `webharvest_scraper_cost_usd_total{source_id}` | "Cost per source (USD/hour)"                   |
| Escalation   | `webharvest_scraper_escalations_total`       | "Cloud escalation rate"                        |
| Latency      | `webharvest_scraper_fetch_duration_seconds_bucket` | p95 fetch duration                             |
| Cloud detail | `extracto_tokens_total{kind}`                | tokens/sec by kind (input/output/cache)        |
| Compare      | `extracto_extract_duration_seconds_bucket` + `local_model_extract_duration_seconds_bucket` | p50/p95 latency cloud vs local on one panel    |

Every panel in the spec is provisioned in `infra/grafana/dashboards/webharvest.json` and auto-loaded on Grafana startup.
