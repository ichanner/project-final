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
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import conn
from .runner import run_source

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
    except Exception:  # noqa: BLE001
        log.exception("scheduled run failed src=%s", source_id)


def _reconcile(scheduler: AsyncIOScheduler) -> None:
    wanted = _wanted()
    have = {j.id for j in scheduler.get_jobs() if j.id.startswith("source-")}
    wanted_ids = {_job_id(sid) for sid in wanted}

    # Add or update jobs
    for sid, cron in wanted.items():
        job_id = _job_id(sid)
        if not cron:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
                log.info("removed schedule for src=%s (cron unset)", sid)
            continue
        try:
            trigger = CronTrigger.from_crontab(cron)
        except Exception as e:  # noqa: BLE001
            log.warning("invalid cron for src=%s ('%s'): %s", sid, cron, e)
            continue
        # APScheduler treats add_job(replace_existing=True) as an upsert.
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

    # Remove jobs whose source is gone
    for stale_id in have - wanted_ids:
        scheduler.remove_job(stale_id)
        log.info("removed orphan job %s", stale_id)


async def main() -> None:
    scheduler = AsyncIOScheduler()
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
            except asyncio.TimeoutError:
                _reconcile(scheduler)
    finally:
        log.info("worker shutting down")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
