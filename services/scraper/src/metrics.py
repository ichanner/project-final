"""Prometheus metrics for the scraper + worker processes.

Naming convention: `webharvest_<area>_<measurement>` where area is one of
{scraper, fetch, poll, anchor, field, fast_path}. Histogram metrics use
`_seconds` or `_bytes` suffix per Prometheus conventions.

The metric set is organized around a real operational dashboard built with
the RED method (Rate, Errors, Duration) plus a per-source state surface.
Every metric here drives at least one panel or alert. Anything thesis-flavored
(cumulative cost saved, anchor age, etc.) was dropped — the dashboard answers
"is anything broken?", not "did the architecture pay off?".
"""

from prometheus_client import Counter, Gauge, Histogram

fetch_total = Counter(
    "webharvest_scraper_fetch_total",
    "Fetches by binary outcome. ok = 200 + non-anti-bot, error = transport "
    "failure or HTTP 4xx/5xx or anti-bot interstitial.",
    ["source_id", "outcome"],
)

fetch_duration = Histogram(
    "webharvest_scraper_fetch_duration_seconds",
    "Fetch wall-clock duration in seconds (network round-trip — measures "
    "the open internet, not webharvest).",
    ["source_id"],
)

run_entities = Counter(
    "webharvest_scraper_run_entities_total",
    "Entities written by primary run, by change type (new/updated/stale). "
    "The dashboard's activity-proof signal — proves the diff loop is alive.",
    ["source_id", "change"],
)

run_cost_usd = Counter(
    "webharvest_scraper_cost_usd_total",
    "Actual spend per source in USD, by model. Stays at 0 once a source is "
    "anchored and steady-state polling kicks in. A non-zero rate here means "
    "anchoring is happening — either initial bootstrap or operator-triggered "
    "re-anchor.",
    ["source_id", "backend"],
)

field_changes_total = Counter(
    "webharvest_field_changes_total",
    "Entity field-value changes detected by the diff. Drives the most-active "
    "drift panels and the StaleEntitySpike alert. Cardinality bounded by "
    "per-source schema field count.",
    ["source_id", "field"],
)

fast_path_total = Counter(
    "webharvest_fast_path_total",
    "Fast-path (cached-anchor BS4) attempts. outcome=hit when BS4 produced "
    "entities, miss when anchors broke. Fast-path hit rate is the SLI for "
    "the LLM-bootstrap-then-cache pattern.",
    ["source_id", "outcome"],
)

fast_path_duration = Histogram(
    "webharvest_fast_path_duration_seconds",
    "BeautifulSoup extraction wall-clock for cached-anchor polls. Typically "
    "50-100ms. Spikes mean a large page or a degraded selector retrying.",
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
    "connection_refused / ssl / protocol / http_4xx / http_5xx / anti_bot. "
    "The 'E' in RED for the polling pipeline.",
    ["source_id", "error_class"],
)

fetch_consecutive_failures = Gauge(
    "webharvest_fetch_consecutive_failures",
    "Consecutive failed fetches for a source. Resets to 0 on the next "
    "successful (200, non-anti-bot) fetch. Climbing values mean a source "
    "is going dark and likely needs operator attention. Drives "
    "ConsecutiveFetchFailures alert.",
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

poll_total = Counter(
    "webharvest_poll_total",
    "Polls executed, labeled by which operational path was taken. "
    "path=dom_fast_path means cached anchors + BS4 (no LLM call). "
    "path=llm_anchor means first-run or manual re-anchor called the LLM. "
    "Steady-state operation should be ~100% dom_fast_path.",
    ["source_id", "path"],
)

poll_duration = Histogram(
    "webharvest_poll_duration_seconds",
    "End-to-end pipeline time per poll (fetch + extract + diff + persist). "
    "DOM fast-path polls are typically <1s, LLM anchoring polls are 5-30s "
    "depending on model and HTML size. The 'D' in RED.",
    ["source_id"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

polls_skipped_total = Counter(
    "webharvest_polls_skipped_total",
    "Scheduled poll firings that did NOT execute. reason=misfire (scheduler "
    "drift), max_instances_blocked (previous run still in flight). Either "
    "value being non-zero means the worker is overloaded or stuck.",
    ["source_id", "reason"],
)

anchor_extraction_count = Gauge(
    "webharvest_anchor_extraction_count",
    "Number of entities the most recent fast-path BS4 extraction produced. "
    "Compared against a 1h moving average to detect anchor degradation "
    "(see AnchorBreakage alert). Operational anomaly signal — sudden drops "
    "mean the page DOM changed.",
    ["source_id"],
)

anchor_re_anchor_total = Counter(
    "webharvest_anchor_re_anchor_total",
    "Times a source's anchors were regenerated via the LLM anchoring path. "
    "reason=initial (first run, no prior anchors), re_anchor (operator "
    "invoked or anchors got cleared by a schema change). A non-zero rate "
    "here in steady state means anchors are breaking and being re-derived.",
    ["source_id", "reason"],
)
