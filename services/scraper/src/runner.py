"""Run a scrape: fetch -> snapshot -> extract -> diff -> persist.

The extraction tier:
  1. Heuristic service runs first. Free, ~50ms, good on JSON-LD and clean tables.
  2. If heuristic confidence is below HEURISTIC_CONFIDENCE_THRESHOLD, escalate
     to extracto with the source's chosen cloud model (claude/gpt-4o/llama/etc).
The "backend" string we record in the DB is either "heuristic" or the model
name — that's what Grafana groups by.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from .db import conn
from .diff import identity_for
from .fetcher import fetch
from .metrics import (
    backend_in_use,
    escalations_total,
    fetch_duration,
    fetch_total,
    run_confidence,
    run_cost_usd,
    run_entities,
)

EXTRACTO_URL = os.environ.get("EXTRACTO_URL", "http://extracto:8081")
HEURISTIC_URL = os.environ.get("HEURISTIC_URL", "http://heuristic:8082")
HEURISTIC_CONFIDENCE_THRESHOLD = float(
    os.environ.get("HEURISTIC_CONFIDENCE_THRESHOLD", "0.7")
)


async def _extract(
    html: str, schema: dict, anchor: str | None, model: str | None
) -> dict[str, Any]:
    """Try heuristic first; escalate to a cloud model on low confidence."""
    payload = {"html": html, "schema": schema, "anchor": anchor}
    backend = "heuristic"
    confidence = 0.0
    entities: list[dict] = []
    cost_usd = 0.0
    heuristic_error: str | None = None

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            r = await client.post(f"{HEURISTIC_URL}/extract", json=payload)
            r.raise_for_status()
            data = r.json()
            entities = data.get("entities", [])
            confidence = float(data.get("confidence", 0.0))
        except Exception as e:  # noqa: BLE001
            confidence = 0.0
            entities = []
            heuristic_error = str(e)

        if confidence < HEURISTIC_CONFIDENCE_THRESHOLD:
            cloud_payload = {**payload, "model": model} if model else payload
            r = await client.post(f"{EXTRACTO_URL}/extract", json=cloud_payload)
            r.raise_for_status()
            data = r.json()
            entities = data.get("entities", [])
            confidence = float(data.get("confidence", 0.0))
            cost_usd = float(data.get("cost_usd", 0.0))
            # extracto echoes back the model it actually used; that's our
            # backend label. Falls back to the requested model if missing.
            backend = data.get("model") or model or "cloud"

    return {
        "backend": backend,
        "confidence": confidence,
        "entities": entities,
        "cost_usd": cost_usd,
        "heuristic_error": heuristic_error,
    }


async def run_source(source_id: int) -> dict[str, Any]:
    """Run a full scrape pipeline for a single source. Returns run summary."""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT url, schema, anchor, identity_key, model FROM sources WHERE id = %s",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"source {source_id} not found")
        url, schema, anchor, identity_key, model = row

    sid_label = str(source_id)

    # Fetch
    t0 = time.monotonic()
    try:
        status, html = await fetch(url)
        fetch_total.labels(source_id=sid_label, outcome="ok").inc()
    except Exception as e:  # noqa: BLE001
        fetch_total.labels(source_id=sid_label, outcome="error").inc()
        with conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (source_id, started_at, finished_at, error) "
                "VALUES (%s, now(), now(), %s) RETURNING id",
                (source_id, str(e)),
            )
            run_id = cur.fetchone()[0]
        return {"run_id": run_id, "error": str(e)}
    finally:
        fetch_duration.labels(source_id=sid_label).observe(time.monotonic() - t0)

    # Snapshot
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO snapshots (source_id, status_code, html, bytes) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (source_id, status, html, len(html)),
        )
        snapshot_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO runs (source_id, snapshot_id, started_at) VALUES (%s, %s, now()) RETURNING id",
            (source_id, snapshot_id),
        )
        run_id = cur.fetchone()[0]

    # Extract
    backend_in_use.labels(backend="heuristic").set(1)
    try:
        result = await _extract(html, schema or {}, anchor, model)
    finally:
        backend_in_use.labels(backend="heuristic").set(0)

    backend = result["backend"]
    if backend != "heuristic":
        escalations_total.labels(source_id=sid_label, model=backend).inc()

    confidence = result["confidence"]
    cost_usd = result["cost_usd"]
    entities = result["entities"]

    run_confidence.labels(source_id=sid_label, backend=backend).observe(confidence)
    run_cost_usd.labels(source_id=sid_label, backend=backend).inc(cost_usd)

    # Diff + persist
    new_count = updated_count = stale_count = 0

    with conn() as c, c.cursor() as cur:
        for ent in entities:
            ident = identity_for(ent, list(identity_key))
            cur.execute(
                "SELECT id, data FROM entities WHERE source_id = %s AND identity = %s",
                (source_id, ident),
            )
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    "INSERT INTO entities (source_id, identity, data, confidence, last_run_id) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (source_id, ident, Jsonb(ent), confidence, run_id),
                )
                new_count += 1
                run_entities.labels(source_id=sid_label, change="new").inc()
            else:
                old_id, old_data = existing
                if old_data != ent:
                    cur.execute(
                        "UPDATE entities SET data = %s, confidence = %s, "
                        "last_seen = now(), last_run_id = %s, stale = FALSE WHERE id = %s",
                        (Jsonb(ent), confidence, run_id, old_id),
                    )
                    updated_count += 1
                    run_entities.labels(source_id=sid_label, change="updated").inc()
                else:
                    cur.execute(
                        "UPDATE entities SET last_seen = now(), last_run_id = %s, stale = FALSE WHERE id = %s",
                        (run_id, old_id),
                    )

        cur.execute(
            "UPDATE entities SET stale = TRUE WHERE source_id = %s AND last_run_id != %s "
            "RETURNING 1",
            (source_id, run_id),
        )
        stale_count = cur.rowcount or 0
        if stale_count > 0:
            run_entities.labels(source_id=sid_label, change="stale").inc(stale_count)

        cur.execute(
            "UPDATE runs SET finished_at = now(), backend = %s, confidence = %s, "
            "entity_count = %s, new_count = %s, updated_count = %s, stale_count = %s, cost_usd = %s "
            "WHERE id = %s",
            (
                backend,
                confidence,
                len(entities),
                new_count,
                updated_count,
                stale_count,
                cost_usd,
                run_id,
            ),
        )

    return {
        "run_id": run_id,
        "source_id": source_id,
        "backend": backend,
        "confidence": confidence,
        "entity_count": len(entities),
        "new": new_count,
        "updated": updated_count,
        "stale": stale_count,
        "cost_usd": cost_usd,
    }
