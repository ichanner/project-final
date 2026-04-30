"""Cached-anchor first, LLM bake-off as fallback.

Architecture (the cost story):

  Each Run on a source:
    1. Fetch HTML, snapshot it.
    2. If source.anchors is set:
         - Apply via BeautifulSoup (~50ms, $0).
         - Verify result quality against the LLM's verification probe.
         - If valid: insert a single fast-path run row, diff, persist. DONE.
       If anchors are missing or fail verification:
         - Run the LLM bake-off (primary + challengers, all in parallel).
         - Each model returns its OWN anchor recipe (NOT entities).
         - Apply each recipe via BS4. The "agreement" metric becomes "do
           the models' recipes produce the same entity set?"
         - If primary's recipe verifies, save it to source.anchors.
         - Insert one run row per model. Diff primary's entities, persist.

The bake-off survives — but it only runs when anchors are missing or have
broken. Steady state polls cost zero LLM tokens.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from .db import conn
from .diff import identity_for
from .dom_extractor import apply_anchors, verify_anchors
from .fetcher import fetch
from .metrics import (
    agreement_jaccard,
    escalations_total,
    fast_path_duration,
    fast_path_total,
    fetch_duration,
    fetch_total,
    field_agreement,
    field_changes_total,
    run_confidence,
    run_cost_usd,
    run_entities,
)

log = logging.getLogger("scraper.runner")

EXTRACTO_URL = os.environ.get("EXTRACTO_URL", "http://extracto:8081")


async def _call_model(
    client: httpx.AsyncClient,
    html: str,
    schema: dict,
    anchor: str | None,
    model: str,
    identity_field: str | None,
) -> dict[str, Any]:
    """One LLM call returns BOTH anchors AND entities — anchors are the
    cost-saver (cached, BS4-applied next time), entities are the fallback
    when anchors fail verification."""
    t0 = time.monotonic()
    try:
        r = await client.post(
            f"{EXTRACTO_URL}/extract",
            json={
                "html": html, "schema": schema, "anchor": anchor,
                "model": model, "identity_field": identity_field,
            },
            timeout=180.0,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "model": model,
            "anchors": data.get("anchors"),
            "entities_from_llm": data.get("entities") or [],
            "confidence": float(data.get("confidence", 0.0)),
            "cost_usd": float(data.get("cost_usd", 0.0)),
            "duration_s": time.monotonic() - t0,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "model": model,
            "anchors": None,
            "entities_from_llm": [],
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


def _schema_field_names(schema: dict) -> list[str]:
    fields = (schema or {}).get("fields") or {}
    return list(fields.keys()) if isinstance(fields, dict) else []


def _diff_and_persist(
    cur,
    source_id: int,
    primary_run_id: int,
    entities: list[dict],
    identity_key: list[str],
    schema_fields: list[str],
    confidence: float,
    sid_label: str,
) -> tuple[int, int, int]:
    """Apply primary entities to the entities table. Returns (new, updated, stale)."""
    new_count = updated_count = stale_count = 0
    for ent in entities:
        ident = identity_for(ent, identity_key, schema_fields)
        cur.execute(
            "SELECT id, data FROM entities WHERE source_id = %s AND identity = %s",
            (source_id, ident),
        )
        existing = cur.fetchone()
        if existing is None:
            cur.execute(
                "INSERT INTO entities (source_id, identity, data, confidence, last_run_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (source_id, ident, Jsonb(ent), confidence, primary_run_id),
            )
            new_count += 1
            run_entities.labels(source_id=sid_label, change="new").inc()
            continue
        old_id, old_data = existing
        if old_data != ent:
            cur.execute(
                "UPDATE entities SET data = %s, confidence = %s, "
                "last_seen = now(), last_run_id = %s, stale = FALSE WHERE id = %s",
                (Jsonb(ent), confidence, primary_run_id, old_id),
            )
            updated_count += 1
            run_entities.labels(source_id=sid_label, change="updated").inc()
            for field_name in set((old_data or {}).keys()) | set(ent.keys()):
                old_v = (old_data or {}).get(field_name)
                new_v = ent.get(field_name)
                if old_v != new_v:
                    cur.execute(
                        "INSERT INTO entity_changes "
                        "(entity_id, source_id, run_id, field, old_value, new_value) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            old_id, source_id, primary_run_id, field_name,
                            Jsonb(old_v) if old_v is not None else None,
                            Jsonb(new_v) if new_v is not None else None,
                        ),
                    )
                    field_changes_total.labels(source_id=sid_label, field=field_name).inc()
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
    return new_count, updated_count, stale_count


async def _fetch_and_snapshot(source_id: int, url: str, sid_label: str) -> tuple[int, str, int] | None:
    """Returns (status, html, snapshot_id) or None on fetch error."""
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
        log.exception("fetch failed src=%s", source_id)
        return {"_error_run_id": run_id, "_error": str(e)}  # type: ignore[return-value]
    finally:
        fetch_duration.labels(source_id=sid_label).observe(time.monotonic() - t0)

    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO snapshots (source_id, status_code, html, bytes) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (source_id, status, html, len(html)),
        )
        snapshot_id = cur.fetchone()[0]

    return status, html, snapshot_id


async def run_source(source_id: int) -> dict[str, Any]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT url, schema, anchor, identity_key, primary_model, comparison_models, anchors "
            "FROM sources WHERE id = %s",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"source {source_id} not found")
        url, schema, anchor, identity_key, primary_model, comparison_models, cached_anchors = row

    sid_label = str(source_id)
    schema = schema or {}
    schema_fields = _schema_field_names(schema)
    identity_key = list(identity_key or [])
    comparison_models = list(comparison_models or [])
    identity_field = identity_key[0] if identity_key else (schema_fields[0] if schema_fields else None)

    fetched = await _fetch_and_snapshot(source_id, url, sid_label)
    if isinstance(fetched, dict) and "_error" in fetched:
        return {"run_id": fetched["_error_run_id"], "error": fetched["_error"]}
    status, html, snapshot_id = fetched  # type: ignore[misc]

    # ---- Fast path: try cached anchors, no LLM call -------------------------
    if cached_anchors:
        t0 = time.monotonic()
        verdict = verify_anchors(html, cached_anchors, schema_fields)
        elapsed = time.monotonic() - t0
        fast_path_duration.labels(source_id=sid_label).observe(elapsed)

        if verdict["ok"]:
            entities = apply_anchors(html, cached_anchors)
            confidence = float(cached_anchors.get("confidence", 0.95))
            with conn() as c, c.cursor() as cur:
                cur.execute(
                    "INSERT INTO runs "
                    "(source_id, snapshot_id, started_at, finished_at, backend, "
                    " is_primary, confidence, entity_count, cost_usd) "
                    "VALUES (%s, %s, now(), now(), %s, TRUE, %s, %s, 0) "
                    "RETURNING id",
                    (source_id, snapshot_id, "fast-path", confidence, len(entities)),
                )
                run_id = cur.fetchone()[0]
                new_count, updated_count, stale_count = _diff_and_persist(
                    cur, source_id, run_id, entities, identity_key,
                    schema_fields, confidence, sid_label,
                )
                cur.execute(
                    "UPDATE runs SET new_count = %s, updated_count = %s, stale_count = %s "
                    "WHERE id = %s",
                    (new_count, updated_count, stale_count, run_id),
                )

            fast_path_total.labels(source_id=sid_label, outcome="hit").inc()
            run_entities.labels(source_id=sid_label, change="new")  # ensure series exists
            log.info(
                "fast-path src=%s entities=%s new=%s updated=%s stale=%s in %.0fms",
                source_id, len(entities), new_count, updated_count, stale_count, elapsed * 1000,
            )
            return {
                "snapshot_id": snapshot_id, "source_id": source_id,
                "primary_model": "fast-path", "models_run": ["fast-path"],
                "primary": {
                    "run_id": run_id, "entity_count": len(entities),
                    "new": new_count, "updated": updated_count, "stale": stale_count,
                    "cost_usd": 0.0, "confidence": confidence,
                },
                "challengers": [],
                "fast_path": {"hit": True, "duration_ms": int(elapsed * 1000)},
            }

        # Anchors are bad → fall through to LLM, log why.
        fast_path_total.labels(source_id=sid_label, outcome="miss").inc()
        log.warning("fast-path miss src=%s reasons=%s", source_id, verdict["reasons"])

    # ---- LLM bake-off: each model returns its own anchor recipe -------------
    if not primary_model:
        raise ValueError(f"source {source_id} has no primary_model")

    models_to_run = [primary_model] + [m for m in comparison_models if m != primary_model]

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_call_model(client, html, schema, anchor, m, identity_field) for m in models_to_run]
        )

    # For each model: prefer BS4-applied anchors (verified). If anchors fail,
    # fall back to the LLM's directly-extracted entities — same call, no
    # second round-trip. Either way we get entities; the difference is whether
    # next poll is free (anchored) or not (LLM every time).
    per_model: list[dict[str, Any]] = []
    for r in results:
        anchors = r["anchors"]
        verdict = {"ok": False, "reasons": ["no anchors returned"], "count": 0, "expected_count": 0}
        entities = []
        source_of_entities = "none"
        if anchors:
            verdict = verify_anchors(html, anchors, schema_fields)
            if verdict["ok"]:
                entities = apply_anchors(html, anchors)
                source_of_entities = "anchored"
        if not entities and r["entities_from_llm"]:
            # Fallback: use the LLM's direct extraction. Anchors won't be cached.
            entities = r["entities_from_llm"]
            source_of_entities = "llm-direct"
        per_model.append({**r, "entities": entities, "verdict": verdict, "source": source_of_entities})

    primary_pm = per_model[0]
    primary_entities = primary_pm["entities"]
    primary_identities = {identity_for(e, identity_key, schema_fields) for e in primary_entities}

    # If the primary's anchors verify, persist them as the canonical recipe.
    if primary_pm["anchors"] and primary_pm["verdict"]["ok"]:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                "UPDATE sources SET anchors = %s, last_anchored_at = now() "
                "WHERE id = %s",
                (Jsonb(primary_pm["anchors"]), source_id),
            )
        log.info("anchored src=%s via %s, %s entities", source_id, primary_model, len(primary_entities))

    # Persist a run row per model
    run_rows: list[dict[str, Any]] = []
    primary_run_id: int | None = None
    with conn() as c, c.cursor() as cur:
        for r in per_model:
            is_primary = r["model"] == primary_model
            agreement: float | None = None
            if not is_primary:
                challenger_idents = {identity_for(e, identity_key, schema_fields) for e in r["entities"]}
                agreement = _jaccard(primary_identities, challenger_idents)
                agreement_jaccard.labels(
                    source_id=sid_label, primary=primary_model, challenger=r["model"]
                ).set(agreement)

                # Per-field agreement on intersection of identities
                pby = {identity_for(e, identity_key, schema_fields): e for e in primary_entities}
                cby = {identity_for(e, identity_key, schema_fields): e for e in r["entities"]}
                common = set(pby.keys()) & set(cby.keys())
                if common and schema_fields:
                    for fname in schema_fields:
                        matches, comparable = 0, 0
                        for ident in common:
                            pv, cv = pby[ident].get(fname), cby[ident].get(fname)
                            if pv is None and cv is None:
                                continue
                            comparable += 1
                            if pv == cv:
                                matches += 1
                        if comparable:
                            field_agreement.labels(
                                source_id=sid_label, primary=primary_model,
                                challenger=r["model"], field=fname,
                            ).set(matches / comparable)

            cur.execute(
                "INSERT INTO runs "
                "(source_id, snapshot_id, started_at, finished_at, backend, "
                " is_primary, confidence, entity_count, cost_usd, agreement, error) "
                "VALUES (%s, %s, now(), now(), %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    source_id, snapshot_id, r["model"], is_primary,
                    r["confidence"], len(r["entities"]), r["cost_usd"],
                    agreement, r["error"] or ("; ".join(r["verdict"]["reasons"]) if r["verdict"]["reasons"] else None),
                ),
            )
            run_id = cur.fetchone()[0]
            if is_primary:
                primary_run_id = run_id

            run_confidence.labels(source_id=sid_label, backend=r["model"]).observe(r["confidence"])
            run_cost_usd.labels(source_id=sid_label, backend=r["model"]).inc(r["cost_usd"])
            if not is_primary:
                escalations_total.labels(source_id=sid_label, model=r["model"]).inc()

            run_rows.append({
                "run_id": run_id, "model": r["model"], "is_primary": is_primary,
                "confidence": r["confidence"], "entity_count": len(r["entities"]),
                "cost_usd": r["cost_usd"], "duration_s": round(r["duration_s"], 3),
                "agreement": agreement,
                "error": r["error"],
                "anchors_ok": r["verdict"]["ok"],
            })

    # Diff primary entities into entities table — works whether the entities
    # came from BS4-applied anchors or from the LLM's direct extraction.
    new_count = updated_count = stale_count = 0
    if primary_run_id is not None and primary_pm["error"] is None and primary_entities:
        confidence = primary_pm["confidence"]
        with conn() as c, c.cursor() as cur:
            new_count, updated_count, stale_count = _diff_and_persist(
                cur, source_id, primary_run_id, primary_entities,
                identity_key, schema_fields, confidence, sid_label,
            )
            cur.execute(
                "UPDATE runs SET new_count = %s, updated_count = %s, stale_count = %s "
                "WHERE id = %s",
                (new_count, updated_count, stale_count, primary_run_id),
            )

    return {
        "snapshot_id": snapshot_id, "source_id": source_id,
        "primary_model": primary_model,
        "models_run": [r["model"] for r in run_rows],
        "primary": {
            "run_id": primary_run_id, "entity_count": len(primary_entities),
            "new": new_count, "updated": updated_count, "stale": stale_count,
            "cost_usd": primary_pm["cost_usd"], "confidence": primary_pm["confidence"],
            "anchors_persisted": primary_pm["anchors"] is not None and primary_pm["verdict"]["ok"],
            "source": primary_pm.get("source", "none"),
        },
        "challengers": [r for r in run_rows if not r["is_primary"]],
        "fast_path": {"hit": False},
    }
