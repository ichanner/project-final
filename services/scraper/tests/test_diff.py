from src.diff import identity_for


def test_identity_uses_keys_in_order():
    e = {"company": "Acme", "filing_date": "2024-01-15", "rate_change": "1.2"}
    assert identity_for(e, ["company", "filing_date"]) == "Acme||2024-01-15"


def test_identity_handles_missing_field():
    e = {"company": "Acme"}
    assert identity_for(e, ["company", "filing_date"]) == "Acme||"


def test_identity_falls_back_to_full_entity_when_no_key():
    e = {"a": 1, "b": 2}
    assert identity_for(e, []) == '{"a": 1, "b": 2}'


def test_identity_strips_whitespace():
    e = {"company": "  Acme  ", "filing_date": "  2024  "}
    assert identity_for(e, ["company", "filing_date"]) == "Acme||2024"
