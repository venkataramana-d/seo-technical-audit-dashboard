"""M3 Tier C #7 (per-page crawl depth) + Tier B #5 (per-crawl thematic scores).

Reuses tests/test_worker_crawl.py's isolated in-memory DB fixture and its faked
2-page crawl (home -> /about). finalize_crawl (on a completed crawl) is expected
to write Page.depth (BFS click-depth from the homepage) and
Crawl.theme_scores_json (per-theme 0-100 score from the crawl's Issue rows).
"""

from sqlalchemy import select

from tests.test_worker_crawl import isolated_db, _fake_crawl_site_success  # noqa: F401
import worker.tasks as tasks
import worker.crawl_service as crawl_service
from worker.db.models import Crawl, Page


def test_completed_crawl_persists_page_depth(monkeypatch, isolated_db):
    # The faked crawl: home ("https://example.com/") links to /about, /about
    # links nowhere. So home is depth 0, /about is depth 1 (one click away).
    monkeypatch.setattr(tasks, "crawl_site", _fake_crawl_site_success)
    with isolated_db() as db:
        crawl_id = crawl_service.create_crawl(db, "https://example.com", max_pages=10).id
    tasks.handle_crawl_start({"crawl_id": crawl_id})

    with isolated_db() as db:
        pages = db.execute(select(Page).where(Page.crawl_id == crawl_id)).scalars().all()
        by_url = {p.normalized_url: p for p in pages}
        assert by_url["https://example.com/"].depth == 0  # homepage / root
        assert by_url["https://example.com/about"].depth == 1  # one click from home


def test_completed_crawl_persists_theme_scores(monkeypatch, isolated_db):
    # The faked crawl emits two issues: an "Images"/Low finding (penalty 2 ->
    # Images theme = 98) and a "Headings"/Critical finding (penalty 25 ->
    # Content theme = 75, since "Heading" is a Content-theme keyword).
    monkeypatch.setattr(tasks, "crawl_site", _fake_crawl_site_success)
    with isolated_db() as db:
        crawl_id = crawl_service.create_crawl(db, "https://example.com", max_pages=10).id
    tasks.handle_crawl_start({"crawl_id": crawl_id})

    with isolated_db() as db:
        crawl = db.get(Crawl, crawl_id)
        scores = crawl.theme_scores_json
        assert isinstance(scores, dict) and scores, "expected non-empty theme scores"
        assert scores["Images"] == 98      # 100 - PENALTY[Low](2)
        assert scores["Content"] == 75     # 100 - PENALTY[Critical](25)
        # Every score is clamped to the 0-100 range.
        assert all(0 <= v <= 100 for v in scores.values())
