"""Prometheus metrics for the scraper + worker processes.

Naming convention: `webharvest_<area>_<measurement>` where area is one of
{scraper, fetch, poll, anchor, field, fast_path, cost, entity}. Histogram
metrics use `_seconds` or `_bytes` suffix per Prometheus conventions.

The metric layer is organized around the project's three claims:

  1. STABILITY  — anchors actually last (anchor_age, anchor_re_anchor_total,
                  anchor_extraction_count, anchor_field_population_ratio,
                  anchor_phantom_update_ratio)
  2. ECONOMY    — sustained cost is zero (cost_saved_usd_total against
                  scraper_cost_usd_total which captures actual spend)
  3. SELF-AWARENESS — the system surfaces its own failure modes (the fetch_*
                  family + poll_total, polls_skipped_total)

The final DevOps comparison is polling strategy: naive full fetches vs
conditional HTTP polling.
"""

from prometheus_client import Counter, Gauge, Histogram

fetch_total = Counter(
    "webharvest_scraper_fetch_total",
    "Legacy: total fetches by binary outcome. New code should reference "
    "webharvest_fetch_total which carries the HTTP status_code label.",
    ["source_id", "outcome"],
)

fetch_duration = Histogram(
    "webharvest_scraper_fetch_duration_seconds",
    "Fetch wall-clock duration in seconds (network round-trip — measures "
    "the open internet, not webharvest)",
    ["source_id"],
)

run_entities = Counter(
    "webharvest_scraper_run_entities_total",
    "Entities written by primary run, by change type (new/updated/stale)",
    ["source_id", "change"],
)

run_cost_usd = Counter(
    "webharvest_scraper_cost_usd_total",
    "Actual spend per source in USD, by model. Stays at 0 once a source is "
    "anchored and steady-state polling kicks in.",
    ["source_id", "backend"],
)

field_changes_total = Counter(
    "webharvest_field_changes_total",
    "Entity field-value changes detected by the diff. Cardinality bounded "
    "by per-source schema field count.",
    ["source_id", "field"],
)

fast_path_total = Counter(
    "webharvest_fast_path_total",
    "Fast-path (cached-anchor BS4) attempts. outcome=hit when BS4 produced "
    "entities, miss when anchors broke.",
    ["source_id", "outcome"],
)

fast_path_duration = Histogram(
    "webharvest_fast_path_duration_seconds",
    "BeautifulSoup extraction wall-clock for cached-anchor polls. ~50-100ms "
    "for typical pages.",
    ["source_id"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)

fetch_status_total = Counter(
    "webharvest_fetch_total",
    "HTTP status code distribution per source. status_code=0 represents "
    "network/transport failures (timeout, dns, refused, ssl).",
    ["source_id", "status_code"],
)

fetch_errors_total = Counter(
    "webharvest_fetch_errors_total",
    "Fetch errors classified by failure mode: timeout / dns / "
    "connection_refused / ssl / protocol / http_4xx / http_5xx / anti_bot.",
    ["source_id", "error_class"],
)

fetch_consecutive_failures = Gauge(
    "webharvest_fetch_consecutive_failures",
    "Consecutive failed fetches for a source. Resets to 0 on the next "
    "successful (200, non-anti-bot) fetch. Climbing values mean a source "
    "is going dark and likely needs operator attention.",
    ["source_id"],
)

fetch_response_size_bytes = Histogram(
    "webharvest_fetch_response_size_bytes",
    "Response body size in bytes. Anomalous drops (e.g. a 200KB page "
    "suddenly returning 5KB) typically indicate an anti-bot interstitial "
    "or a redirect to a login screen.",
    ["source_id"],
    buckets=(1_000, 5_000, 10_000, 50_000, 100_000, 250_000, 500_000,
             1_000_000, 2_500_000, 5_000_000),
)

fetch_redirect_count = Histogram(
    "webharvest_fetch_redirect_count",
    "Number of HTTP redirects followed during a fetch. Spikes indicate a "
    "redirect chain — often the symptom of an auth wall or geo-fence.",
    ["source_id"],
    buckets=(0, 1, 2, 3, 5, 10),
)

fetch_requests_total = Counter(
    "webharvest_fetch_requests_total",
    "Fetch attempts by polling strategy. mode=naive sends no validators; "
    "mode=conditional sends ETag/Last-Modified validators when available. "
    "result=modified means full body returned, not_modified means HTTP 304, "
    "unsupported means the source returned 2xx without reusable validators.",
    ["source_id", "mode", "result"],
)

fetch_bytes_total = Counter(
    "webharvest_fetch_bytes_total",
    "Response body bytes downloaded by polling strategy. 304 responses add 0.",
    ["source_id", "mode"],
)

fetch_bytes_saved_total = Counter(
    "webharvest_fetch_bytes_saved_total",
    "Estimated body bytes not downloaded because conditional polling returned "
    "HTTP 304 Not Modified. Uses the last full response size for the source.",
    ["source_id"],
)

poll_total = Counter(
    "webharvest_poll_total",
    "Polls executed, labeled by which operational path was taken. "
    "path=conditional_skip means HTTP 304 skipped extraction. "
    "path=dom_fast_path means cached anchors + BS4 (no LLM call). "
    "path=llm_anchor means first-run or manual re-anchor called the LLM.",
    ["source_id", "path"],
)

poll_duration = Histogram(
    "webharvest_poll_duration_seconds",
    "End-to-end pipeline time per poll (fetch + extract + diff + persist). "
    "Conditional skips are tiny, DOM fast-path polls are typically <1s, "
    "LLM anchoring polls are 5-30s depending "
    "on model and HTML size.",
    ["source_id"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

polls_skipped_total = Counter(
    "webharvest_polls_skipped_total",
    "Scheduled poll firings that did NOT execute. reason=misfire (scheduler "
    "drift), max_instances_blocked (previous run still in flight).",
    ["source_id", "reason"],
)

extractions_skipped_total = Counter(
    "webharvest_extractions_skipped_total",
    "Extraction pipeline skips. reason=http_304 means conditional HTTP polling "
    "proved the source was unchanged, so BS4/LLM/diff work was skipped.",
    ["source_id", "reason"],
)

anchor_age_seconds = Gauge(
    "webharvest_anchor_age_seconds",
    "Age of a source's cached anchor recipe in seconds (now() - "
    "last_anchored_at). Climbs monotonically per source until a re-anchor "
    "event resets it. The headline stability metric.",
    ["source_id"],
)

anchor_re_anchor_total = Counter(
    "webharvest_anchor_re_anchor_total",
    "Times a source's anchors were regenerated via the LLM anchoring path. "
    "reason=initial (first run, no prior anchors), re_anchor (operator "
    "invoked or anchors got cleared by a schema change).",
    ["source_id", "reason"],
)

anchor_extraction_count = Gauge(
    "webharvest_anchor_extraction_count",
    "Number of entities the most recent fast-path BS4 extraction produced. "
    "Compared against a 1h moving average to detect anchor degradation "
    "(see AnchorBreakage alert).",
    ["source_id"],
)

anchor_field_population_ratio = Gauge(
    "webharvest_anchor_field_population_ratio",
    "Fraction of rows in the most recent fast-path extraction where this "
    "specific field was populated (non-null, non-empty). A drop below ~0.5 "
    "for a previously-stable field means that one field's selector broke "
    "even though the root selector still works.",
    ["source_id", "field"],
)

anchor_phantom_update_ratio = Gauge(
    "webharvest_anchor_phantom_update_ratio",
    "Fraction of rows in the most recent diff that were rejected because "
    "their anchor-field values disagreed with the stored entity's anchors. "
    "Elevated values mean the recipe is binding rows to the wrong DOM "
    "positions — degradation in progress even if extraction count looks ok.",
    ["source_id"],
)

cost_saved_usd_total = Counter(
    "webharvest_cost_saved_usd_total",
    "Cumulative USD that an LLM-every-poll baseline would have charged on "
    "each fast-path hit if we'd called it instead of using cached anchors. The opportunity "
    "cost the architecture avoids. See pricing.estimate_extraction_cost.",
    ["source_id"],
)
