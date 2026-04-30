from prometheus_client import Counter, Gauge, Histogram

fetch_total = Counter(
    "webharvest_scraper_fetch_total",
    "Total fetches by outcome",
    ["source_id", "outcome"],
)

fetch_duration = Histogram(
    "webharvest_scraper_fetch_duration_seconds",
    "Fetch duration in seconds",
    ["source_id"],
)

run_entities = Counter(
    "webharvest_scraper_run_entities_total",
    "Entities written by primary run, by change type",
    ["source_id", "change"],
)

run_confidence = Histogram(
    "webharvest_scraper_run_confidence",
    "Run confidence score distribution, by model",
    ["source_id", "backend"],
    buckets=(0.1, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0),
)

run_cost_usd = Counter(
    "webharvest_scraper_cost_usd_total",
    "Estimated cost per source in USD, by model",
    ["source_id", "backend"],
)

# Kept for back-compat with the existing dashboard panel; counts every
# challenger run that fired (since every challenger is by definition not the
# user's chosen primary backend, "escalation" is loose but useful as a
# challenger-call-count).
escalations_total = Counter(
    "webharvest_scraper_escalations_total",
    "Challenger model invocations, by model",
    ["source_id", "model"],
)

agreement_jaccard = Gauge(
    "webharvest_agreement_jaccard",
    "Jaccard agreement on identity-keys between primary and challenger models",
    ["source_id", "primary", "challenger"],
)

field_agreement = Gauge(
    "webharvest_field_agreement",
    "Per-field agreement: fraction of co-extracted entities where this field's "
    "value equals the primary model's value",
    ["source_id", "primary", "challenger", "field"],
)

# Per-field change counter — increments every time an entity's field value
# changes from one run to the next. Cardinality is bounded by schema field
# count per source (typically <10).
field_changes_total = Counter(
    "webharvest_field_changes_total",
    "Entity field-value changes detected by the diff",
    ["source_id", "field"],
)

# Cached-anchor fast-path metrics. Hit = BS4 produced valid entities, no LLM
# called. Miss = anchors invalid (or absent) — fell back to LLM bake-off.
fast_path_total = Counter(
    "webharvest_fast_path_total",
    "Fast-path attempts using cached DOM anchors (no LLM called)",
    ["source_id", "outcome"],  # outcome = hit | miss
)
fast_path_duration = Histogram(
    "webharvest_fast_path_duration_seconds",
    "BeautifulSoup extraction wall-clock for cached-anchor polls",
    ["source_id"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)
