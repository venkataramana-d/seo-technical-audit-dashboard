"""Tests for worker/crawl_diff.py - comparing two crawls of the same
project. Seeds two crawls with hand-crafted Page/Issue rows designed to
exercise new/fixed issues, regressed/improved pages, and score deltas."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from worker import crawl_diff
from worker.db.models import Base, Crawl, Issue, Organization, Page, Project


def _isolated_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def isolated_db(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(crawl_diff, "SessionLocal", session_factory)
    return session_factory


def _seed_two_crawls(session_factory):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with session_factory() as db:
        org = Organization(name="Test Org")
        db.add(org)
        db.flush()
        project = Project(org_id=org.id, name="example.com", root_url="https://example.com")
        db.add(project)
        db.flush()

        old_crawl = Crawl(
            project_id=project.id, status="completed", health_score=50.0, seo_score_avg=85.0, finished_at=t0,
        )
        new_crawl = Crawl(
            project_id=project.id, status="completed", health_score=75.0, seo_score_avg=80.0,
            finished_at=t0 + timedelta(days=7),
        )
        db.add_all([old_crawl, new_crawl])
        db.flush()

        # Old crawl: /a (score 80, has "Missing alt text"), /b (score 90)
        old_a = Page(crawl_id=old_crawl.id, url="https://example.com/a", normalized_url="https://example.com/a",
                     status_code=200, seo_score=80.0)
        old_b = Page(crawl_id=old_crawl.id, url="https://example.com/b", normalized_url="https://example.com/b",
                     status_code=200, seo_score=90.0)
        db.add_all([old_a, old_b])
        db.flush()
        db.add(Issue(crawl_id=old_crawl.id, page_id=old_a.id, issue_type="Missing alt text", severity="notice"))
        db.add(Issue(crawl_id=old_crawl.id, page_id=None, issue_type="Duplicate title", severity="warning"))

        # New crawl: /a regressed (80->60, alt-text issue fixed), /b improved (90->95, new issue),
        # /c is a brand new page (no prior score to compare)
        new_a = Page(crawl_id=new_crawl.id, url="https://example.com/a", normalized_url="https://example.com/a",
                     status_code=200, seo_score=60.0)
        new_b = Page(crawl_id=new_crawl.id, url="https://example.com/b", normalized_url="https://example.com/b",
                     status_code=200, seo_score=95.0)
        new_c = Page(crawl_id=new_crawl.id, url="https://example.com/c", normalized_url="https://example.com/c",
                     status_code=200, seo_score=70.0)
        db.add_all([new_a, new_b, new_c])
        db.flush()
        db.add(Issue(crawl_id=new_crawl.id, page_id=new_b.id, issue_type="New issue found", severity="warning"))
        db.add(Issue(crawl_id=new_crawl.id, page_id=None, issue_type="Duplicate title", severity="warning"))  # unchanged
        db.add(Issue(crawl_id=new_crawl.id, page_id=None, issue_type="Orphan pages", severity="warning"))  # new sitewide

        db.commit()
        return project.id, old_crawl.id, new_crawl.id


def test_compare_crawls_finds_new_and_fixed_issues(isolated_db):
    _project_id, old_id, new_id = _seed_two_crawls(isolated_db)

    diff = crawl_diff.compare_crawls(old_id, new_id)

    assert {"url": "https://example.com/b", "issue_type": "New issue found"} in diff["new_issues"]
    assert {"url": None, "issue_type": "Orphan pages"} in diff["new_issues"]
    # unchanged sitewide issue shouldn't appear as new
    assert not any(i["issue_type"] == "Duplicate title" for i in diff["new_issues"])

    assert diff["fixed_issues"] == [{"url": "https://example.com/a", "issue_type": "Missing alt text"}]


def test_compare_crawls_finds_regressed_and_improved_pages(isolated_db):
    _project_id, old_id, new_id = _seed_two_crawls(isolated_db)

    diff = crawl_diff.compare_crawls(old_id, new_id)

    assert diff["regressed_pages"] == [
        {"url": "https://example.com/a", "old_score": 80.0, "new_score": 60.0, "delta": -20.0}
    ]
    assert diff["improved_pages"] == [
        {"url": "https://example.com/b", "old_score": 90.0, "new_score": 95.0, "delta": 5.0}
    ]
    # /c is new to this crawl (no prior score) - shouldn't appear in either list
    assert not any(p["url"] == "https://example.com/c" for p in diff["regressed_pages"] + diff["improved_pages"])


def test_compare_crawls_computes_score_deltas(isolated_db):
    _project_id, old_id, new_id = _seed_two_crawls(isolated_db)

    diff = crawl_diff.compare_crawls(old_id, new_id)

    assert diff["health_score_delta"] == 25.0  # 75.0 - 50.0
    assert diff["seo_score_avg_delta"] == -5.0  # 80.0 - 85.0


def test_get_score_trend_returns_chronological_history(isolated_db):
    project_id, old_id, new_id = _seed_two_crawls(isolated_db)

    trend = crawl_diff.get_score_trend(project_id)

    assert [t["crawl_id"] for t in trend] == [old_id, new_id]
    assert trend[0]["health_score"] == 50.0
    assert trend[1]["health_score"] == 75.0


def test_get_previous_completed_crawl_returns_the_prior_one(isolated_db):
    _project_id, old_id, new_id = _seed_two_crawls(isolated_db)

    prev = crawl_diff.get_previous_completed_crawl(new_id)

    assert prev is not None
    assert prev.id == old_id


def test_get_previous_completed_crawl_returns_none_for_the_first_crawl(isolated_db):
    old_id, _new_id = _seed_two_crawls(isolated_db)[1:]

    assert crawl_diff.get_previous_completed_crawl(old_id) is None
