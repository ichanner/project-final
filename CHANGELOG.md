# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [0.3.0] — anchored fast-path + field roles

The release where the system became actually usable. Two big architectural ideas landed: LLM-bootstrapped DOM anchoring (LLM runs once per source, BeautifulSoup forever after) and the volatile/anchor field role split (drift only counts on fields you mark as worth watching).

### Why these changes

The previous release ran the LLM on every poll and counted every micro-extraction-difference as a "drift event." Two consequences fell out:

- I drained $14 of OpenRouter credits twice in one afternoon by leaving an every-minute cron firing on Claude.
- The Grafana drift panels showed 18-20 "updated" entities per Wikipedia poll — every poll, indefinitely. Phantom drift from whitespace and footnote-marker noise on fields that hadn't actually changed.

Both were architectural problems, not configuration ones. This release fixes both architecturally.

### Added

- **`sources.anchors` JSONB column** + `last_anchored_at`. Caches the LLM's CSS-selector recipe so subsequent polls are deterministic BeautifulSoup, no LLM call.
- **`services/scraper/src/dom_extractor.py`**. Applies cached anchors via BS4 with first-occurrence dedup — when the root selector matches the same identity in multiple page regions, only the first DOM occurrence wins. Stable across re-scrapes.
- **Field roles**: every schema field is `anchor` (default) or `volatile`. Drift only counts on volatile fields. Anchor flicker is silent.
- **Anchor cross-check** in `_diff_and_persist`. Reject row updates whose anchor field values disagree with the stored entity's anchors. Catches the wrong-row binding bug.
- **`POST /sources/{id}/re-anchor`** endpoint + React button to invalidate cached anchors.
- **`GET /sources/{id}/snapshot`** to inspect the most recent fetched HTML — verify what the LLM/BS4 actually saw.
- **`webharvest_fast_path_total{source_id, outcome}`** + duration histogram. Per-source SLI: healthy = 95%+ hits.
- **Field-level agreement metric** (`webharvest_field_agreement`) + matrix panel in Grafana.
- **Postgres datasource in Grafana** alongside Prometheus, for high-cardinality entity-level panels.
- **Entity-history Grafana dashboard** (`/d/webharvest-entity`) — drilldown per entity with sparklines and full change log.
- **Schema builder UI** in React: add fields with name/type/volatile-toggle, JSON preview, model presets, "add and run" one-shot button.
- **Per-source live cron editor** (UI inline) with presets (1m / 2m / 5m / 15m / 1h / 6h / off).
- **`worker` container**: real API/worker split. Same image as scraper, different command (`python -m src.worker`). Reconciles per-source cron from Postgres every 30s.
- **`FastPathHitRateLow` Prometheus alert**. Fires when a source's fast-path hit rate drops below 70% for 10m — the operational signal that anchors broke and need re-anchoring.

### Changed

- The cron physically cannot trigger an LLM call. Once a source has cached anchors, every scheduled poll uses BS4 only — even if the anchors are broken. The user must explicitly invalidate via re-anchor to spend credits again.
- Hybrid LLM call: a single request returns BOTH an anchor recipe AND up to 30 sample entities (verification + bootstrap). The recipe is the cost-saver; the sample is the fallback when BS4 verification fails.
- HTML preprocessing: strip `<head>`, `<script>`, `<style>`, `<svg>`, `<noscript>`, comments BEFORE applying the byte cap. Most pages have 60-90% of bytes in those tags. Cap raised to 300KB; smart-trim now prefers `<main>` / `<article>` content.
- User-Agent on the scraper switched to a realistic Chrome string. The default `python-httpx/X.Y` was getting rejected by Wikipedia's mirror checks.
- Identity resolution: now derives from the first declared schema field by default. `identity_key` API field still works for composite keys (`["company", "filing_date"]`) — just not exposed in the UI for the common case.

### Removed

- The previous "fall back to LLM-direct extraction every poll when anchors fail" behavior. Replaced with: persist anchors verbatim (broken or not), let next poll's BS4 produce 0 entities, surface as fast-path miss, require manual re-anchor.

### Migration note

`db/init.sql` is incompatible with 0.2.x. Wipe with `docker compose down -v` before bringing up.

## [0.2.0] — multi-model bake-off

Replaced the heuristic-vs-cloud routing with a multi-model bake-off across four cloud LLMs (Claude Sonnet 4, GPT-4o, Llama 3.3 70B, Gemini 2.0 Flash) via OpenRouter. Every snapshot fans out to all configured models in parallel; the primary's entities are persisted, challengers run for measurement.

### Added

- `sources.primary_model` + `sources.comparison_models[]`.
- `webharvest_agreement_jaccard{source_id, primary, challenger}` set-level metric.
- 4 Prometheus alert rules (drift, agreement, cost surge, stale spike).
- Two-table demo fixture (`tests/integration/fixtures/`).
- OpenRouter pricing table for the four models.

### Removed

- `services/heuristic/` and the heuristic-fallback routing. Cloud-only.

## [0.1.0] — initial release

Five-service Docker Compose stack with a heuristic local extractor and Anthropic SDK cloud extractor. Confidence-based escalation. Original spec deliverables: `docker-compose.yml`, four GitHub Actions workflows, Trivy + audits + gitleaks, auto-provisioned Grafana, Dependabot.

### Scope cuts vs. the original spec (carried forward to 0.3)

- Distilled local model on llama.cpp — replaced first by a Python heuristic, then dropped entirely in 0.2.
- Wayback Machine training pipeline — never built.
- Web search source discovery — never built.
- Pagination auto-detection — never built.
