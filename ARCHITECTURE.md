# Architecture

This is the long version of what the README sketches. It's written in the order the system processes a request, with the trade-offs called out where they matter.

## Service graph

Six application services in `docker-compose.yml`. The split between `scraper` (API) and `worker` (scheduler) is the part that's not just convention — it's the real DevOps API/worker split.

```
   ┌────────────┐   /api    ┌──────────┐                     ┌──────────────┐
   │ Dashboard  │──────────▶│ Scraper  │────── /extract ────▶│   Extracto   │
   │ React/nginx│           │ FastAPI  │  (only when source  │  Node + OpenAI│
   └─────┬──────┘           │          │   has no anchors)   │  SDK pointed  │
         │                  └────┬─────┘                     │  at OpenRouter│
         │ proxies               │                           └──────┬───────┘
         │                       │ runs +                           │
         │                       │ persistence                      │ HTTPS
         │                  ┌────▼─────┐                            ▼
         │                  │ Postgres │                  ┌──────────────────┐
         │                  │          │                  │   OpenRouter     │
         │                  │ sources  │                  │  4 models, one   │
         │                  │ runs     │                  │  pricing endpoint│
         │                  │ entities │                  └──────────────────┘
         │                  │ entity_  │
         │                  │ changes  │
         │                  │ snapshots│
         │                  └────┬─────┘
         │                       │
         │                  ┌────▼──────┐
         │                  │  Worker   │  separate container, same image as scraper
         │                  │ APSchedul │  reconciles sources.refresh_cron every 30s
         │                  │ er        │  fires run_source(sid) at each cron tick
         │                  └───────────┘
         │
         │      ┌───────────┐    ┌──────────┐
         └─────▶│Prometheus │───▶│ Grafana  │  Prom + Postgres datasources
                │ + alerts  │    │ 2 boards │
                └───────────┘    └──────────┘
```

## The request lifecycle

When a run fires (via cron or manual `POST /sources/{id}/run`):

### 1. Fetch + snapshot

`scraper.fetcher.fetch()` issues an `httpx.get()` with a realistic Chrome User-Agent and the standard `Accept` headers. One GET, no retries on 429 — that would be polite but it's not built. The full HTTP body goes into the `snapshots` table so the same HTML can be re-extracted later with a different recipe (you don't pay to re-fetch).

The `User-Agent` matters more than I thought going in. A naive `python-httpx/X.Y` UA gets rejected by Wikipedia's mirror-checking, by Cloudflare, and by anything with a basic bot heuristic. The realistic Chrome UA + `Accept-Language` + `Sec-Fetch-*` is the difference between "works on most pages" and "doesn't work on most pages."

### 2. The decision

```python
if cached_anchors:
    # FAST PATH — BS4 only, never call LLM
    apply_anchors(html, cached_anchors)
    persist_run + diff
    return

# Else — first run or after re-anchor
call LLM bake-off (primary + challengers)
persist all anchors verbatim (verified or not)
persist primary's entities (BS4 result if anchors verified, LLM sample otherwise)
```

The line is hard. **The cron physically cannot trigger an LLM call** — the LLM is only invokable from the path where `cached_anchors IS NULL`. That's enforced by control flow, not by guideline. It's the cost-discipline guarantee.

### 3. The fast path (every poll except first)

`apply_anchors()` lives in `services/scraper/src/dom_extractor.py`. It does one thing: takes HTML + a recipe, applies the root selector, then for each matched row applies the per-field selectors with their extract methods (`text` / `attr:href` / `html`) and transforms (`parseFloat` / `trim` / etc.). Sub-second on 300KB of HTML.

Two robustness features that took longer than they should have to land:

**First-occurrence dedup.** Pages have multiple repeating regions that match the same selector — Wikipedia has companion tables, blockchain.com has hydration JSON sections, etc. If `root_selector` returns 1500 nodes but only 113 distinct identities, we keep the first DOM occurrence of each. Eliminates the flicker where the same entity got bound to different DOM rows on different runs.

**Anchor cross-check.** Even with dedup, the LLM occasionally produces selectors that pull a wrong cell on some rows (off-by-one due to colspan, footnote markers, etc.). When the diff loop sees a row whose **anchor fields** disagree with the stored entity's anchors, the row is rejected — keep the previous data, mark `last_seen` so it doesn't go stale, log a warning. Caught the "Apple revenue 93,736 → 391,035 → 93,736" oscillation that was eating 18 phantom updates per poll.

### 4. The first-run LLM bake-off

When a source has no anchors yet (first run, or after `POST /sources/{id}/re-anchor`), the scraper fans out to extracto in parallel for the primary model + every challenger. Each model returns a JSON object with:

- `root_selector` — CSS selector for the repeating region
- `fields` — per-field sub-selector + extract method + transform
- `entities` — first 30 rows as a verification probe + bootstrap fallback
- `expected_count` — integer estimate
- `confidence` — number in [0, 1]

The output is constrained by a strict JSON schema (`response_format: {type: "json_schema", strict: true}`) but providers respect that with varying enthusiasm. Bedrock-served Claude has a habit of returning `data: [...]` instead of `entities: [...]`, ignoring the strict directive. The parser handles `entities` / `data` / `items` / `results` / `rows` and strips markdown fences if a model insists on wrapping in them.

### 5. Verify + persist

`verify_anchors()` applies the recipe via BS4 and grades the result:

- Did `root_selector` match anything? (count > 0)
- Did it match within 50% of the LLM's claimed `expected_count`? (rough sanity check)
- Does the first matched entity equal the LLM's `verification` probe? (byte-equality on the identity field)

If the verdict is OK, BS4's output is used as the entity list. Anchors are saved to `sources.anchors` as the canonical recipe.

If the verdict fails (BS4 returned 0 entities, or the verification probe doesn't match), the LLM's first-30 entity sample is used as a one-time bootstrap. Anchors are saved **anyway**, even broken ones — the next poll will use BS4 against them, return 0, and the `webharvest_fast_path_total{outcome="miss"}` counter increments. That's the user's signal to re-anchor.

### 6. Diff + persist

`_diff_and_persist()` is where the field-role split actually pays off.

```python
volatile_fields = [f for f in schema.fields if f.role == "volatile"]
# anchor fields are everything else (default role = "anchor")

for ent in entities:
    existing = lookup by identity
    if existing is None:
        INSERT, count as new
    else:
        # anchor cross-check: reject row if anchor fields disagree
        if anchor_mismatch(existing, ent):
            UPDATE last_seen only — keep stored data
            continue
        UPDATE data (silently merge anchor differences)
        if any volatile field changed:
            count as updated
            log entity_changes for each volatile field that changed
            increment webharvest_field_changes_total{field}
```

So **drift counts only on volatile fields**. Anchor field whitespace flicker, footnote markers, BS4 binding noise — all silently merge into the stored row without firing the drift alert or incrementing the counter.

Stale detection: any entity whose `last_run_id` doesn't match this run gets `stale=true`. Stale-not-deleted — we keep the row so its history isn't lost.

### 7. Metrics emit

Every step records a metric:

| Metric | What it answers |
| --- | --- |
| `webharvest_scraper_fetch_total{source_id, outcome}` | Are pages being fetched? Is anything 404-ing? |
| `webharvest_scraper_fetch_duration_seconds{source_id}` | Per-source fetch latency. |
| `webharvest_scraper_run_entities_total{source_id, change}` | Entities by change type (new / updated / stale). |
| `webharvest_scraper_run_confidence{source_id, backend}` | Model-reported confidence by backend. |
| `webharvest_scraper_cost_usd_total{source_id, backend}` | Running USD spend per source per backend. |
| `webharvest_field_changes_total{source_id, field}` | Per-field drift count. **Only volatile fields ever increment this.** |
| `webharvest_agreement_jaccard{source_id, primary, challenger}` | Set-level agreement between models in a bake-off run. |
| `webharvest_field_agreement{source_id, primary, challenger, field}` | Per-field agreement on intersection. |
| `webharvest_fast_path_total{source_id, outcome}` | Fast-path hit / miss count. The cost-discipline SLI. |
| `webharvest_fast_path_duration_seconds{source_id}` | BS4 wall-clock per poll (~50-100ms typical). |
| `extracto_extract_duration_seconds{model, outcome}` | LLM call wall-clock by model. |
| `extracto_tokens_total{model, kind}` | Token throughput by model and direction. |
| `extracto_cost_usd_total{model}` | Same number as the scraper-side cost counter, from the extracto side. |

## What each Postgres table is for

```
sources         one row per URL being watched. Holds schema, anchors, cron,
                primary/challenger models. The anchors JSONB column is the
                cached recipe — when it's NULL, the next run goes through the
                LLM. When it's set, every run is BS4-only.

snapshots       one row per fetch. status_code + raw HTML. Lets you re-extract
                a past snapshot with a new schema without re-fetching.

runs            one row per (snapshot, model) pair. The bake-off is here:
                same snapshot_id across rows, different backend strings.
                is_primary distinguishes the row whose entities got persisted.

entities        the canonical state of each tracked thing. Identity-keyed,
                stale-not-deleted. data is JSONB.

entity_changes  granular per-field change log. One row per volatile-field
                value change. Drives the entity-history sparklines and the
                Grafana SQL panels.
```

## Trade-offs the design makes

### Cost discipline at the cost of recovery flexibility

The "scheduled runs cannot call the LLM" rule is strict. If a source's anchors break (the page got redesigned), the scheduled poll returns 0 entities and increments `fast_path_total{outcome="miss"}`. The source stays broken until the user manually clicks re-anchor.

Alternative I considered: auto-retry with the LLM on the first miss. Rejected — that's exactly the loop that drained credits twice during the build. Once the credit-discipline pattern was in, the system stopped surprising me.

### CSS selectors over text-based matching

The recipe is CSS selectors. They're fragile to redesign. A more robust approach would be content-anchored matching: find the row whose text contains the entity's anchor-field values, then walk to the volatile-field cells from there.

Not built. Reasons:
- CSS works for the pages that have stable structure (most of the web).
- The anchor cross-check + first-occurrence dedup catch most of the failure modes.
- Content-anchored matching is real engineering, not a one-day fix.

A future version could mix the two: try CSS first, fall back to content matching when it's broken. The plumbing is there.

### Set-level vs field-level agreement

Two metrics on bake-off runs because they measure different things:

- **`webharvest_agreement_jaccard`** (set-level) = "did the challenger see the same set of entities as the primary?" Catches "missed the row" / "hallucinated a row."
- **`webharvest_field_agreement`** (field-level) = "for the entities both saw, did they agree on each field's value?" Catches "got the row but mis-read the price."

The Wikipedia comparison hit ~0.93 on field agreement for `comments` and 1.0 on title — useful to see the disagreement concentrate on specific fields rather than across-the-board.

### Persistence: only the primary writes

Challenger model runs record cost / latency / confidence / agreement, but their entities don't go into the `entities` table. The user picks one canonical answer; challengers exist for audit.

If a challenger consistently outperforms the primary, you change `sources.primary_model` and the next bake-off reverses the comparison. One column update.

## Where the parts live

```
db/init.sql                            schema with field-role-aware columns
docker-compose.yml                     6 services + healthcheck deps
infra/grafana/                         provisioned datasources + 2 dashboard JSONs
infra/prometheus/prometheus.yml        scrape config
infra/prometheus/alerts.yml            5 alert rules
services/scraper/src/main.py           FastAPI surface
services/scraper/src/worker.py         APScheduler-owning worker container entrypoint
services/scraper/src/runner.py         the run_source() orchestration
services/scraper/src/dom_extractor.py  BS4 recipe applier (the fast path)
services/scraper/src/diff.py           identity_for() — schema-first identity
services/extracto/src/extract.js       hybrid LLM call (recipe + sample)
services/extracto/src/anthropicClient  OpenRouter client + per-model pricing table
services/dashboard/src/App.jsx         schema builder + inline cron + entity history
.github/workflows/ci.yml               lint + test + compose-build smoke
.github/workflows/security.yml         trivy + audits + secret scan
.github/workflows/integration.yml      stack-up + bake-off on fixtures
.github/workflows/deploy.yml           tagged release → GHCR with SLSA + SBOM
```

## What I'd do differently

If I were starting fresh:

1. **Content-anchored matching from day one**, not CSS selectors. The CSS approach pulled me into the row-binding bug for two hours of debugging that wouldn't have happened with text-based fingerprints.

2. **Playwright fetcher behind a flag.** Even on a class deadline, I'd have committed half a day to a JS-rendering fallback. The system silently degrades on SPAs and that's a bigger user-experience hole than I appreciated until the demo phase.

3. **Per-source sampling on the bake-off.** Running 4 models on every poll is great for benchmarking, terrible for cost. The right pattern is: primary on every poll, challengers on every Nth (1 in 10, configurable). Already-built dashboards still work; cost drops 10×.

4. **Schema inference from the page.** The original spec called for "LLM proposes schema, user confirms." That's a real UX win for non-technical users. It's a one-prompt addition to extracto.

The system as it stands is a useful tool, not just a class assignment. That part I didn't expect.
