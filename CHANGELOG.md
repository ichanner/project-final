# Changelog

All notable changes to WebHarvest are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.2.0] — multi-model bake-off

This release replaces the heuristic-vs-cloud routing with a multi-model bake-off across four cloud LLMs. Every run picks one model as the *primary* (its entities are what get persisted) and a list of *challengers* (run on the same snapshot for measurement). Grafana now shows real cost / latency / agreement comparisons across models on identical inputs.

### Why

The original "local heuristic vs cloud LLM" framing wasn't really a comparison — a Python parser and an LLM aren't comparable tools. The proposal originally called for "GPT-4o vs Claude" as the headline comparison; this release makes that the actual design.

### Added

- **Bake-off runner**: `run_source()` fans out a single snapshot to every configured model in parallel (`asyncio.gather`). Each model's call becomes its own row in the `runs` table.
- `sources.primary_model` and `sources.comparison_models[]` columns. Old `sources.model` removed.
- `runs.is_primary` (BOOL) and `runs.agreement` (Jaccard with primary) columns.
- `webharvest_agreement_jaccard{source_id, primary, challenger}` Prometheus gauge.
- New Grafana panels: cost-by-model, escalation-rate-by-model, tokens-by-model, latency-by-model, agreement-vs-primary, total spend, spend-by-model.
- Dashboard React UI: snapshot-grouped runs table; model dropdown + challenger checkboxes on the source form; per-row "primary" / "challenger" badge; agreement column.
- Four supported model slugs with per-model pricing in `extracto/src/anthropicClient.js`: `anthropic/claude-sonnet-4`, `openai/gpt-4o`, `meta-llama/llama-3.3-70b-instruct`, `google/gemini-2.0-flash-001`.

### Changed

- `extracto` now accepts `model` per request body and labels every metric by model.
- The "backend" string recorded in `runs.backend` is now the model slug (or "heuristic" historically) — Grafana groups everything by it.
- Integration test rewritten for bake-off: uses gemini-flash + llama-70b on the JSON-LD and table fixtures (sub-cent per run). Now requires `OPENROUTER_API_KEY` in CI as a GitHub Secret.

### Removed

- `services/heuristic/` and the runtime routing through it. The directory is gone; bringing the heuristic back as a ground-truth oracle on JSON-LD pages remains an option but is unimplemented.
- `LOCAL_MODEL_URL`, `HEURISTIC_URL`, `LOCAL_CONFIDENCE_THRESHOLD`, `HEURISTIC_CONFIDENCE_THRESHOLD` env vars (no routing tier left to gate).
- The CI lane for the heuristic Python service.

### Migration note

The `0.2.0` schema is incompatible with `0.1.x`. Wipe with `docker compose down -v` before bringing up.

## [0.1.0] — initial release

### Added

- Five-service architecture in Docker Compose: scraper (Python/FastAPI), extracto (Node/Express + Anthropic SDK), local-model (Python heuristic extractor), dashboard (Vite + React), Postgres.
- Local-first extraction with confidence-based escalation to the cloud LLM. Threshold tunable via `LOCAL_CONFIDENCE_THRESHOLD`.
- Identity-keyed entity diffing with stale-not-deleted semantics. HTML snapshots persisted so schemas can be re-applied without re-fetching.
- Prometheus metrics on every service. Auto-provisioned Grafana dashboard.
- GitHub Actions workflows: `ci.yml`, `integration.yml`, `security.yml` (Trivy + pip-audit + npm audit + gitleaks), `deploy.yml` (GHCR + SLSA + SBOM on `v*` tags).
- Dependabot for actions, pip, npm, Docker base images.
- Documentation: README, ARCHITECTURE, EVALUATION, SWOT_ANALYSIS.

### Scope simplifications vs. the original spec

- Distilled local model replaced by a heuristic extractor (since superseded — see 0.2.0).
- Web search step for source discovery omitted; sources are added by URL.
- Wayback Machine training pipeline out of scope.

[Unreleased]: https://github.com/ichanner/project-final/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ichanner/project-final/releases/tag/v0.2.0
[0.1.0]: https://github.com/ichanner/project-final/releases/tag/v0.1.0
