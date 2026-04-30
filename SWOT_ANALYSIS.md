# Modified SWOT

One engineer's take on every tool the project actually picked, written in the order of "what's load-bearing" rather than alphabetical. Sections vary in length depending on what's interesting — some tools deserve a paragraph, some deserve five.

The "modified" in modified SWOT means I'm being concrete instead of abstract. Each item references something the project does, hits, or breaks on.

---

## 1. Anthropic Claude Sonnet 4 (default primary model)

**Strengths.** Best structured-output discipline of the four models I tested. With strict json_schema enabled, it returns clean JSON the largest fraction of the time — the parser fallback (markdown fence stripping, alternate-key alias-matching) fires least often for Claude in extracto's logs. The recipe Claude produces for Wikipedia anchors cleanly and persists indefinitely. Confidence scoring is roughly calibrated — a 0.95 from Claude is more trustworthy than a 0.95 from llama.

**Weaknesses.** Most expensive of the four. $3 input / $15 output per million. A 50KB page is around 15K input tokens and one extract is ~$0.05-0.15 with the entity sample we ask for. I drained $14 of credits twice during the build because of every-minute crons firing on Claude. Latency is in the middle — not slow, but gpt-4o eats it for breakfast.

**Opportunities.** Prompt caching is supported through OpenRouter for Claude — cache the system prompt across calls and amortize the prefix cost. Not wired up; would cut per-call cost by ~40% on warm sources. The 1M-token context window means we could lift the 300KB HTML cap considerably for sources that need it.

**Threats.** Model deprecation cycle is short (~90 day notice once announced). Pricing has moved twice in the last year. The "primary" choice is sticky — if Claude's anchoring style stops working on a redesigned page, every source built against it has to re-anchor against a new model.

---

## 2. OpenAI GPT-4o (cheap-but-equal challenger)

**Strengths.** Fastest in my tests — ~4-5s wall-clock for HN, vs ~12-15s for Claude. ~30% cheaper than Claude per token. Cleanest JSON output of all four — strict mode actually behaves and the parser fallback rarely has to fire. On the HN bake-off, agreement with Claude was 1.00 — same set of titles, both right.

**Weaknesses.** Self-reported confidence pegs at 1.0 too often, including on pages where it shouldn't be that sure. Useful for cost-tracking, useless for routing decisions. Long-context handling is OK but it's not the headline feature it is for Claude — on 200KB+ pages the 300KB content cap matters more here than for Claude.

**Opportunities.** If gpt-4o keeps matching Claude on agreement at lower cost, it's the obvious default primary on a cost basis. I started with Claude as default for caution; the data from the bake-off says gpt-4o would be the smarter pick for most pages.

**Threats.** OpenAI's API rate limits get tight without a paid plan. OpenRouter wraps the auth but you still hit the same upstream ceiling under heavy load. Pricing changes 1-2× a year.

---

## 3. Meta Llama 3.3 70B Instruct (budget challenger)

**Strengths.** ~25-30× cheaper than Claude on input tokens, ~38× on output. For sources where you don't need the gold-standard answer, this is the obvious choice. Open weights — multiple OpenRouter providers serve it, so you can shop on cost or latency without changing model. Reasonable structured-output support; my regex fallback fires more for llama than for Claude/gpt-4o but still parses correctly.

**Weaknesses.** Slowest of the four (~15s on HN-sized pages). Set-level agreement drops to 0.79 vs Claude on HN — that's "missed 1 of 30 entities" or "hallucinated 1." Visible immediately in the agreement matrix panel. Without field-level diff we don't know which.

**Opportunities.** A cost-tier cascade — try llama first, fall back to Claude only when llama's confidence drops below threshold — would collapse 80% of cloud cost. Not built. The bake-off pattern made the data case for it before I'd have thought to design it.

**Threats.** Open-weights models drift in capability with newer releases. llama-3.4 or llama-4 will silently change behavior if you bump the slug. Self-host pressure: if cost matters enough that llama is your primary, the next step is running it yourself, at which point you're back to operational complexity (GPUs, KV caches, batching).

---

## 4. Google Gemini 2.0 Flash (cheapest, fastest, flakiest)

**Strengths.** Cheapest of the four — $0.10 / $0.40 per million tokens. Roughly comparable to gpt-4o on speed. When it works, agreement with Claude was 1.00 in my testing. Google's strict json mode is real; clean output most of the time.

**Weaknesses.** Aggressive rate-limiting on OpenRouter's free/low-credit tier. Maybe half of my runs against gemini during the build came back as `429 Provider returned error`. The dashboard's "challenger errored" pattern was almost always gemini.

**Opportunities.** With a paid OpenRouter account or BYOK key, the rate-limit issue goes away — gemini becomes the most attractive default primary on cost-per-correct-extraction. The metrics already break down by model, so any "cost per successful extraction" panel works without code changes.

**Threats.** Google has changed the structured-output API surface twice in the last year. The current `response_format: json_schema, strict: true` shape works today; the parser fallback exists because *some* day a provider will silently change it.

---

## 5. OpenRouter (the gateway)

**Strengths.** One API, one auth, one pricing endpoint, four (or four hundred) models behind it. Swapping models is a string change. The OpenAI-compatible request shape means I get to use the OpenAI SDK without writing four separate clients. Cost per call is reported on every response (`usage.cost`), so the cost metric reads it directly without my own pricing math being load-bearing.

**Weaknesses.** Single point of failure. When OpenRouter has a bad ten minutes, every model dies together. Their per-provider rate limits are inherited — if Anthropic rate-limits the OpenRouter pool, your "Claude" requests fail even if you paid OpenRouter directly.

**Opportunities.** Provider routing can be specified per-request (`provider: {order: ["Anthropic", "Bedrock"]}`). Not wired up in this project. Would let you prefer the cheapest provider for each model at request time.

**Threats.** Providers can leave the platform. Pricing moves. The strict-mode-respecting providers I tested today might not respect it tomorrow if upstream changes.

---

## 6. The model bake-off pattern

**Strengths.** Honest comparison: same input, same prompt, same schema, four models, every metric observable. The agreement metric makes "is this model trustworthy" answerable from a Grafana panel rather than from a benchmarking spreadsheet that goes stale within a quarter. Easy to A/B model swaps — change `sources.primary_model` and the next run produces a fresh comparison automatically.

**Weaknesses.** 4× cloud cost per run. Latency bound by the slowest model (parallel fan-out, but the run isn't done until the last response is in). Set-level agreement is Jaccard on identity-keys — coarse. Field-level agreement is better but only computed on the entity intersection, so models that disagreed on the *set* don't get compared on individual fields where both saw the same row.

**Opportunities.** Sample-mode bake-off: only run challengers every Nth poll (e.g., 1 in 10), reducing cost by 90% while still tracking model drift. Not implemented. The dashboards would still work — same metric labels, less data per row.

**Threats.** Prompt drift. The system prompt is identical across models, but it was tuned (consciously or otherwise) on whichever model I tested first. A prompt that's accidentally Claude-flavored will systematically advantage Claude in the comparison.

---

## 7. LLM-bootstrapped DOM anchoring + BeautifulSoup fast-path

This is the architectural choice that makes the project a tool rather than a demo.

**Strengths.** Cost discipline is enforced architecturally. Once a source has anchors, scheduled cron polls physically cannot call the LLM — that's a control-flow guarantee in `runner.py`, not a guideline. When the runs table shows `cost_usd=0.0000`, you know the LLM wasn't called. The Wikipedia source polled 113 entities/minute for $0 indefinitely. The DeFi source polled 1,526 entities/minute for $0. Latency dropped from ~15s (LLM) to ~50-100ms (BS4). The fast-path hit rate becomes a per-source SLI.

**Weaknesses.** CSS selectors are brittle to redesigns. The LLM happily produces a `table.wikitable.sortable tbody tr` that matches *every* sortable wikitable on the page, not just the one we want — that was the row-binding flicker that ate two hours of debugging. First-occurrence dedup fixed it, but the underlying fragility is structural: CSS doesn't have "the table with this header" as a primitive.

**Opportunities.** Content-anchored matching as a fallback when CSS breaks. The infrastructure for this is partly there — `entity_changes` already tracks per-field history; finding "Apple Inc." in a redesigned page by text is one BS4 walk away. Periodic LLM audit (every Nth fast-path run) to detect drift early.

**Threats.** SPAs without server-rendered data. blockchain.com worked because Claude lucked into anchoring a hydration JSON blob — that's not robust. A more honest production version would detect when the static HTML doesn't contain the data and surface a clear "needs JS rendering" warning instead of silently returning 0 entities.

---

## 8. Volatile / anchor field roles

**Strengths.** The single biggest quality-of-life win in the project. Before: every poll on Wikipedia showed 18-20 "updated" entities — phantom drift from BS4 binding noise on stable fields. After: 0 false-positive updates across six consecutive polls. The Grafana drift panels and the EntityFieldDrift alert went from "drowning in noise" to "shows real change only." Generalizes way past web scraping — anywhere you're tracking how a collection drifts over time, the split applies.

**Weaknesses.** Picking the right role per field requires the user to know the domain. A field they don't think to mark stays anchor and silently absorbs noise — but might mask real change they'd want to know about (e.g., a typo in a stable name field).

**Opportunities.** Auto-classify roles by observing two snapshots. If a field's value differs between back-to-back scrapes of the same source, it's volatile by inference. Could even surface this in the schema-builder UI: "you marked 5 fields anchor; based on observation, `rank` is changing — should I flip it volatile?"

**Threats.** None real. The pattern is small enough and self-contained enough that I'd be surprised if it broke.

---

## 9. APScheduler in a separate worker container

**Strengths.** Real DevOps API/worker split. The scraper API handles HTTP requests; the worker reconciles `sources.refresh_cron` from Postgres every 30s and owns its own scheduler. Same image, different `command:` in compose — the operational pattern is exactly what production deployments do. UI cron edits propagate to the worker within 30s without inter-process notification or restart.

**Weaknesses.** The 30s reconciliation lag is real. If a user clicks "every 1m" and the next minute boundary hits before the worker reconciled, they'll wonder if anything happened. Not a functional bug; just a user-experience nick.

**Opportunities.** The worker doesn't currently expose `/metrics`. Adding `prometheus_client` and a `webharvest_worker_jobs` gauge would let Grafana show "scheduled jobs by source" and catch reconciliation bugs visually.

**Threats.** Single worker = single point of failure for scheduling. If the worker crashes, scheduled polls stop until it restarts. Postgres advisory locks would let you run multiple workers safely; not built.

---

## 10. PostgreSQL

**Strengths.** Same database serves three audiences: the scraper writes through its connection pool, the worker reads source config, and Grafana panels execute SQL via the Postgres datasource. The "Most volatile entities" panel runs `SELECT entity_id, count(*) FROM entity_changes WHERE changed_at > now() - interval '1 hour' GROUP BY 1 ORDER BY 2 DESC LIMIT 20` — that's a real DevOps move, using SQL for high-cardinality data Prometheus would collapse.

**Weaknesses.** No partitioning on entity_changes — at 1500 entities × 30 polls/hour × 5 volatile fields = 225K changes/hour for the DeFi source alone. After a few days you'd want a retention policy or table partitioning by `changed_at`. Not built.

**Opportunities.** Switch to TimescaleDB for the change log. The query pattern is "give me the last N hours of changes for entity X" which is exactly what Timescale's hypertables are optimized for.

**Threats.** Schema migration story. The init.sql runs on first boot only; if the schema evolves you need an alembic-style migration tool. Not set up.

---

## 11. Prometheus + Grafana

**Strengths.** Auto-provisioned datasources and dashboards from JSON in the repo. Two dashboards (main + entity-history drilldown). 5 alert rules in `alerts.yml`. The "Field agreement matrix" table panel is a single SQL query against `webharvest_field_agreement` — visible inter-model disagreement on specific fields without me writing a chart library. Postgres datasource alongside Prometheus means I get aggregates (Prom) and high-cardinality drill-down (Postgres) on the same dashboard.

**Weaknesses.** No persistence story for long-term metrics — Prometheus default retention is 15 days. Grafana login is admin/admin out of the box, fine for local but a real footgun if anyone exposes port 3001 to the internet. Worker container doesn't export metrics so its job state isn't visible in Grafana.

**Opportunities.** Alerting in Grafana 11 is real (Slack/email/webhook). I could fire on `webharvest_fast_path_total{outcome="miss"}` exceeding a threshold — that's "anchors are breaking, wake somebody up."

**Threats.** Self-hosted observability becomes a yak-shave at scale (Grafana upgrades, Prometheus storage management, dashboard provisioning bugs that manifest only on cold restarts). For a class project this doesn't matter; for production, Datadog or Honeycomb take over for ~$30/host/month.

---

## 12. Docker Compose

**Strengths.** Single declarative YAML for the whole stack — repo + a docker daemon is everything someone needs to run this locally. Healthcheck-gated dependencies (`condition: service_healthy`) mean services start in the right order without orchestration code. Same compose file is used for `docker compose build` in CI, so what runs locally is what gets imaged for GHCR.

**Weaknesses.** Single-host. No autoscaling, no rolling updates, no multi-AZ failure tolerance. If the worker crashes, Compose restarts it, but there's no graceful re-scheduling — pollings missed during the crash window are missed.

**Opportunities.** Compose v2 has profiles (`docker compose --profile dev up`). I'm using a dev/test split via overlay files (`docker-compose.test.yml`) — could migrate to profiles for cleanliness.

**Threats.** Production deployment story isn't Compose-shaped. If this ships beyond a class demo, you'd rewrite as Helm charts or ECS task definitions. Compose-to-Kubernetes converters exist (`kompose`) but produce mediocre output that needs hand-tuning.

---

## 13. Trivy

**Strengths.** Open-source, no per-seat pricing. Scans built images *and* repo filesystem in one tool. SARIF output integrates with GitHub code scanning — findings show inline with PRs. CycloneDX SBOM generation is a free side-effect; the deploy workflow attaches them to GHCR images.

**Weaknesses.** Vulnerability DB lag — Trivy uses GitHub Security Advisories + NVD, comprehensive but not real-time. A CVE published Tuesday morning might not show in Trivy until evening. False positives on transitive Python deps that are present in `requirements.txt` but never actually imported.

**Opportunities.** `trivy config` does Dockerfile linting (non-root check, etc.). Would prevent regressions on the security baseline I established in the Dockerfiles.

**Threats.** Aqua Security is a commercial company; Trivy is their loss leader. License changes are possible but not predicted; license has been stable since 2019.

---

## 14. GitHub Actions

**Strengths.** Free for public repos. Native integration with the rest of GitHub — issues, code scanning, dependabot, releases. Hosted runners are zero-ops. Reusable workflows compose: ci → integration → security → deploy is four files, each with one clear responsibility.

**Weaknesses.** Hosted runners are slow on Docker builds — `compose build` smoke gate adds 3-4 minutes per CI run. Caching is per-workflow and per-key, so docker layer caches don't share across `security.yml` and `integration.yml`.

**Opportunities.** GitHub Actions on the user's own machine (self-hosted runners) eat the build time problem.

**Threats.** Pricing tier shifts could turn a free workflow into a $100/month one for a large team. Vendor lock-in is real — porting four workflows to GitLab CI or Jenkins is doable but every action reference would need to be replaced.

---

## What I'd add if I had another week

A couple of these came up while writing this doc. Not implemented:

1. **Periodic LLM audit on healthy sources.** Every Nth fast-path run, also run the LLM in shadow mode and compare. If the new candidate anchors agree with current, log it. If they diverge, raise a re-anchor recommendation. Catches drift before selectors break.

2. **Content-anchored matching as fallback.** Find "Apple Inc." in HTML by text rather than by `:nth-child(2)`. More resilient to redesigns. The cross-check logic is already partway there.

3. **Per-source sampling on the bake-off.** Run challengers only every Nth poll. Cost drops 10×, still tracks model drift over time.

The system as it stands does what the rubric asks and a bit more. The pieces I'm proudest of — the role split, the cost-discipline architecture — generalize beyond web scraping, which is the unexpected outcome.
