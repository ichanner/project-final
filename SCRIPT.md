================================================================
WebHarvest — DevOps Final Project Demo
================================================================

A 7-container web data extraction system with an operational
Grafana dashboard. The unique architectural angle: the LLM is
called ONCE per source to derive a CSS-selector recipe, then
BeautifulSoup uses that recipe forever.


================================================================
SCENE 1 — Environment: docker compose + github actions + security
================================================================

Show: the whole stack comes up with one command.
Show: 3 GitHub workflows green on main (CI, Security, Integration).
Show: Security workflow's individual jobs (pip-audit, npm audit,
      trivy on each image, gitleaks).


================================================================
SCENE 2 — Sample app: add a source, watch LLM anchor ONCE
================================================================

Show: React UI with the 3 pre-warmed sources already running.
Show: clicking a preset, hitting Add+Run for a 4th source.
Show: first run takes 5-15s (LLM is anchoring).
Show: second run takes ~100ms (BS4 fast-path on cached recipe).
Show: clicking "recipe" → the cached anchor JSON the LLM produced.


================================================================
SCENE 3 — The unique angle: LLM bootstrap + BS4 fast path
================================================================

Show: clicking "recipe" on an anchored source — the JSON.
Show: clicking "Inspect" on a DeFi entity — sparkline of price drift.
Show: this is what's running on every cron tick — pure Python on
      cached selectors. Zero LLM calls.


================================================================
SCENE 4 — Operational dashboard: 6 rows, RED method
================================================================

Walk through the dashboard top to bottom:
  Row 1 STATUS         — services up/down, active alerts count
  Row 2 RATE           — polls/min global + per source
  Row 3 ERRORS         — fetch failures by class, consecutive
                         failures per source, cron skips
  Row 4 DURATION       — poll p50/p95, fast-path latency
  Row 5 ACTIVITY       — field changes, entity transitions,
                         entity counts, polls by path
  Row 6 DIAGNOSTIC     — firing alerts table + recent failures
                         + recent runs (Postgres-backed)

Show: alerts page → AnchorBreakage rule with remediation annotation.


================================================================
SCENE 5 — SWOT: 6 evidence-backed sections
================================================================

Show: SWOT_ANALYSIS.md → 6 sections (Compose, Postgres, Worker,
      Prometheus+Grafana, GitHub Actions, LLM bootstrap).
Each section ends with an "Evidence." line pointing to specific
metrics, files, panels, or DB rows from the running system.

================================================================
END
================================================================
