"""Background scheduler — runs as its own container.

Standard DevOps API/worker split: the scraper API container handles HTTP
requests and writes source/cron config to Postgres. This worker container
reads that config, owns an APScheduler, and fires `run_source` jobs on each
source's per-source cron. Both containers share the same image; only the
entrypoint differs (compose's `command:` override).

Reconciliation: every 30 seconds we pull all sources from Postgres and sync
the scheduler's job set. Add new sources, remove deleted ones, replace cron
expressions on changed ones. So a UI cron edit propagates to the worker
within at most 30s — no inter-process notification needed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from prometheus_client import start_http_server

from .db import conn, ensure_runtime_schema
from .metrics import polls_skipped_total
from .runner import run_source

WORKER_METRICS_PORT = int(os.environ.get("WORKER_METRICS_PORT", "8083"))

log = logging.getLogger("worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _job_id(source_id: int) -> str:
    return f"source-{source_id}"


def _wanted() -> dict[int, str | None]:
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT id, refresh_cron FROM sources")
        return {sid: cron for sid, cron in cur.fetchall()}


async def _safe_run(source_id: int) -> None:
    try:
        result = await run_source(source_id)
        primary = (result or {}).get("primary") or {}
        log.info(
            "scheduled run src=%s entities=%s new=%s updated=%s stale=%s cost=$%.4f",
            source_id,
            primary.get("entity_count", 0),
            primary.get("new", 0),
            primary.get("updated", 0),
            primary.get("stale", 0),
            primary.get("cost_usd", 0.0),
        )
    except Exception:
        log.exception("scheduled run failed src=%s", source_id)


def _source_id_from_event(event) -> str | None:
    """Extract source_id from an APScheduler event whose job_id is 'source-N'."""
    if not event.job_id or not event.job_id.startswith("source-"):
        return None
    return event.job_id[len("source-"):]


def _on_job_missed(event) -> None:
    """Scheduler tried to fire a job at its scheduled time but couldn't (e.g.
    misfire_grace_time exceeded — usually means the system was paused or the
    event loop was blocked)."""
    sid = _source_id_from_event(event)
    if sid is not None:
        polls_skipped_total.labels(source_id=sid, reason="misfire").inc()
        log.warning("scheduled poll missed src=%s (misfire grace exceeded)", sid)


def _on_max_instances_blocked(event) -> None:
    """Previous run is still in flight when the next cron tick fires. Our
    `max_instances=1` config means the new tick is dropped — the in-flight
    poll wins. Surfaces as poll-skipped-because-pile-up."""
    sid = _source_id_from_event(event)
    if sid is not None:
        polls_skipped_total.labels(source_id=sid, reason="max_instances_blocked").inc()
        log.warning(
            "scheduled poll dropped src=%s (previous run still in flight)", sid,
        )


def _reconcile(scheduler: AsyncIOScheduler) -> None:
    wanted = _wanted()
    have = {j.id for j in scheduler.get_jobs() if j.id.startswith("source-")}
    wanted_ids = {_job_id(sid) for sid in wanted}

    for sid, cron in wanted.items():
        job_id = _job_id(sid)
        if not cron:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
                log.info("removed schedule for src=%s (cron unset)", sid)
            continue
        try:
            trigger = CronTrigger.from_crontab(cron)
        except Exception as e:
            log.warning("invalid cron for src=%s ('%s'): %s", sid, cron, e)
            continue
        scheduler.add_job(
            _safe_run,
            trigger=trigger,
            args=[sid],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=60,
            coalesce=True,
            max_instances=1,
        )

    for stale_id in have - wanted_ids:
        scheduler.remove_job(stale_id)
        log.info("removed orphan job %s", stale_id)


async def main() -> None:
    ensure_runtime_schema()

    start_http_server(WORKER_METRICS_PORT)
    log.info("worker /metrics listening on :%s", WORKER_METRICS_PORT)

    scheduler = AsyncIOScheduler()
    scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)
    scheduler.add_listener(_on_max_instances_blocked, EVENT_JOB_MAX_INSTANCES)
    scheduler.start()
    log.info("worker started — initial reconcile")
    _reconcile(scheduler)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=30.0)
            except TimeoutError:
                _reconcile(scheduler)
    finally:
        log.info("worker shutting down")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
