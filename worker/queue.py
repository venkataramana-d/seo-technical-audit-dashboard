"""DB-backed job queue - stands in for Redis+Celery/arq during local dev.

Same two-function shape (`enqueue()` / a worker that pulls and processes)
described in `03-DATA-MODEL-AND-API.md` §3, so swapping to a real queue later
only means replacing this module's internals, not any caller.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select, update

from worker.db.models import Job
from worker.db.session import SessionLocal

logger = logging.getLogger("worker")

JobHandler = Callable[[dict], dict]
_HANDLERS: dict[str, JobHandler] = {}


def register(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """Decorator: `@register("audit.page")` maps a job_type to its handler."""

    def decorator(fn: JobHandler) -> JobHandler:
        _HANDLERS[job_type] = fn
        return fn

    return decorator


def enqueue(job_type: str, payload: dict) -> int:
    """Insert a queued Job row, return its id."""
    with SessionLocal() as db:
        job = Job(job_type=job_type, payload_json=payload, status="queued")
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id


def _claim_next_job(db) -> Job | None:
    """Atomically claim the oldest queued job: a single UPDATE ... RETURNING
    keyed off a correlated subquery, so two workers polling concurrently
    can't both claim the same row. Works unchanged on SQLite 3.35+ and
    Postgres - no dialect-specific SQL.
    """
    next_id_subq = (
        select(Job.id).where(Job.status == "queued").order_by(Job.id).limit(1).scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == next_id_subq)
        .values(status="running", started_at=func.now())
        .returning(Job.id)
    )
    row = db.execute(stmt).first()
    db.commit()
    if row is None:
        return None
    return db.get(Job, row[0])


class Worker:
    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval

    def run_once(self) -> bool:
        """Claim and process a single queued job, if one exists. Returns
        True if a job was processed, False if the queue was empty. The
        caller drives the loop (see worker/__main__.py, which also
        interleaves a periodic scheduler check between calls) - kept this
        way so tests don't need an interruptible loop either."""
        with SessionLocal() as db:
            job = _claim_next_job(db)
            if job is None:
                return False
            job_id, job_type, payload = job.id, job.job_type, job.payload_json

        start = time.monotonic()
        handler = _HANDLERS.get(job_type)
        with SessionLocal() as db:
            db_job = db.get(Job, job_id)
            try:
                if handler is None:
                    raise ValueError(f"no handler registered for job_type={job_type!r}")
                db_job.result_json = handler(payload)
                db_job.status = "completed"
            except Exception as exc:  # noqa: BLE001 - job failures must not crash the worker
                db_job.status = "failed"
                db_job.error = f"{type(exc).__name__}: {exc}"
                logger.exception("job %s (%s) failed", job_id, job_type)
            finally:
                db_job.finished_at = datetime.now(timezone.utc)
                db.commit()
                status = db_job.status

        logger.info(
            "job id=%s type=%s status=%s duration=%.2fs",
            job_id,
            job_type,
            status,
            time.monotonic() - start,
        )
        return True
