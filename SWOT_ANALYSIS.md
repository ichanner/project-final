# SWOT Analysis

A modified SWOT for every major tool chosen for WebHarvest. Each block frames the tool against the realistic alternatives that were on the table — Strengths and Weaknesses are intrinsic; Opportunities are roads not yet taken; Threats are things that could break the choice.

---

## 1. Claude Sonnet 4.6 (cloud LLM extractor)

**Considered alternatives:** GPT-4o, Claude Haiku 4.5, Llama 3 70B via a hosted endpoint, fine-tuned T5 / BART for extraction.

| | |
|---|---|
| **Strengths** | Long-context window (1M tokens) tolerates raw HTML pages without chunking; structured outputs (`output_config.format`) guarantee schema-conformant JSON without prefill or regex parsing; prompt caching on the system prompt amortizes the per-request prefix over many extractions; SDK exposes a typed `usage` object that the cost metric reads directly. |
| **Weaknesses** | Per-token pricing scales linearly with HTML size; 50 KB pages can be 15K+ input tokens at $3/M; the model is a black box — failure modes are non-debuggable beyond reading the response; round-trip latency adds 2–10 s per extraction. |
| **Opportunities** | Extended thinking (`thinking: {type: "adaptive"}`) for ambiguous pages; batch API at 50% cost for non-urgent overnight rescans; using `count_tokens` to gate the call when the page is unusually large. |
| **Threats** | Model deprecation (90-day window once announced); pricing changes; rate-limit tier collapse if the workload grows past current bucket; data-handling concerns if any source page contains PII. |

---

## 2. Local heuristic extractor (Python + BeautifulSoup + lxml)

**Considered alternatives:** A distilled small LLM running on llama.cpp (the original spec), a fine-tuned encoder model, regex-only.

| | |
|---|---|
| **Strengths** | Zero per-call cost; sub-100 ms latency; no external dependency; exposes the same API shape a real distilled model would, so swapping it in later is a one-service change; deterministic and easy to test; the JSON-LD path catches sites that publish `schema.org` data even when their visible HTML changes. |
| **Weaknesses** | No semantic understanding — confidence is heuristic, not learned, so it can be over- or under-confident on edge cases; the cards heuristic is brittle (depends on shared CSS classes); doesn't generalize to single-entity pages. |
| **Opportunities** | Train a real distilled model on extraction pairs the cloud generates (the original spec) — the architecture already supports it; add microdata/RDFa parsing alongside JSON-LD; add a per-source learned threshold instead of the global `LOCAL_CONFIDENCE_THRESHOLD`. |
| **Threats** | Sites moving to client-side rendering (the static HTML has no data); JSON-LD becoming less common as sites move to JS frameworks; over-reliance — high-confidence wrong extractions are worse than low-confidence escalations. |

---

## 3. Semantic anchoring (vs. CSS / XPath selectors)

**Considered alternative:** Traditional scraping with CSS selectors or XPath stored per source.

| | |
|---|---|
| **Strengths** | Survives full site redesigns as long as the data still exists somewhere on the page; humans write the anchor, not a brittle DOM path; one anchor works across paginated variants of the same page type; encodes intent ("the rate filings table") rather than implementation. |
| **Weaknesses** | Adds a model dependency to what was a pure HTTP+parse pipeline; latency goes up and cost is non-zero; debugging a "why didn't it find it" failure requires reading model output, not a stack trace; can pick up unintended regions if the page has multiple table-like structures. |
| **Opportunities** | Build an internal eval harness with Wayback Machine snapshots to measure actual resilience to redesigns (the original spec); cache anchors per source after the first extraction so subsequent calls are cheaper. |
| **Threats** | A site adding an *additional* matching region (e.g., a "related filings" sidebar) silently broadens extraction; sites that intentionally obfuscate (anti-scraping) can defeat semantic matching just as well as selectors. |

---

## 4. Docker Compose (vs. Kubernetes / bare processes)

**Considered alternatives:** Kubernetes (kind / k3s), systemd units, Nomad.

| | |
|---|---|
| **Strengths** | One file describes the whole topology; healthcheck-gated `depends_on` removes startup-order race bugs; `docker compose up --build` is the entire onboarding story for a contributor; CI can spin up the same stack with one command (the integration job does); mature tooling, no cluster to maintain. |
| **Weaknesses** | Single-host only; no rolling updates; no autoscaling; secrets handling is just env vars; no built-in service mesh, so cross-service auth is "trust the network." |
| **Opportunities** | The `docker-compose.yml` translates near-mechanically to a Kubernetes Deployment+Service set when scale demands it; `kompose` could automate the first pass; image tags from `deploy.yml` are already structured to support that path. |
| **Threats** | Production-grade requirements (HA, multi-region, autoscaling) force a migration; Docker Inc. licensing changes for Docker Desktop on developer machines (mitigated by Colima / Podman / Lima fallbacks). |

---

## 5. Trivy (vs. Snyk / Grype / Dependabot only)

**Considered alternatives:** Snyk Container, Grype + Syft, Dependabot, GitHub native vulnerability alerts.

| | |
|---|---|
| **Strengths** | Free and open source; one tool covers OS packages, language packages, IaC misconfigurations, secrets, and SBOM generation; native SARIF output integrates with GitHub Code Scanning out of the box; CycloneDX SBOM artifact is one extra step in the same workflow. |
| **Weaknesses** | Vulnerability database lag (hours behind upstream advisories); some advisories are non-actionable (`unfixed`); large image scans can take 1–2 minutes per service; remediation guidance is generic. |
| **Opportunities** | Trivy can scan the Compose file itself for misconfigurations (privileged containers, missing user, etc.) — easy add; running Trivy in pre-commit prevents bad images from ever being pushed; combining with `cosign` to sign images post-scan. |
| **Threats** | A high-severity zero-day reaches the vendor before the DB; a CVE that Trivy considers "unfixed" is silently ignored by the `ignore-unfixed: true` config; commercial alternatives may catch things Trivy misses (depends on workload). |

---

## 6. Prometheus + Grafana (vs. cloud-managed observability)

**Considered alternatives:** Datadog, New Relic, AWS CloudWatch, Loki/Tempo for logs/traces.

| | |
|---|---|
| **Strengths** | Pull model — Prometheus scrapes `/metrics`; services don't need to know about the metrics backend; both tools provision from declarative YAML/JSON, so the dashboard travels with the repo; zero per-metric pricing; the same mental model used in production at scale. |
| **Weaknesses** | Local storage only by default (no long-term retention); no logs or traces in this stack — would require Loki/Tempo for those; alerting requires Alertmanager which isn't wired up; query language (PromQL) has a learning curve. |
| **Opportunities** | Add Alertmanager for "escalation rate spiked" or "cost > $X/hour" alerts; add Loki for structured-log correlation with metrics by `source_id`; add Tempo for distributed traces across scraper → extracto → Anthropic. |
| **Threats** | Local Prometheus storage caps out at ~15 days of data by default — long-term comparison metrics need remote storage (Mimir, Cortex, or a managed service); dashboard JSON drifts from code if changes are made in the UI and not exported. |

---

## 7. GitHub Actions (vs. self-hosted CI)

**Considered alternatives:** Jenkins, GitLab CI, CircleCI, Drone.

| | |
|---|---|
| **Strengths** | Free for public repos; ecosystem of pre-built actions (`docker/build-push-action`, `aquasecurity/trivy-action`, `gitleaks-action`); SARIF integration with the Security tab is one line; GHCR ships in the same product so the deploy workflow has nothing to authenticate to externally; matrix strategy keeps per-service pipelines DRY. |
| **Weaknesses** | Vendor lock-in (workflows are not portable to other CI as-is); minute quotas on private repos; debugging requires re-pushing or `tmate`; secrets handling is workspace-scoped, not per-environment by default. |
| **Opportunities** | Reusable workflows to share between repos; `composite` actions to wrap the scan-and-upload pattern; environments + protection rules for staged deploys. |
| **Threats** | Action supply-chain compromise (mitigated in this repo by `.github/dependabot.yml`, which proposes weekly updates for actions, pip, npm, and Docker base images; SHA-pinning every third-party action is the next level of hardening); GitHub outage stalls every PR; pricing changes for private repos. |
