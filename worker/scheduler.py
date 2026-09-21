"""Phase 3 "Always-on" scheduling - a tick function, not a daemon. There's no
Redis/cloud infra here (still a single local worker process, per Phase 0's
scope), so recurring crawls work by having the worker's own loop
periodically call `enqueue_due_crawls()` (wired in `worker/__main__.py`)
rather than relying on OS-level cron or a distributed scheduler.

No separate `schedules` table: `CrawlConfig.schedule_cron`/`next_run_at`
(Phase 0 + this phase) already carry everything needed - a `schedules` table
per `03-DATA-MODEL-AND-API.md` would just duplicate `crawl_config_id` +
`cron_expr`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from worker.db.models import Crawl, CrawlConfig, Job
from worker.db.session import SessionLocal


def enqueue_due_crawls(now: datetime | None = None) -> list[int]:
    """Finds every CrawlConfig with a schedule that's due, enqueues a fresh
    Crawl run for each (reusing that same CrawlConfig - unlike create_crawl(),
    which creates a brand new config for a first/manual run), and advances
    next_run_at. Returns the new Crawl IDs, mainly useful for tests/logging."""
    now = now or datetime.now(timezone.utc)

    with SessionLocal() as db:
        due_configs = (
            db.execute(
                select(CrawlConfig).where(
                    CrawlConfig.schedule_cron.isnot(None),
                    (CrawlConfig.next_run_at.is_(None)) | (CrawlConfig.next_run_at <= now),
                )
            )
            .scalars()
            .all()
        )

        new_crawl_ids = []
        for config in due_configs:
            crawl = Crawl(
                project_id=config.project_id,
                crawl_config_id=config.id,
                status="queued",
                pages_total_estimate=config.max_pages,
            )
            db.add(crawl)
            db.flush()  # need crawl.id for the Job payload below

            # Insert the Job row directly in this same session/transaction
            # rather than calling worker.queue.enqueue() - that function
            # opens its own session and commits, which would try to grab
            # SQLite's single writer lock while this transaction is still
            # open and deadlock ("database is locked"). Keeping it one
            # transaction is also more correct: the new Crawl, its Job, and
            # the advanced next_run_at all commit together or not at all.
            db.add(Job(job_type="crawl.start", payload_json={"crawl_id": crawl.id}, status="queued"))
            new_crawl_ids.append(crawl.id)

            config.next_run_at = croniter(config.schedule_cron, now).get_next(datetime)

        db.commit()
        return new_crawl_ids
