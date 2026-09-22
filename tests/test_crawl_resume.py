"""M2 T2.3: resume + cooperative-pause behaviour.

Two layers:
- crawl_site() accepts a pre-seeded visited set + pending frontier and a
  should_stop() callback (pure-ish; fetch is stubbed).
- the worker resumes from persisted pages and leaves an interrupted/paused
  crawl in the resumable `paused` state instead of failing.
"""

import pytest

import modules.crawler as crawler
from modules.crawler import CrawlConfig, crawl_site


def _stub_fetch(monkeypatch, pages):
    """Stub fetch_page + link extraction so crawl_site runs without network.
    `pages` maps normalized url -> list of internal link urls it contains."""
    def fake_fetch(url, *a, **k):
        return {"success": True, "status_code": 200, "final_url": url, "soup": object(),
                "redirect_count": 0}
    monkeypatch.setattr(crawler, "fetch_page", fake_fetch)
    monkeypatch.setattr(crawler, "_extract_internal_links",
                        lambda soup, base, dom, cfg: pages.get(base, []))
    # robots always allowed, no throttle
    monkeypatch.setattr(crawler, "_robots_allowed", lambda *a, **k: (True, None))


def _cfg(**kw):
    return CrawlConfig(seed_url="https://example.com/", max_pages=50, max_depth=5,
                       robots_mode="ignore", render_js=False, run_full_audit=False,
                       crawl_delay=0, max_workers=2, **kw)


def test_should_stop_pauses_cleanly(monkeypatch):
    _stub_fetch(monkeypatch, {
        "https://example.com/": ["https://example.com/a", "https://example.com/b"],
        "https://example.com/a": [], "https://example.com/b": [],
    })
    # Stop immediately: no depth batch should run.
    res = crawl_site(_cfg(), should_stop=lambda: True)
    assert res["stats"]["stopped"] is True
    assert res["stats"]["pages_crawled"] == 0


def test_resume_skips_visited_and_uses_frontier(monkeypatch):
    _stub_fetch(monkeypatch, {
        "https://example.com/a": ["https://example.com/c"],
        "https://example.com/c": [],
    })
    # Home already crawled; resume with /a and /b pending. /a discovers /c.
    res = crawl_site(
        _cfg(),
        resume_visited=["https://example.com/"],
        resume_frontier=["https://example.com/a", "https://example.com/b"],
    )
    crawled = {p["url"] for p in res["pages"]}
    assert "https://example.com/" not in crawled  # visited, skipped
    assert "https://example.com/a" in crawled
    assert "https://example.com/c" in crawled     # discovered during resume
    assert res["stats"]["stopped"] is False


# ── Worker-level resume / pause (reuses test_worker_crawl's isolated DB) ──────

from tests.test_worker_crawl import isolated_db, _fake_crawl_site_success  # noqa: E402,F401
import worker.tasks as tasks  # noqa: E402
import worker.crawl_service as crawl_service  # noqa: E402
from worker.db.models import Crawl, Page  # noqa: E402


def test_load_resume_state_from_persisted_pages(monkeypatch, isolated_db):
    monkeypatch.setattr(tasks, "crawl_site", _fake_crawl_site_success)
    with isolated_db() as db:
        crawl = crawl_service.create_crawl(db, "https://example.com", max_pages=10)
        crawl_id = crawl.id
    # Run the fake 2-page crawl to persist pages + internal link (/about).
    tasks.handle_crawl_start({"crawl_id": crawl_id})
    visited, frontier = crawl_service.load_resume_state(crawl_id)
    assert "https://example.com/" in visited
    assert "https://example.com/about" in visited
    # /about was discovered as an internal link AND crawled, so not in frontier.
    assert "https://example.com/about" not in frontier


def test_interrupted_crawl_with_pages_is_paused_not_failed(monkeypatch, isolated_db):
    # A crawl that persists a page then raises must be left resumable (paused),
    # not failed, so no work is lost.
    def crawl_then_raise(config, on_result=None, progress_callback=None, **kwargs):
        on_result("https://example.com/", {"page": {
            "url": "https://example.com/", "status_code": 200,
            "audit": {"seo_score": 70.0, "metadata": {}, "headings": {}, "all_issues": []},
        }})
        raise RuntimeError("boom mid-crawl")
    monkeypatch.setattr(tasks, "crawl_site", crawl_then_raise)
    with isolated_db() as db:
        crawl_id = crawl_service.create_crawl(db, "https://example.com", max_pages=10).id
    out = tasks.handle_crawl_start({"crawl_id": crawl_id})
    assert out["status"] == "paused"
    with isolated_db() as db:
        assert db.get(Crawl, crawl_id).status == "paused"
