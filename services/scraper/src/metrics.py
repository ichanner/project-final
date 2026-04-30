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
