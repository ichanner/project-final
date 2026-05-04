"""Cached-anchor BS4 first, LLM anchoring only on first run or operator demand.

Architecture:

  Each Run on a source:
    1. Fetch the page HTML.
    2. If source.anchors is set:
         - Apply via BeautifulSoup (~50ms, $0).
         - Insert a fast-path run row, diff against stored entities, persist.
       If anchors are missing:
         - Call the selected LLM once to create an anchor recipe + bootstrap
           entity sample. Cache the recipe; future cron polls use BS4 only.

Steady-state polls cost zero LLM tokens. The unique angle is that the LLM
output (a CSS-selector recipe) is itself the cached artifact — not the model.

Fetch-side metrics live entirely in fetcher.py to keep the metric story
per-file. This module emits poll-, anchor-, and diff-side metrics.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from .db import conn, ensure_runtime_schema
from .diff import identity_for
from .dom_extractor import apply_anchors, verify_anchors
from .fetcher import fetch
from .metrics import (
    anchor_extraction_count,
    anchor_re_anchor_total,
    fast_path_duration,
    fast_path_total,
    fetch_duration,
    fetch_total,
    field_changes_total,
    poll_duration,
    poll_total,
    run_cost_usd,
    run_entities,
)

log = logging.getLogger("scraper.runner")

EXTRACTO_URL = os.environ.get("EXTRACTO_URL", "http://extracto:8081")
_schema_checked = False


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
    except Exception as e:
        return {
            "model": model,
            "anchors": None,
            "entities_from_llm": [],
            "confidence": 0.0,
            "cost_usd": 0.0,
            "duration_s": time.monotonic() - t0,
            "error": str(e),
        }


def _schema_field_names(schema: dict) -> list[str]:
    fields = (schema or {}).get("fields") or {}
    return list(fields.keys()) if isinstance(fields, dict) else []


def _split_field_roles(schema: dict) -> tuple[list[str], list[str]]:
    """Returns (anchor_fields, volatile_fields). Default role = anchor."""
    fields = (schema or {}).get("fields") or {}
    anchors, volatiles = [], []
    if not isinstance(fields, dict):
        return anchors, volatiles
    for name, defn in fields.items():
        role = (defn or {}).get("role") if isinstance(defn, dict) else None
        (volatiles if role == "volatile" else anchors).append(name)
    return anchors, volatiles


def _diff_and_persist(
    cur,
    source_id: int,
    primary_run_id: int,
    entities: list[dict],
    identity_key: list[str],
    schema_fields: list[str],
    volatile_fields: list[str],
    confidence: float,
    sid_label: str,
    anchor_fields: list[str] | None = None,
) -> tuple[int, int, int]:
    """Apply primary entities to the entities table.

    Two non-obvious behaviors:
    1. Anchor-field differences are SILENTLY merged (treated as extraction
       noise, e.g. whitespace, footnote markers, BS4 binding flicker). They
       never contribute to updated_count and never write entity_changes.
    2. Only VOLATILE field differences count as drift — they're the signal
       the user explicitly opted in to watch.

    If schema declares no volatile fields (legacy), we fall back to all-fields
    diffing for back-compat.
    """
    drift_fields = volatile_fields or schema_fields
    secondary_anchors = [f for f in (anchor_fields or []) if not identity_key or f != identity_key[0]]
    new_count = updated_count = stale_count = 0
    rejected_count = 0

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
        old_data = old_data or {}

        if secondary_anchors:
            mismatched = []
            for af in secondary_anchors:
                old_a = old_data.get(af)
                new_a = ent.get(af)
                if old_a and new_a and str(old_a).strip() and str(new_a).strip() and old_a != new_a:
                    mismatched.append((af, old_a, new_a))
            if mismatched:
                rejected_count += 1
                cur.execute(
                    "UPDATE entities SET last_seen = now(), last_run_id = %s, stale = FALSE WHERE id = %s",
                    (primary_run_id, old_id),
                )
                continue

        volatile_diff = False
        changed_volatile_fields: list[tuple[str, Any, Any]] = []
        for field_name in drift_fields:
            old_v = old_data.get(field_name)
            new_v = ent.get(field_name)
            if old_v != new_v:
                volatile_diff = True
                changed_volatile_fields.append((field_name, old_v, new_v))

        cur.execute(
            "UPDATE entities SET data = %s, confidence = %s, "
            "last_seen = now(), last_run_id = %s, stale = FALSE WHERE id = %s",
            (Jsonb(ent), confidence, primary_run_id, old_id),
        )

        if volatile_diff:
            updated_count += 1
            run_entities.labels(source_id=sid_label, change="updated").inc()
            for field_name, old_v, new_v in changed_volatile_fields:
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

    cur.execute(
        "UPDATE entities SET stale = TRUE WHERE source_id = %s AND last_run_id != %s",
        (source_id, primary_run_id),
    )
    stale_count = cur.rowcount or 0
    if stale_count > 0:
        run_entities.labels(source_id=sid_label, change="stale").inc(stale_count)

    if rejected_count:
        log.info(
            "src=%s rejected %s rows for anchor-field mismatch (extractor bound to wrong DOM row)",
            source_id, rejected_count,
        )

    return new_count, updated_count, stale_count


async def _fetch_and_snapshot(
    source_id: int,
    url: str,
    sid_label: str,
) -> dict[str, Any]:
    """Returns fetch/snapshot metadata or an error-marker dict on failure.

    Fetch metric emission lives entirely in fetcher.py — the per-status,
    error-class, response-size, redirect-count, and consecutive-failure
    counters all fire there. We just consume the (status, html) here and
    log the failure to runs.error if something blew up.
    """
    t0 = time.monotonic()
    try:
        result = await fetch(url, source_id)
    except Exception as e:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (source_id, started_at, finished_at, error, is_primary) "
                "VALUES (%s, now(), now(), %s, TRUE) RETURNING id",
                (source_id, str(e)),
            )
            run_id = cur.fetchone()[0]
        log.warning("fetch failed src=%s: %s", source_id, e)
        return {"_error_run_id": run_id, "_error": str(e)}
    finally:
        fetch_duration.labels(source_id=sid_label).observe(time.monotonic() - t0)

    html = result.html
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO snapshots (source_id, status_code, html, bytes) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (source_id, result.status_code, html, len(html)),
        )
        snapshot_id = cur.fetchone()[0]

    return {
        "status": result.status_code,
        "html": html,
        "snapshot_id": snapshot_id,
    }


async def run_source(source_id: int) -> dict[str, Any]:
    """Execute a poll for one source. Wraps the entire pipeline in poll_duration
    so even error-paths contribute timing data."""
    global _schema_checked
    if not _schema_checked:
        ensure_runtime_schema()
        _schema_checked = True

    poll_t0 = time.monotonic()
    sid_label = str(source_id)

    try:
        with conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT url, schema, anchor, identity_key, primary_model, "
                "anchors, last_anchored_at "
                "FROM sources WHERE id = %s",
                (source_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"source {source_id} not found")
            (
                url, schema, anchor, identity_key, primary_model,
                cached_anchors, last_anchored_at,
            ) = row

        schema = schema or {}
        schema_fields = _schema_field_names(schema)
        anchor_fields, volatile_fields = _split_field_roles(schema)
        identity_key = list(identity_key or [])
        identity_field = identity_key[0] if identity_key else (
            anchor_fields[0] if anchor_fields else (
                schema_fields[0] if schema_fields else None
            )
        )

        for change in ("new", "updated", "stale"):
            run_entities.labels(source_id=sid_label, change=change).inc(0)
        for outcome in ("hit", "miss"):
            fast_path_total.labels(source_id=sid_label, outcome=outcome).inc(0)
        for outcome in ("ok", "error"):
            fetch_total.labels(source_id=sid_label, outcome=outcome).inc(0)
        for path in ("dom_fast_path", "llm_anchor"):
            poll_total.labels(source_id=sid_label, path=path).inc(0)

        fetched = await _fetch_and_snapshot(source_id, url, sid_label)
        if isinstance(fetched, dict) and "_error" in fetched:
            return {"run_id": fetched["_error_run_id"], "error": fetched["_error"]}

        html = fetched["html"]
        snapshot_id = fetched["snapshot_id"]

        if cached_anchors:
            poll_total.labels(source_id=sid_label, path="dom_fast_path").inc()
            t0 = time.monotonic()
            verdict = verify_anchors(html, cached_anchors, schema_fields)
            entities = apply_anchors(html, cached_anchors, identity_field=identity_field)
            elapsed = time.monotonic() - t0
            fast_path_duration.labels(source_id=sid_label).observe(elapsed)
            outcome = "hit" if entities else "miss"
            fast_path_total.labels(source_id=sid_label, outcome=outcome).inc()

            anchor_extraction_count.labels(source_id=sid_label).set(len(entities))

            confidence = float(cached_anchors.get("confidence", 0.95))
            err = None if entities else (
                "; ".join(verdict["reasons"])
                or "BS4 produced 0 entities — re-anchor required"
            )
            with conn() as c, c.cursor() as cur:
                cur.execute(
                    "INSERT INTO runs "
                    "(source_id, snapshot_id, started_at, finished_at, backend, "
                    " is_primary, confidence, entity_count, cost_usd, error) "
                    "VALUES (%s, %s, now(), now(), %s, TRUE, %s, %s, 0, %s) "
                    "RETURNING id",
                    (source_id, snapshot_id, "fast-path", confidence, len(entities), err),
                )
                run_id = cur.fetchone()[0]
                new_count = updated_count = stale_count = 0
                if entities:
                    new_count, updated_count, stale_count = _diff_and_persist(
                        cur, source_id, run_id, entities, identity_key,
                        schema_fields, volatile_fields, confidence, sid_label,
                        anchor_fields=anchor_fields,
                    )
                    cur.execute(
                        "UPDATE runs SET new_count = %s, updated_count = %s, stale_count = %s "
                        "WHERE id = %s",
                        (new_count, updated_count, stale_count, run_id),
                    )

            log.info(
                "fast-path src=%s outcome=%s entities=%s new=%s updated=%s stale=%s in %.0fms",
                source_id, outcome, len(entities), new_count, updated_count, stale_count, elapsed * 1000,
            )
            return {
                "snapshot_id": snapshot_id, "source_id": source_id,
                "primary_model": "fast-path", "models_run": ["fast-path"],
                "primary": {
                    "run_id": run_id, "entity_count": len(entities),
                    "new": new_count, "updated": updated_count, "stale": stale_count,
                    "cost_usd": 0.0, "confidence": confidence,
                    "anchors_persisted": True, "source": "anchored",
                    "error": err,
                    "volatile_fields": volatile_fields,
                },
                "fast_path": {"hit": outcome == "hit", "duration_ms": int(elapsed * 1000)},
            }

        if not primary_model:
            raise ValueError(f"source {source_id} has no primary_model")

        poll_total.labels(source_id=sid_label, path="llm_anchor").inc()
        re_anchor_reason = "initial" if last_anchored_at is None else "re_anchor"
        anchor_re_anchor_total.labels(
            source_id=sid_label, reason=re_anchor_reason,
        ).inc()

        async with httpx.AsyncClient() as client:
            result = await _call_model(client, html, schema, anchor, primary_model, identity_field)

        anchors = result["anchors"]
        verdict = {"ok": False, "reasons": ["no anchors returned"], "count": 0, "expected_count": 0}
        primary_entities: list[dict] = []
        source_of_entities = "none"
        if anchors:
            verdict = verify_anchors(html, anchors, schema_fields)
            if verdict["ok"]:
                primary_entities = apply_anchors(html, anchors, identity_field=identity_field)
                source_of_entities = "anchored"
        if not primary_entities and result["entities_from_llm"]:
            primary_entities = result["entities_from_llm"]
            source_of_entities = "llm-bootstrap"

        if anchors:
            with conn() as c, c.cursor() as cur:
                cur.execute(
                    "UPDATE sources SET anchors = %s, last_anchored_at = now() "
                    "WHERE id = %s",
                    (Jsonb(anchors), source_id),
                )
            verified = "verified" if verdict["ok"] else "UNVERIFIED — next poll will likely return 0"
            log.info(
                "anchored src=%s via %s (%s) — %s entities bootstrapped",
                source_id, primary_model, verified, len(primary_entities),
            )

        with conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO runs "
                "(source_id, snapshot_id, started_at, finished_at, backend, "
                " is_primary, confidence, entity_count, cost_usd, error) "
                "VALUES (%s, %s, now(), now(), %s, TRUE, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    source_id, snapshot_id, primary_model,
                    result["confidence"], len(primary_entities), result["cost_usd"],
                    result["error"] or (
                        "; ".join(verdict["reasons"]) if verdict["reasons"] else None
                    ),
                ),
            )
            primary_run_id = cur.fetchone()[0]
            run_cost_usd.labels(source_id=sid_label, backend=primary_model).inc(result["cost_usd"])

        new_count = updated_count = stale_count = 0
        if result["error"] is None and primary_entities:
            confidence = result["confidence"]
            with conn() as c, c.cursor() as cur:
                new_count, updated_count, stale_count = _diff_and_persist(
                    cur, source_id, primary_run_id, primary_entities,
                    identity_key, schema_fields, volatile_fields,
                    confidence, sid_label, anchor_fields=anchor_fields,
                )
                cur.execute(
                    "UPDATE runs SET new_count = %s, updated_count = %s, stale_count = %s "
                    "WHERE id = %s",
                    (new_count, updated_count, stale_count, primary_run_id),
                )

        return {
            "snapshot_id": snapshot_id, "source_id": source_id,
            "primary_model": primary_model,
            "models_run": [primary_model],
            "primary": {
                "run_id": primary_run_id, "entity_count": len(primary_entities),
                "new": new_count, "updated": updated_count, "stale": stale_count,
                "cost_usd": result["cost_usd"], "confidence": result["confidence"],
                "anchors_persisted": anchors is not None and verdict["ok"],
                "source": source_of_entities,
                "error": result["error"],
            },
            "fast_path": {"hit": False},
        }
    finally:
        poll_duration.labels(source_id=sid_label).observe(time.monotonic() - poll_t0)
