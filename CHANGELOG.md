# Changelog

All notable changes to WebHarvest are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] — initial release

### Added

- Five-service architecture in Docker Compose: scraper (Python/FastAPI), extracto (Node/Express + Anthropic SDK), local-model (Python heuristic extractor), dashboard (Vite + React), Postgres.
- Local-first extraction with confidence-based escalation to the cloud LLM. Threshold tunable via `LOCAL_CONFIDENCE_THRESHOLD`.
- Identity-keyed entity diffing with stale-not-deleted semantics. HTML snapshots persisted so schemas can be re-applied without re-fetching.
- Prometheus metrics on every service. Auto-provisioned Grafana dashboard with the panels called out in the project spec (fetch outcomes, entity changes, confidence distribution, cost, escalation rate, latency comparisons).
- GitHub Actions workflows:
  - `ci.yml` — ruff/eslint, pytest/node-test, Vite build, then `docker compose build` as a smoke gate.
  - `integration.yml` — brings the full stack up with a fixture nginx and exercises an end-to-end create/run/re-run cycle on two distinct fixture types (JSON-LD and HTML table).
  - `security.yml` — Trivy on every built image (SARIF to Code Scanning, CycloneDX SBOMs as artifacts), Trivy filesystem scan, pip-audit, npm audit, gitleaks. Runs on PR, push, and weekly schedule.
  - `deploy.yml` — on `v*` tags, builds and pushes all four service images to GHCR with SLSA provenance and SBOM attestations, then opens a GitHub Release with auto-generated notes.
- Dependabot configuration for actions, pip, npm, and Docker base images (weekly).
- Documentation: `README.md`, `ARCHITECTURE.md`, `EVALUATION.md`, `SWOT_ANALYSIS.md`.

### Scope simplifications vs. original spec

- Distilled local model replaced by a heuristic extractor (JSON-LD, microdata, table parsing, repeating-card detection) behind the same API a distilled model would expose. Escalation, confidence scoring, and Grafana panels all still work.
- Web search step for source discovery omitted; sources are added by URL.
- Wayback Machine training pipeline out of scope.

[Unreleased]: https://github.com/USER/web-harvest/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/USER/web-harvest/releases/tag/v0.1.0
