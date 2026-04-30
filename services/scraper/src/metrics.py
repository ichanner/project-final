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
    "Entities written by run, by change type",
    ["source_id", "change"],
)

run_confidence = Histogram(
    "webharvest_scraper_run_confidence",
    "Run confidence score distribution",
    ["source_id", "backend"],
    buckets=(0.1, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0),
)

run_cost_usd = Counter(
    "webharvest_scraper_cost_usd_total",
    "Estimated cost per source in USD",
    ["source_id", "backend"],
)

backend_in_use = Gauge(
    "webharvest_scraper_backend_in_use",
    "1 if a run is currently using this backend",
    ["backend"],
)

escalations_total = Counter(
    "webharvest_scraper_escalations_total",
    "Local-model escalations to cloud",
    ["source_id"],
)
