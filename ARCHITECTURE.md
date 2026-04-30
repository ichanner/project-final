# Architecture

This is the long version of what the README sketches in ASCII. It covers the request flow, the multi-model bake-off pattern, what each metric is for, and the trade-offs the design makes.

## Service graph

Three application services run in containers; Postgres, Prometheus, Grafana sit alongside.

```
                                  ┌──────────────────────────────────────────┐
                                  │                                          │
                                  │   OpenRouter (https://openrouter.ai/v1)  │
                                  │                                          │
                                  │   anthropic/claude-sonnet-4              │
                                  │   openai/gpt-4o                          │
                                  │   meta-llama/llama-3.3-70b-instruct      │
                                  │   google/gemini-2.0-flash-001            │
                                  │                                          │
                                  └────────┬─────────────────────────────────┘
                                           │ HTTPS, OpenAI-compatible API
                                           │
   ┌────────────┐    /api    ┌────────────┐│  /extract        ┌────────────┐
   │ Dashboard  │───────────▶│  Scraper   │┼─────────────────▶│  Extracto  │
   │ (React)    │            │ (FastAPI)  ││   one call per   │ (Node)     │
   │ schema     │            │            ││   model in a     │            │
   │ builder    │            │  fetch &   ││   single snapshot│  Routes by │
   │            │            │  snapshot  ││                  │  body.model│
   │            │            │  diff      │                   └─────┬──────┘
   └─────┬──────┘            │  persist   │                         │
         │ /api/runs         │            │                         │
         │                   └─────┬──────┘                         │
         │                         │                                │
         │       ┌─────────────────┴─────────────────────┐          │
         │       │                                       │          │
         │   ┌───▼─────────┐                       ┌─────▼──────────▼───┐
         │   │  Postgres   │                       │  Prometheus        │
         │   │             │                       │  (scrapes /metrics)│
         │   │  sources    │                       │                    │
         │   │  snapshots  │                       └─────────┬──────────┘
         │   │  runs       │                                 │
         │   │  entities   │                            ┌────▼─────┐
         │   └─────────────┘                            │ Grafana  │
         │                                              │          │
         └──────────────────────────────────────────────▶          │
                              browser links to /grafana/           │
                                                       └──────────┘
```

## The request lifecycle

When you POST `/api/sources/{id}/run`:

1. **Fetch.** Scraper pulls the URL with httpx. One GET, no retries on 429 (could add). Records the status code and HTML byte count as Prometheus metrics. Stores the raw HTML as a row in `snapshots` so schemas can be re-applied without re-fetching.
2. **Fan-out.** The same HTML is sent to extracto once per model in `[primary_model] + comparison_models`. The fan-out is `asyncio.gather` so the slowest model bounds the run, not the sum.
3. **Per-model extraction.** Extracto uses the OpenAI SDK pointed at OpenRouter, with `model` set per request. The system prompt and JSON schema are identical across models — the only variable is which model handles the call.
4. **Parse the response.** OpenRouter providers respect `response_format: json_schema` to varying degrees. Anthropic via Bedrock has historically returned JSON under `"data"` instead of `"entities"`, sometimes wraps in markdown fences, sometimes leads with chain-of-thought prose. Extracto's parser walks JSON.parse → fenced-block extractor → `{...}` substring → tolerant key lookup (`entities` → `data` → `items` → `results` → `rows`). This is the kind of layer you don't want, but it's the price of fan-out across providers with different output discipline.
5. **Persist run rows.** Each model's call becomes one row in `runs`, all sharing the same `snapshot_id`. The primary row gets `is_primary = TRUE`. Challenger rows get an `agreement` score (Jaccard of identity-keys with the primary's set).
6. **Diff the primary.** Only the primary's entities go through the diff-and-persist path: lookup `(source_id, identity)`, INSERT/UPDATE/mark-stale. Challenger entities are ephemeral — they live in `runs.entity_count` but never touch the `entities` table.
7. **Emit metrics.** Per-model histograms for confidence; counters for cost and tokens; gauges for set-level (Jaccard) and per-field agreement-vs-primary.

## Identity, schemas, and the "no identity_key" UX

In 0.2.0 the dashboard's source builder no longer asks for an `identity_key`. The implicit identity for a new source is the value of the **first field declared in the schema**. So if your schema is `{title, points, user, comments}`, the dedup key is `entity.title`. This is enforced in `services/scraper/src/diff.py`'s `identity_for()`, which falls back through:

1. Explicit `identity_key` array (legacy / power users) →
2. First schema field's value →
3. JSON-stringified entity (so two literally-identical entities still hash the same)

You can still pass `identity_key` via the API for composite keys (`["company", "filing_date"]`) — the field just isn't surfaced in the UI for the common case.

## What each metric is for

These are the series Grafana groups by.

### Scraper (Python)

| Metric | Type | Labels | What it answers |
| --- | --- | --- | --- |
| `webharvest_scraper_fetch_total` | counter | `source_id`, `outcome` | Are pages being fetched? Is anything 404-ing? |
| `webharvest_scraper_fetch_duration_seconds` | histogram | `source_id` | Per-source fetch latency. Spikes = network issue or site rate-limiting. |
| `webharvest_scraper_run_entities_total` | counter | `source_id`, `change` ∈ {new, updated, stale} | Diff churn. All-stale on a stable source = the page changed. |
| `webharvest_scraper_run_confidence` | histogram | `source_id`, `backend` | Model-reported confidence per model. Low p50 = model isn't sure of page structure. |
| `webharvest_scraper_cost_usd_total` | counter | `source_id`, `backend` | Running USD spend, per model per source. |
| `webharvest_scraper_escalations_total` | counter | `source_id`, `model` | Challenger call counts. In bake-off mode this is "every challenger run that fired." |
| `webharvest_agreement_jaccard` | gauge | `source_id`, `primary`, `challenger` | Set-level: did the challenger see the same entities as the primary? |
| `webharvest_field_agreement` | gauge | `source_id`, `primary`, `challenger`, `field` | Field-level: for matched entities, did each field's value agree? **The dashboard's flagship.** |

### Extracto (Node)

| Metric | Type | Labels | What it answers |
| --- | --- | --- | --- |
| `extracto_extract_duration_seconds` | histogram | `model`, `outcome` | Wall-clock per model. The 50× spread between gpt-4o (~4s) and llama-70b (~15s) shows up here. |
| `extracto_tokens_total` | counter | `model`, `kind` ∈ {input, output} | Token throughput. Cheap-model bias toward longer outputs is visible. |
| `extracto_cost_usd_total` | counter | `model` | Same number as the scraper-side cost counter, but from the extracto side. |

## Trade-offs the design makes

### Cost vs. comparison value

Running four models on every snapshot multiplies your bill by ~4× the average per-model rate. For HN with rich schema (~12K input tokens, ~100 output tokens per entity × 30 entities) the four-model bake-off costs ~$0.10 per run. For a class project this is fine; for production you'd run challengers as a sample (every Nth run) and let the primary handle the rest.

### Latency

Fan-out is parallel, so the run completes in `max(model_durations)`, not the sum. But that means the slowest model — usually llama-3.3-70b at ~15-25s for HN-sized pages — is the bound. The dashboard surfaces this directly via `extracto_extract_duration_seconds` p95 by model.

### Set-level vs. field-level agreement

Two metrics are recorded for a reason:

- **`webharvest_agreement_jaccard`** (set-level) catches "the cheap model missed 1 of 30 entities" but not "the cheap model got 30 entities and 1 wrong field per entity."
- **`webharvest_field_agreement`** (field-level) is computed only on entities that *both* models extracted (the intersection, by identity). It surfaces fine-grained disagreement: e.g., "llama and claude both saw 28 of the 30 HN posts, but llama disagreed on `comments` for 4 of them."

Together they answer "who saw what" and "who got the details right" separately, which is what an operator actually wants when picking a model.

### Persistence model

Only the primary's entities make it to the `entities` table. This is deliberate: the user wants one canonical answer, and challenger runs are diagnostic. If a challenger consistently outperforms the primary on a source, you change the primary; you don't blend outputs. (Easy: one column update on `sources.primary_model`.)

### What the heuristic was, and isn't anymore

0.1.0 had a Python heuristic service (BeautifulSoup, JSON-LD parser, table detector) as a "free" first tier — try it, escalate to cloud on low confidence. That worked, but framing it as a comparable tool to a frontier LLM was a category error: the heuristic only handles structured-markup pages, where every cloud model also gets ~100% accuracy. So the comparison was either trivial (both right) or unfair (heuristic can't even attempt). 0.2.0 drops it entirely.

A future version could revive the heuristic as a *ground-truth oracle* — only on pages with valid JSON-LD, only as a check against what the cloud models claim. That's a real comparison and a real metric ("how often does each cloud model agree with the page's own structured data?"). It's not built. The vestigial dir is gone; bringing it back would fit as a third entity source in `runner.py`'s fan-out.

## Where the parts live

```
db/init.sql                          Schema with primary_model + comparison_models
docker-compose.yml                   Service graph (scraper, extracto, dashboard, postgres, prom, grafana)
infra/grafana/                       Provisioned datasource + dashboard JSON
infra/prometheus/prometheus.yml      Scrape config
services/scraper/src/runner.py       The bake-off fan-out, identity resolution, agreement metrics
services/scraper/src/main.py         FastAPI surface (sources, runs, entities)
services/scraper/src/diff.py         identity_for() — schema-first identity resolution
services/extracto/src/extract.js     Per-model extraction + tolerant JSON parser
services/extracto/src/anthropicClient.js   OpenRouter client + per-model pricing
services/dashboard/src/App.jsx       Schema builder UI + snapshot-grouped runs
.github/workflows/ci.yml             Lint + test + compose-build
.github/workflows/security.yml       Trivy + audits + secret scan
.github/workflows/integration.yml    Full stack-up + bake-off on fixtures
.github/workflows/deploy.yml         Tagged release -> GHCR with SLSA + SBOM
```
