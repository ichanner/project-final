"""Scraper API — sources, runs, entities, per-entity change history.

Per-source scheduling: every source with a non-null `refresh_cron` gets its
own APScheduler job. Sources with refresh_cron NULL are manual-only. The
scheduler reconciles itself from the DB on startup and every 30 seconds, so
adding/changing/removing a source via the API is reflected without restart.

Entity history: every per-field change is written to `entity_changes` by
`runner.py`. Two endpoints expose it: GET /sources/{sid}/entities/{eid}/history
and GET /sources/{sid}/changes. Same data is in Prometheus as
`webharvest_field_changes_total{source_id, field}` for rate-based panels.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field
from starlette.responses import Response

from .db import conn, ensure_runtime_schema
from .runner import run_source

log = logging.getLogger("scraper")
logging.basicConfig(level=logging.INFO)


class SourceIn(BaseModel):
    url: str
    label: str | None = None
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    anchor: str | None = None
    identity_key: list[str] = Field(default_factory=list)
    refresh_cron: str | None = None
    conditional_polling: bool = True
    primary_model: str | None = None

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


class SourcePatch(BaseModel):
    """Partial update — only fields present on the body are touched."""
    label: str | None = None
    refresh_cron: str | None = None
    conditional_polling: bool | None = None
    primary_model: str | None = None
    anchor: str | None = None
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_runtime_schema()
    log.info("scraper api started")
    yield
    log.info("scraper api shutting down")


app = FastAPI(title="WebHarvest Scraper", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/sources")
def list_sources() -> list[dict[str, Any]]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT s.id, s.url, s.label, s.schema, s.anchor, s.identity_key, "
            "s.refresh_cron, s.conditional_polling, s.etag, s.last_modified, "
            "s.last_content_bytes, s.primary_model, "
            "s.last_anchored_at, "
            "(s.anchors IS NOT NULL) AS has_anchors, "
            "s.created_at, "
            "(SELECT max(started_at) FROM runs WHERE source_id = s.id AND is_primary) AS last_run_at "
            "FROM sources s ORDER BY s.id"
        )
        cols = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            d = dict(zip(cols, row, strict=False))
            fields = ((d.get("schema") or {}).get("fields")) or {}
            d["schema_field_names"] = list(fields.keys())
            out.append(d)
        return out


@app.post("/sources", status_code=201)
def create_source(src: SourceIn) -> dict[str, Any]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sources "
            "(url, label, schema, anchor, identity_key, refresh_cron, conditional_polling, "
            " primary_model) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (url) DO UPDATE SET label = EXCLUDED.label, "
            "schema = EXCLUDED.schema, anchor = EXCLUDED.anchor, "
            "identity_key = EXCLUDED.identity_key, refresh_cron = EXCLUDED.refresh_cron, "
            "conditional_polling = EXCLUDED.conditional_polling, "
            "primary_model = EXCLUDED.primary_model, "
            "updated_at = now() RETURNING id",
            (
                src.url,
                src.label,
                Jsonb(src.schema_),
                src.anchor,
                src.identity_key,
                src.refresh_cron,
                src.conditional_polling,
                src.primary_model,
            ),
        )
        sid = cur.fetchone()[0]
    return {"id": sid}


@app.patch("/sources/{source_id}")
def patch_source(source_id: int, patch: SourcePatch) -> dict[str, Any]:
    """Partial update. If the schema or anchor description changes, the
    cached DOM anchors are invalidated — they were derived against the old
    schema and may not produce the right fields anymore."""
    fields = patch.model_dump(exclude_unset=True, by_alias=False)
    if "schema_" in fields:
        fields["schema"] = Jsonb(fields.pop("schema_"))

    if not fields:
        raise HTTPException(status_code=400, detail="empty patch")

    schema_changed = "schema" in fields or "anchor" in fields
    sets, vals = [], []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    if schema_changed:
        sets += ["anchors = NULL", "last_anchored_at = NULL"]
    sets.append("updated_at = now()")
    vals.append(source_id)

    with conn() as c, c.cursor() as cur:
        cur.execute(
            f"UPDATE sources SET {', '.join(sets)} WHERE id = %s "
            "RETURNING id, refresh_cron",
            tuple(vals),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="source not found")
        sid, _ = row
    return {
        "id": sid,
        "updated": list(fields.keys()),
        "anchors_invalidated": schema_changed,
    }


@app.post("/sources/{source_id}/re-anchor")
def re_anchor(source_id: int) -> dict[str, Any]:
    """Force the next run to re-derive anchors via the selected LLM.
    Operationally: 'the page changed and the cached recipe is wrong.'"""
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE sources SET anchors = NULL, last_anchored_at = NULL, "
            "updated_at = now() WHERE id = %s RETURNING id",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="source not found")
    return {"id": source_id, "anchors_invalidated": True}


@app.get("/sources/{source_id}/anchors")
def get_anchors(source_id: int) -> dict[str, Any]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT anchors, last_anchored_at FROM sources WHERE id = %s",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="source not found")
    return {
        "source_id": source_id,
        "anchors": row[0],
        "last_anchored_at": row[1],
    }


@app.delete("/sources/{source_id}")
def delete_source(source_id: int) -> dict[str, Any]:
    with conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM sources WHERE id = %s RETURNING id", (source_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="source not found")
    return {"id": source_id, "deleted": True}


@app.post("/sources/{source_id}/run")
async def trigger_run(source_id: int) -> dict[str, Any]:
    try:
        return await run_source(source_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/sources/{source_id}/entities")
def get_entities(source_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, identity, data, confidence, first_seen, last_seen, stale, "
            "(SELECT count(*) FROM entity_changes WHERE entity_id = entities.id) AS update_count "
            "FROM entities WHERE source_id = %s ORDER BY last_seen DESC LIMIT %s",
            (source_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


@app.get("/sources/{source_id}/entities/{entity_id}/history")
def entity_history(source_id: int, entity_id: int, limit: int = 200) -> dict[str, Any]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT identity, data FROM entities WHERE id = %s AND source_id = %s",
            (entity_id, source_id),
        )
        ent = cur.fetchone()
        if not ent:
            raise HTTPException(status_code=404, detail="entity not found")
        cur.execute(
            "SELECT id, run_id, field, old_value, new_value, changed_at "
            "FROM entity_changes WHERE entity_id = %s "
            "ORDER BY changed_at ASC LIMIT %s",
            (entity_id, limit),
        )
        cols = [d[0] for d in cur.description]
        changes = [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    return {
        "entity_id": entity_id,
        "identity": ent[0],
        "current": ent[1],
        "changes": changes,
    }


@app.get("/sources/{source_id}/snapshot")
def latest_snapshot(source_id: int, full: bool = False) -> dict[str, Any]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, fetched_at, status_code, bytes, html "
            "FROM snapshots WHERE source_id = %s "
            "ORDER BY fetched_at DESC LIMIT 1",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="no snapshot for this source")
    snap_id, fetched_at, status, byte_count, html = row
    if full:
        body = html
    else:
        head = html[:2000]
        tail = html[-2000:] if len(html) > 4000 else ""
        body = head + ("\n\n... [truncated " + str(len(html) - 4000) + " bytes] ...\n\n" + tail if tail else "")
    return {
        "snapshot_id": snap_id,
        "fetched_at": fetched_at,
        "status_code": status,
        "bytes": byte_count,
        "html": body,
        "truncated": not full,
    }

