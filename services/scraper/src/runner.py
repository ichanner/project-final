"""Run a scrape: fetch -> snapshot -> extract -> diff -> persist."""

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
LOCAL_MODEL_URL = os.environ.get("LOCAL_MODEL_URL", "http://local-model:8082")
LOCAL_CONFIDENCE_THRESHOLD = float(os.environ.get("LOCAL_CONFIDENCE_THRESHOLD", "0.7"))


async def _extract(html: str, schema: dict, anchor: str | None) -> dict[str, Any]:
    """Try local model first; escalate to extracto/cloud on low confidence."""
    payload = {"html": html, "schema": schema, "anchor": anchor}
    backend = "local"
    confidence = 0.0
    entities: list[dict] = []
    cost_usd = 0.0

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.post(f"{LOCAL_MODEL_URL}/extract", json=payload)
            r.raise_for_status()
            data = r.json()
            entities = data.get("entities", [])
            confidence = float(data.get("confidence", 0.0))
        except Exception as e:  # noqa: BLE001
            confidence = 0.0
            entities = []
            local_error = str(e)
        else:
            local_error = None

        if confidence < LOCAL_CONFIDENCE_THRESHOLD:
            backend = "cloud"
            r = await client.post(f"{EXTRACTO_URL}/extract", json=payload)
            r.raise_for_status()
            data = r.json()
            entities = data.get("entities", [])
            confidence = float(data.get("confidence", 0.0))
            cost_usd = float(data.get("cost_usd", 0.0))

    return {
        "backend": backend,
        "confidence": confidence,
        "entities": entities,
        "cost_usd": cost_usd,
        "local_error": local_error,
    }


async def run_source(source_id: int) -> dict[str, Any]:
    """Run a full scrape pipeline for a single source. Returns run summary."""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT url, schema, anchor, identity_key FROM sources WHERE id = %s",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"source {source_id} not found")
        url, schema, anchor, identity_key = row

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
    backend_in_use.labels(backend="local").set(1)
    try:
        result = await _extract(html, schema or {}, anchor)
    finally:
        backend_in_use.labels(backend="local").set(0)

    backend = result["backend"]
    if backend == "cloud":
        escalations_total.labels(source_id=sid_label).inc()

    confidence = result["confidence"]
    cost_usd = result["cost_usd"]
    entities = result["entities"]

    run_confidence.labels(source_id=sid_label, backend=backend).observe(confidence)
    run_cost_usd.labels(source_id=sid_label, backend=backend).inc(cost_usd)

    # Diff + persist
    new_count = updated_count = stale_count = 0
    seen_identities: set[str] = set()

    with conn() as c, c.cursor() as cur:
        for ent in entities:
            ident = identity_for(ent, list(identity_key))
            seen_identities.add(ident)
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

        # Mark anything we didn't see this run as stale (don't delete).
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
