from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field
from starlette.responses import Response

from .db import conn
from .runner import run_source

log = logging.getLogger("scraper")
logging.basicConfig(level=logging.INFO)

scheduler = AsyncIOScheduler()


class SourceIn(BaseModel):
    url: str
    label: str | None = None
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    anchor: str | None = None
    identity_key: list[str] = Field(default_factory=list)
    refresh_cron: str | None = None
    # Optional: which OpenRouter model to escalate to. If omitted, the source
    # uses the extracto service's default. We pass the slug straight through
    # (e.g. "anthropic/claude-sonnet-4", "openai/gpt-4o").
    model: str | None = None

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    default_cron = os.environ.get("SCRAPER_REFRESH_CRON", "0 */6 * * *")
    try:
        scheduler.add_job(
            _refresh_due_sources,
            CronTrigger.from_crontab(default_cron),
            id="default-refresh",
            replace_existing=True,
        )
        scheduler.start()
        log.info("scheduler started cron=%s", default_cron)
    except Exception:
        log.exception("scheduler failed to start")
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="WebHarvest Scraper", lifespan=lifespan)


async def _refresh_due_sources() -> None:
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM sources ORDER BY id")
        ids = [r[0] for r in cur.fetchall()]
    for sid in ids:
        try:
            await run_source(sid)
        except Exception:
            log.exception("scheduled run failed source_id=%s", sid)


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
            "SELECT id, url, label, identity_key, refresh_cron, model, created_at "
            "FROM sources ORDER BY id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


@app.post("/sources", status_code=201)
def create_source(src: SourceIn) -> dict[str, Any]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sources (url, label, schema, anchor, identity_key, refresh_cron, model) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (url) DO UPDATE SET label = EXCLUDED.label, "
            "schema = EXCLUDED.schema, anchor = EXCLUDED.anchor, "
            "identity_key = EXCLUDED.identity_key, refresh_cron = EXCLUDED.refresh_cron, "
            "model = EXCLUDED.model, updated_at = now() RETURNING id",
            (
                src.url,
                src.label,
                Jsonb(src.schema_),
                src.anchor,
                src.identity_key,
                src.refresh_cron,
                src.model,
            ),
        )
        sid = cur.fetchone()[0]
    return {"id": sid}


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
            "SELECT id, identity, data, confidence, first_seen, last_seen, stale "
            "FROM entities WHERE source_id = %s ORDER BY last_seen DESC LIMIT %s",
            (source_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


@app.get("/runs")
def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, source_id, started_at, finished_at, backend, confidence, "
            "entity_count, new_count, updated_count, stale_count, cost_usd, error "
            "FROM runs ORDER BY started_at DESC LIMIT %s",
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
