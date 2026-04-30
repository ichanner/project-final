"""Multi-model bake-off runner.

A single Run on a source fetches the page once, snapshots the HTML, and then
fans out the same HTML to every model in (primary_model + comparison_models).
Each model's call becomes its own row in `runs`, all sharing the same
snapshot_id. The primary model's entities are persisted; challenger runs are
recorded for measurement only.

Agreement (Jaccard on identity-keys) is computed per-challenger against the
primary's entity set and stored both in the runs row and as a Prometheus gauge,
so Grafana can show per-pair agreement over time.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from .db import conn
from .diff import identity_for
from .fetcher import fetch
from .metrics import (
    agreement_jaccard,
    escalations_total,
    fetch_duration,
    fetch_total,
    run_confidence,
    run_cost_usd,
    run_entities,
)

EXTRACTO_URL = os.environ.get("EXTRACTO_URL", "http://extracto:8081")


async def _call_model(
    client: httpx.AsyncClient,
    html: str,
    schema: dict,
    anchor: str | None,
    model: str,
) -> dict[str, Any]:
    """Call extracto for one model. Returns the parsed JSON or an error envelope."""
    t0 = time.monotonic()
    try:
        r = await client.post(
            f"{EXTRACTO_URL}/extract",
            json={"html": html, "schema": schema, "anchor": anchor, "model": model},
            timeout=180.0,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "model": model,
            "entities": data.get("entities", []),
            "confidence": float(data.get("confidence", 0.0)),
            "cost_usd": float(data.get("cost_usd", 0.0)),
            "duration_s": time.monotonic() - t0,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "model": model,
            "entities": [],
            "confidence": 0.0,
            "cost_usd": 0.0,
            "duration_s": time.monotonic() - t0,
            "error": str(e),
        }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


async def run_source(source_id: int) -> dict[str, Any]:
    """Run all configured models for one source against a single fresh snapshot."""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT url, schema, anchor, identity_key, primary_model, comparison_models "
            "FROM sources WHERE id = %s",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"source {source_id} not found")
        url, schema, anchor, identity_key, primary_model, comparison_models = row

    if not primary_model:
        raise ValueError(
            f"source {source_id} has no primary_model set "
            "— assign one before running"
        )

    sid_label = str(source_id)
    schema = schema or {}
    comparison_models = list(comparison_models or [])
    # Dedup: don't run the primary as a comparison too.
    models_to_run = [primary_model] + [m for m in comparison_models if m != primary_model]

    # Fetch once
    t0 = time.monotonic()
    try:
        status, html = await fetch(url)
        fetch_total.labels(source_id=sid_label, outcome="ok").inc()
    except Exception as e:  # noqa: BLE001
        fetch_total.labels(source_id=sid_label, outcome="error").inc()
        with conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (source_id, started_at, finished_at, error, is_primary) "
                "VALUES (%s, now(), now(), %s, TRUE) RETURNING id",
                (source_id, str(e)),
            )
            run_id = cur.fetchone()[0]
        return {"run_id": run_id, "error": str(e)}
    finally:
        fetch_duration.labels(source_id=sid_label).observe(time.monotonic() - t0)

    # Single shared snapshot
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO snapshots (source_id, status_code, html, bytes) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (source_id, status, html, len(html)),
        )
        snapshot_id = cur.fetchone()[0]

    # Fan out across models in parallel
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_call_model(client, html, schema, anchor, m) for m in models_to_run]
        )

    primary_result = results[0]
    primary_identities = {
        identity_for(e, list(identity_key)) for e in primary_result["entities"]
    }

    # Persist a runs row per model
    run_rows: list[dict[str, Any]] = []
    primary_run_id: int | None = None
    with conn() as c, c.cursor() as cur:
        for result in results:
            is_primary = result["model"] == primary_model
            agreement: float | None = None
            if not is_primary:
                challenger_idents = {
                    identity_for(e, list(identity_key)) for e in result["entities"]
                }
                agreement = _jaccard(primary_identities, challenger_idents)
                agreement_jaccard.labels(
                    source_id=sid_label,
                    primary=primary_model,
                    challenger=result["model"],
                ).set(agreement)

            cur.execute(
                "INSERT INTO runs "
                "(source_id, snapshot_id, started_at, finished_at, backend, "
                " is_primary, confidence, entity_count, cost_usd, agreement, error) "
                "VALUES (%s, %s, now(), now(), %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    source_id,
                    snapshot_id,
                    result["model"],
                    is_primary,
                    result["confidence"],
                    len(result["entities"]),
                    result["cost_usd"],
                    agreement,
                    result["error"],
                ),
            )
            run_id = cur.fetchone()[0]
            if is_primary:
                primary_run_id = run_id

            # Per-model metrics
            run_confidence.labels(source_id=sid_label, backend=result["model"]).observe(
                result["confidence"]
            )
            run_cost_usd.labels(source_id=sid_label, backend=result["model"]).inc(
                result["cost_usd"]
            )
            if not is_primary:
                escalations_total.labels(
                    source_id=sid_label, model=result["model"]
                ).inc()

            run_rows.append(
                {
                    "run_id": run_id,
                    "model": result["model"],
                    "is_primary": is_primary,
                    "confidence": result["confidence"],
                    "entity_count": len(result["entities"]),
                    "cost_usd": result["cost_usd"],
                    "duration_s": round(result["duration_s"], 3),
                    "agreement": agreement,
                    "error": result["error"],
                }
            )

    # Diff primary entities into the entities table
    new_count = updated_count = stale_count = 0
    if primary_run_id is not None and primary_result["error"] is None:
        with conn() as c, c.cursor() as cur:
            for ent in primary_result["entities"]:
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
                        (source_id, ident, Jsonb(ent), primary_result["confidence"], primary_run_id),
                    )
                    new_count += 1
                    run_entities.labels(source_id=sid_label, change="new").inc()
                else:
                    old_id, old_data = existing
                    if old_data != ent:
                        cur.execute(
                            "UPDATE entities SET data = %s, confidence = %s, "
                            "last_seen = now(), last_run_id = %s, stale = FALSE WHERE id = %s",
                            (Jsonb(ent), primary_result["confidence"], primary_run_id, old_id),
                        )
                        updated_count += 1
                        run_entities.labels(source_id=sid_label, change="updated").inc()
                    else:
                        cur.execute(
                            "UPDATE entities SET last_seen = now(), last_run_id = %s, stale = FALSE WHERE id = %s",
                            (primary_run_id, old_id),
                        )

            cur.execute(
                "UPDATE entities SET stale = TRUE WHERE source_id = %s AND last_run_id != %s",
                (source_id, primary_run_id),
            )
            stale_count = cur.rowcount or 0
            if stale_count > 0:
                run_entities.labels(source_id=sid_label, change="stale").inc(stale_count)

            cur.execute(
                "UPDATE runs SET new_count = %s, updated_count = %s, stale_count = %s "
                "WHERE id = %s",
                (new_count, updated_count, stale_count, primary_run_id),
            )

    # Update primary's row with the diff counts (already done above) and return
    return {
        "snapshot_id": snapshot_id,
        "source_id": source_id,
        "primary_model": primary_model,
        "models_run": [r["model"] for r in run_rows],
        "primary": {
            "run_id": primary_run_id,
            "entity_count": primary_result and len(primary_result["entities"]) or 0,
            "new": new_count,
            "updated": updated_count,
            "stale": stale_count,
            "cost_usd": primary_result["cost_usd"],
            "confidence": primary_result["confidence"],
        },
        "challengers": [r for r in run_rows if not r["is_primary"]],
    }
