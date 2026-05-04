from src.metrics import fetch_requests_total, fetch_total, run_entities


def test_metrics_have_expected_labels():
    fetch_total.labels(source_id="1", outcome="ok").inc()
    fetch_requests_total.labels(
        source_id="1", mode="conditional", result="not_modified",
    ).inc()
    run_entities.labels(source_id="1", change="new").inc(3)

    samples = list(fetch_total.collect())
    assert samples, "fetch_total should expose samples"
    assert any(
        s.labels.get("source_id") == "1" and s.labels.get("outcome") == "ok"
        for fam in samples
        for s in fam.samples
    )

    conditional_samples = list(fetch_requests_total.collect())
    assert any(
        s.labels.get("mode") == "conditional"
        and s.labels.get("result") == "not_modified"
        for fam in conditional_samples
        for s in fam.samples
    )
