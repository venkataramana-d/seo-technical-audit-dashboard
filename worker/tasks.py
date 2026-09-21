"""Job handler registry - the "packaging, not rewriting" proof point.

Each handler is a thin wrapper: it unpacks a job payload and calls existing
`modules/*.py` functions completely unchanged, then hands the result back to
the queue to persist. No audit logic lives in this file.
"""

from __future__ import annotations

from datetime import datetime, timezone

from modules.auditor import audit_url
from modules.crawler import crawl_site
from worker.crawl_service import build_module_crawl_config, finalize_crawl, persist_result
from worker.db.models import Crawl, CrawlConfig, Project
from worker.db.session import SessionLocal
from worker.queue import register


@register("audit.page")
def handle_audit_page(payload: dict) -> dict:
    """Runs the existing single-URL audit pipeline unchanged. `payload`
    matches `audit_url`'s keyword arguments (url, audit_type, check_links,
    validate_links, fetch_pagespeed, psi_api_key)."""
    return audit_url(**payload)


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
        crawl.started_at = datetime.now(timezone.utc)
        db.commit()

        module_config = build_module_crawl_config(project, crawl_config)

    try:
        crawl_site(module_config, on_result=lambda url, outcome: persist_result(crawl_id, url, outcome))
    except Exception:
        finalize_crawl(crawl_id, status="failed")
        raise

    finalize_crawl(crawl_id, status="completed")

    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        return {
            "crawl_id": crawl_id,
            "pages_crawled": crawl.pages_crawled,
            "health_score": crawl.health_score,
            "seo_score_avg": crawl.seo_score_avg,
        }
