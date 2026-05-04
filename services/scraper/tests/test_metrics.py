from src.metrics import (
    fast_path_total,
    fetch_errors_total,
    fetch_total,
    poll_total,
    run_entities,
)


def test_metrics_have_expected_labels():
    fetch_total.labels(source_id="1", outcome="ok").inc()
    fetch_errors_total.labels(source_id="1", error_class="timeout").inc()
    poll_total.labels(source_id="1", path="dom_fast_path").inc()
    fast_path_total.labels(source_id="1", outcome="hit").inc()
    run_entities.labels(source_id="1", change="new").inc(3)

    fetch_samples = list(fetch_total.collect())
    assert fetch_samples, "fetch_total should expose samples"
    assert any(
        s.labels.get("source_id") == "1" and s.labels.get("outcome") == "ok"
        for fam in fetch_samples
        for s in fam.samples
    )

    poll_samples = list(poll_total.collect())
    assert any(
        s.labels.get("path") == "dom_fast_path"
        for fam in poll_samples
        for s in fam.samples
    )

    error_samples = list(fetch_errors_total.collect())
    assert any(
        s.labels.get("error_class") == "timeout"
        for fam in error_samples
        for s in fam.samples
    )
