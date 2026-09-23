"""M5 T5.4 - opt-in, resumable, time-bounded server-side crawl executor.

The deployment target is Vercel with **no always-on worker** (Railway was
declined), so recurring crawls can't rely on a long-lived background process.
This module is the *only* way an unattended crawl runs without a worker: a
Vercel Cron hit (`api/cron.py`) periodically calls `process_due()`, which fires
any due schedules and then executes a bounded chunk of work inside the cron
function's own time limit. Large crawls therefore complete across several cron
ticks via the existing resume machinery (`load_resume_state` +
`crawl_site(resume_visited=..., resume_frontier=...)`), never re-crawling a page
that's already persisted.

Two environment flags gate the whole thing (documented on `api/cron.py`):
  * ``CRON_SECRET``          - if set, `api/cron.py` requires
    ``Authorization: Bearer <CRON_SECRET>`` (Vercel sends this automatically).
  * ``CRON_EXECUTE_CRAWLS``  - server-side execution is **OFF by default**;
    `api/cron.py` only calls `process_due()` when this is truthy. When it's
    off, cron still *fires* due schedules (creates queued Crawls) but leaves
    them for the normal browser-orchestrated path to run.

ANTI-RACE / ANTI-HIJACK ELIGIBILITY (critical)
----------------------------------------------
Browser-orchestrated crawls must never be hijacked by the cron executor. A
fresh crawl is therefore only claimed when its ``CrawlConfig.schedule_cron IS
NOT NULL`` **and** ``Crawl.status == 'queued'`` **and** ``Crawl.started_at IS
NULL``, and the claim is a single conditional ``UPDATE`` (set
``status='running'`` + ``started_at=now``) whose ``rowcount`` proves this
process won the row - if another process (or a browser) already moved it, the
update matches nothing and we skip it. A crawl a chunk left `paused` mid-run
(its own, previously-started scheduled work) is resumed the same atomic way; a
`paused` crawl is idle and every page it produced is already persisted, so
resuming it can't collide with an in-flight fetch.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select, update

from modules.crawler import crawl_site
from worker.crawl_service import (
    build_module_crawl_config,
    finalize_crawl,
    load_resume_state,
    persist_result,
)
from worker.db.models import Crawl, CrawlConfig, Project
from worker.db.session import SessionLocal
from worker.scheduler import enqueue_due_crawls

logger = logging.getLogger(__name__)


def _mark_status(crawl_id: int, status: str) -> None:
    """Flip a crawl's status in its own short transaction (best-effort)."""
    try:
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is not None:
                crawl.status = status
                db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not set crawl %s status=%s", crawl_id, status)


def run_scheduled_crawl_chunk(crawl_id: int, time_budget_seconds: float = 45) -> dict:
    """Claim + advance ONE scheduled crawl by up to ``time_budget_seconds`` of
    wall-clock work, then hand control back so the cron function returns well
    within Vercel's per-invocation timeout.

    Resume-safe: `load_resume_state` reconstructs the visited set + pending
    frontier from what's already persisted, and `crawl_site` continues from
    there rather than re-crawling. Each page is streamed to the DB via
    `persist_result` as it completes, so an interrupted chunk loses nothing.

    Termination:
      * frontier exhausted (or max_pages/max_depth reached) -> ``finalize_crawl(
        crawl_id, "completed")`` (which runs the sitewide audit, scores, and
        the regression-alert hook), returned as ``finished=True``.
      * time budget hit (``stats.timed_out``) or a cooperative UI pause
        (``stats.stopped``) -> leave the crawl ``paused`` (resumable; a later
        cron tick picks it back up), returned as ``finished=False``.

    Best-effort: any hard error is caught and logged. If pages were already
    persisted the crawl is left ``paused`` (resumable) rather than thrown away;
    only a crawl that produced nothing is finalized ``failed``.
    """
    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        if crawl is None:
            return {"crawl_id": crawl_id, "status": "missing", "pages_crawled": 0,
                    "finished": False, "error": "no such crawl"}
        project = db.get(Project, crawl.project_id)
        crawl_config = db.get(CrawlConfig, crawl.crawl_config_id)
        if project is None or crawl_config is None:
            return {"crawl_id": crawl_id, "status": crawl.status,
                    "pages_crawled": crawl.pages_crawled or 0, "finished": False,
                    "error": "crawl has no project/crawl_config"}
        # Idempotent claim/advance: process_due normally already flipped this to
        # running atomically, but keep run_scheduled_crawl_chunk usable stand-alone.
        crawl.status = "running"
        if crawl.started_at is None:
            crawl.started_at = datetime.now(timezone.utc)
        db.commit()
        module_config = build_module_crawl_config(project, crawl_config)

    # Wall-clock budget: crawl_site checks this between depth batches and stops
    # cleanly (stats.timed_out) rather than being killed mid-request. Floor at
    # 1s so a tiny/negative split can still make progress.
    budget = max(1.0, float(time_budget_seconds))
    module_config.max_seconds = budget

    resume_visited, resume_frontier = load_resume_state(crawl_id)
    resuming = bool(resume_visited)

    def _should_stop() -> bool:
        # Cooperative pause only (the time budget is handled by max_seconds):
        # if the UI flipped the crawl to paused/pausing, stop cleanly.
        try:
            with SessionLocal() as db:
                status = db.execute(
                    select(Crawl.status).where(Crawl.id == crawl_id)
                ).scalar_one_or_none()
        except Exception:  # noqa: BLE001
            return False
        return status in ("paused", "pausing")

    try:
        result = crawl_site(
            module_config,
            on_result=lambda url, outcome: persist_result(crawl_id, url, outcome),
            resume_visited=resume_visited if resuming else None,
            resume_frontier=resume_frontier if resuming else None,
            should_stop=_should_stop,
        )
    except Exception:  # noqa: BLE001 - best-effort; never let one crawl break the tick
        logger.exception("scheduled crawl chunk crashed for crawl %s", crawl_id)
        persisted = load_resume_state(crawl_id)[0]
        if persisted:
            _mark_status(crawl_id, "paused")  # resumable - don't discard partial work
            return {"crawl_id": crawl_id, "status": "paused", "pages_crawled": len(persisted),
                    "finished": False, "error": "chunk error (resumable)"}
        finalize_crawl(crawl_id, "failed")
        return {"crawl_id": crawl_id, "status": "failed", "pages_crawled": 0,
                "finished": False, "error": "hard error"}

    stats = result.get("stats", {})
    pages_crawled = len(load_resume_state(crawl_id)[0])

    if stats.get("timed_out") or stats.get("stopped"):
        # Budget ran out mid-crawl (or a UI pause) - leave it resumable.
        _mark_status(crawl_id, "paused")
        return {"crawl_id": crawl_id, "status": "paused", "pages_crawled": pages_crawled,
                "finished": False}

    # Frontier exhausted (or max_pages/max_depth reached) -> the crawl is done.
    finalize_crawl(crawl_id, "completed")
    return {"crawl_id": crawl_id, "status": "completed", "pages_crawled": pages_crawled,
            "finished": True}


def _claim_next_eligible(now: datetime) -> int | None:
    """Atomically claim the next eligible scheduled crawl, or return None.

    Resumes an already-started `paused` scheduled crawl first (finish in-flight
    work before starting new), then claims a fresh `queued` one. Each claim is a
    conditional UPDATE guarded by the same predicate it selected on, so a row
    another process moved between the SELECT and the UPDATE is skipped
    (rowcount == 0). See the module docstring's anti-race rule.
    """
    with SessionLocal() as db:
        # Phase 1 - resume our own previously-started, incomplete scheduled crawls.
        resumable = (
            db.execute(
                select(Crawl.id)
                .join(CrawlConfig, Crawl.crawl_config_id == CrawlConfig.id)
                .where(CrawlConfig.schedule_cron.isnot(None), Crawl.status == "paused")
                .order_by(Crawl.id.asc())
            )
            .scalars()
            .all()
        )
        for cid in resumable:
            res = db.execute(
                update(Crawl)
                .where(Crawl.id == cid, Crawl.status == "paused")
                .values(status="running")
            )
            db.commit()
            if res.rowcount == 1:
                return cid

        # Phase 2 - claim a fresh scheduled crawl (queued + never started).
        fresh = (
            db.execute(
                select(Crawl.id)
                .join(CrawlConfig, Crawl.crawl_config_id == CrawlConfig.id)
                .where(
                    CrawlConfig.schedule_cron.isnot(None),
                    Crawl.status == "queued",
                    Crawl.started_at.is_(None),
                )
                .order_by(Crawl.id.asc())
            )
            .scalars()
            .all()
        )
        for cid in fresh:
            res = db.execute(
                update(Crawl)
                .where(Crawl.id == cid, Crawl.status == "queued", Crawl.started_at.is_(None))
                .values(status="running", started_at=now)
            )
            db.commit()
            if res.rowcount == 1:
                return cid

    return None


def process_due(now: datetime | None = None, max_crawls: int = 3,
                time_budget_seconds: float = 45) -> dict:
    """Fire due schedules, then run up to ``max_crawls`` eligible crawls within
    a shared ``time_budget_seconds``.

    Steps:
      1. ``enqueue_due_crawls(now)`` - creates a queued Crawl + Job for every
         due CrawlConfig and advances its next_run_at (reused as-is).
      2. Claim and run eligible crawls one at a time via
         `run_scheduled_crawl_chunk`, atomically (see `_claim_next_eligible` and
         the module docstring's anti-race rule). The budget is split across the
         remaining slots dynamically, so a crawl that finishes early hands its
         leftover time to the next one.

    Returns ``{"enqueued": [...], "ran": [...], "finished": [...]}``.
    """
    now = now or datetime.now(timezone.utc)
    enqueued = enqueue_due_crawls(now)

    ran: list[int] = []
    finished: list[int] = []
    overall_start = time.monotonic()

    for i in range(max(0, max_crawls)):
        elapsed = time.monotonic() - overall_start
        remaining = time_budget_seconds - elapsed
        if remaining <= 0:
            break
        slots_left = max_crawls - i
        chunk_budget = remaining / slots_left

        crawl_id = _claim_next_eligible(now)
        if crawl_id is None:
            break

        try:
            result = run_scheduled_crawl_chunk(crawl_id, time_budget_seconds=chunk_budget)
        except Exception:  # noqa: BLE001 - one bad crawl must not sink the tick
            logger.exception("run_scheduled_crawl_chunk raised for crawl %s", crawl_id)
            _mark_status(crawl_id, "paused")
            ran.append(crawl_id)
            continue

        ran.append(crawl_id)
        if result.get("finished"):
            finished.append(crawl_id)

    return {"enqueued": enqueued, "ran": ran, "finished": finished}
