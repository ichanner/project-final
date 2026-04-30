from src.heuristics import extract

TABLE_HTML = """
<html><body>
<h1>Filings</h1>
<table>
  <thead><tr><th>Company</th><th>Date</th><th>Change</th></tr></thead>
  <tbody>
    <tr><td>Acme</td><td>2024-01-15</td><td>1.2%</td></tr>
    <tr><td>Initech</td><td>2024-01-20</td><td>-0.4%</td></tr>
    <tr><td>Hooli</td><td>2024-02-01</td><td>3.1%</td></tr>
  </tbody>
</table>
</body></html>
"""


JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
[
  {"@type": "Product", "name": "Foo", "price": "9.99"},
  {"@type": "Product", "name": "Bar", "price": "19.99"}
]
</script>
</head><body><p>page</p></body></html>
"""


EMPTY_HTML = "<html><body><p>nothing here</p></body></html>"


def test_table_extraction_returns_schema_coerced_rows():
    result = extract(TABLE_HTML, {"fields": {"Company": "string", "Change": "string"}}, None)
    assert result["backend"] == "local"
    assert result["source"] == "table"
    assert len(result["entities"]) == 3
    assert result["entities"][0] == {"Company": "Acme", "Change": "1.2%"}
    assert result["confidence"] > 0.5


def test_jsonld_preferred_when_available():
    result = extract(JSONLD_HTML, {"fields": {"name": "string", "price": "string"}}, None)
    assert result["source"] == "jsonld"
    assert {e["name"] for e in result["entities"]} == {"Foo", "Bar"}
    assert result["confidence"] >= 0.6


def test_empty_page_returns_zero_confidence():
    result = extract(EMPTY_HTML, {}, None)
    assert result["entities"] == []
    assert result["confidence"] == 0.0
