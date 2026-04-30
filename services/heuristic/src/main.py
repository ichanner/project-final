from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel
from starlette.responses import Response

from .heuristics import extract

app = FastAPI(title="WebHarvest Heuristic Extractor")

extract_duration = Histogram(
    "heuristic_extract_duration_seconds",
    "Heuristic extraction duration",
    ["source"],
)
extract_outcomes = Counter(
    "heuristic_extract_outcomes_total",
    "Outcomes by extraction source (jsonld, table, cards, none)",
    ["source"],
)


class ExtractIn(BaseModel):
    html: str
    schema_: dict[str, Any] = {}
    anchor: str | None = None

    model_config = {"populate_by_name": True}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/extract")
def do_extract(payload: dict[str, Any]) -> dict[str, Any]:
    html = payload.get("html") or ""
    schema = payload.get("schema") or {}
    anchor = payload.get("anchor")

    with extract_duration.labels(source="any").time():
        result = extract(html, schema, anchor)

    extract_outcomes.labels(source=result.get("source") or "none").inc()
    return result
