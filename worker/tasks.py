"""Job handler registry - the "packaging, not rewriting" proof point.

Each handler is a thin wrapper: it unpacks a job payload and calls existing
`modules/*.py` functions completely unchanged, then hands the result back to
the queue to persist. No audit logic lives in this file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from modules.auditor import audit_url
from modules.crawler import crawl_site
from worker.crawl_service import (
    build_module_crawl_config,
    finalize_crawl,
    load_resume_state,
    persist_result,
)
from worker.db.models import Crawl, CrawlConfig, Project
from worker.db.session import SessionLocal
from worker.queue import register


@register("audit.page")
def handle_audit_page(payload: dict) -> dict:
    """Runs the existing single-URL audit pipeline unchanged. `payload`
    matches `audit_url`'s keyword arguments (url, audit_type, check_links,
    validate_links, fetch_pagespeed, psi_api_key)."""
    return audit_url(**payload)


def _mark_paused(crawl_id: int) -> None:
    """Leave a crawl in the resumable `paused` state (no finished_at, no
    site-audit aggregation - it isn't done). Re-enqueuing crawl.start resumes it."""
    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        if crawl is not None:
            crawl.status = "paused"
            db.commit()


@register("crawl.start")
def handle_crawl_start(payload: dict) -> dict:
    """Runs `modules.crawler.crawl_site()` for an existing queued Crawl row
    (`payload = {"crawl_id": int}`, created via `worker.crawl_service
    .create_crawl()`), persisting each page/link/issue as the crawl
    progresses via `persist_result`, and finalizing the summary scores on
    completion. Marks the Crawl row failed (and re-raises, so `queue.py`'s
    existing handling also marks the Job failed) if the crawl loop itself
    errors - a per-page failure inside `crawl_site` is already captured as an
    "error" outcome, not an exception here."""
    crawl_id = payload["crawl_id"]

    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        if crawl is None:
            raise ValueError(f"no such crawl_id={crawl_id!r}")
        project = db.get(Project, crawl.project_id)
        crawl_config = db.get(CrawlConfig, crawl.crawl_config_id)
        if crawl_config is None:
            raise ValueError(f"crawl {crawl_id} has no crawl_config_id set")

        crawl.status = "running"
        if crawl.started_at is None:
            crawl.started_at = datetime.now(timezone.utc)
        db.commit()

        module_config = build_module_crawl_config(project, crawl_config)

    # Resume (M2 T2.3): if this crawl already has persisted pages (a prior run
    # that was paused or interrupted), continue from where it left off instead of
    # re-crawling. An empty visited set = fresh start.
    resume_visited, resume_frontier = load_resume_state(crawl_id)
    resuming = bool(resume_visited)

    # Cooperative pause: poll the crawl's status between depth batches; if the UI
    # (or a shutdown) flipped it to "paused"/"pausing", stop cleanly - the crawl
    # stays resumable because every crawled page is already persisted.
    def _should_stop() -> bool:
        with SessionLocal() as db:
            status = db.execute(
                select(Crawl.status).where(Crawl.id == crawl_id)
            ).scalar_one_or_none()
        return status in ("paused", "pausing")

    try:
        result = crawl_site(
            module_config,
            on_result=lambda url, outcome: persist_result(crawl_id, url, outcome),
            resume_visited=resume_visited if resuming else None,
            resume_frontier=resume_frontier if resuming else None,
            should_stop=_should_stop,
        )
    except Exception:
        # Don't discard a partially-completed crawl: if pages were crawled, leave
        # it resumable (paused) rather than failing outright.
        _pages = load_resume_state(crawl_id)[0]
        if _pages:
            _mark_paused(crawl_id)
            return {"crawl_id": crawl_id, "status": "paused", "pages_crawled": len(_pages)}
        finalize_crawl(crawl_id, status="failed")
        raise

    if result.get("stats", {}).get("stopped"):
        _mark_paused(crawl_id)
        return {"crawl_id": crawl_id, "status": "paused",
                "pages_crawled": len(load_resume_state(crawl_id)[0])}

    finalize_crawl(crawl_id, status="completed")

    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        return {
            "crawl_id": crawl_id,
            "pages_crawled": crawl.pages_crawled,
            "health_score": crawl.health_score,
            "seo_score_avg": crawl.seo_score_avg,
        }
