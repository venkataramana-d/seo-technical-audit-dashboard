"""Tests for worker/site_audit.py - the Phase 2 post-crawl aggregation pass.
Seeds a crawl with hand-crafted Page/Link rows designed to trigger each
check, then asserts the right sitewide/page-scoped Issue rows appear with
the right severity. No network access: discover_sitemap_urls is stubbed."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from worker import site_audit
from worker.db.models import Base, Crawl, Issue, Link, Organization, Page, Project


def _isolated_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def isolated_db(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(site_audit, "SessionLocal", session_factory)
    monkeypatch.setattr(site_audit, "discover_sitemap_urls", lambda root_url: [])
    return session_factory


def _seed_crawl(session_factory, root_url="https://example.com") -> int:
    with session_factory() as db:
        org = Organization(name="Test Org")
        db.add(org)
        db.flush()
        project = Project(org_id=org.id, name=root_url, root_url=root_url)
        db.add(project)
        db.flush()
        crawl = Crawl(project_id=project.id, status="running")
        db.add(crawl)
        db.commit()
        return crawl.id


def test_detects_duplicate_titles_and_content(isolated_db):
    crawl_id = _seed_crawl(isolated_db)
    with isolated_db() as db:
        db.add_all(
            [
                Page(crawl_id=crawl_id, url="https://example.com/a", normalized_url="https://example.com/a",
                     status_code=200, title="Same Title", content_hash="hash1"),
                Page(crawl_id=crawl_id, url="https://example.com/b", normalized_url="https://example.com/b",
                     status_code=200, title="Same Title", content_hash="hash1"),
                Page(crawl_id=crawl_id, url="https://example.com/c", normalized_url="https://example.com/c",
                     status_code=200, title="Unique Title", content_hash="hash2"),
            ]
        )
        db.commit()

    site_audit.run_site_audit(crawl_id)

    with isolated_db() as db:
        issues = db.execute(select(Issue).where(Issue.crawl_id == crawl_id)).scalars().all()
        by_type = {i.issue_type: i for i in issues}

        assert "Duplicate title" in by_type
        assert by_type["Duplicate title"].page_id is None  # aggregate finding, not one page
        assert by_type["Duplicate title"].severity == "warning"
        assert set(by_type["Duplicate title"].explanation_json["urls"]) == {
            "https://example.com/a", "https://example.com/b",
        }

        assert "Duplicate content" in by_type
        assert by_type["Duplicate content"].explanation_json["category"] == "Content"


def test_detects_orphan_pages_and_sitemap_gap(monkeypatch, isolated_db):
    crawl_id = _seed_crawl(isolated_db)
    monkeypatch.setattr(
        site_audit,
        "discover_sitemap_urls",
        lambda root_url: ["https://example.com/", "https://example.com/never-crawled"],
    )
    with isolated_db() as db:
        db.add(Page(crawl_id=crawl_id, url="https://example.com/", normalized_url="https://example.com/", status_code=200))
        db.add(Page(crawl_id=crawl_id, url="https://example.com/extra", normalized_url="https://example.com/extra", status_code=200))
        db.commit()

    site_audit.run_site_audit(crawl_id)

    with isolated_db() as db:
        issues = db.execute(select(Issue).where(Issue.crawl_id == crawl_id)).scalars().all()
        by_type = {i.issue_type: i for i in issues}

        orphan = by_type["Orphan pages (in sitemap, not linked internally)"]
        assert orphan.explanation_json["urls"] == ["https://example.com/never-crawled"]
        assert orphan.severity == "warning"

        extra = by_type["Crawled pages missing from sitemap"]
        assert extra.explanation_json["urls"] == ["https://example.com/extra"]
        assert extra.severity == "notice"


def test_detects_long_redirect_chain_and_loop(isolated_db):
    crawl_id = _seed_crawl(isolated_db)
    with isolated_db() as db:
        db.add(Page(
            crawl_id=crawl_id, url="https://example.com/long", normalized_url="https://example.com/long",
            status_code=200,
            redirect_chain_json=["https://example.com/1", "https://example.com/2", "https://example.com/3"],
        ))
        db.add(Page(
            # A genuine loop: the chain revisits a URL it already hit
            # (.../loop -> .../x -> .../loop again), not just a plain
            # single-hop redirect where chain[0] happens to be the page's
            # own originally-requested URL (that's every redirected page,
            # not a loop -- see the regression test below).
            crawl_id=crawl_id, url="https://example.com/loop", normalized_url="https://example.com/loop",
            status_code=200,
            redirect_chain_json=["https://example.com/loop", "https://example.com/x", "https://example.com/loop"],
        ))
        db.commit()

    site_audit.run_site_audit(crawl_id)

    with isolated_db() as db:
        issues = db.execute(select(Issue).where(Issue.crawl_id == crawl_id)).scalars().all()
        by_type_url = {(i.issue_type, i.page_id) for i in issues}
        pages = {p.url: p.id for p in db.execute(select(Page).where(Page.crawl_id == crawl_id)).scalars().all()}

        assert ("Long redirect chain", pages["https://example.com/long"]) in by_type_url
        assert ("Redirect loop", pages["https://example.com/loop"]) in by_type_url

        loop_issue = next(i for i in issues if i.issue_type == "Redirect loop")
        assert loop_issue.severity == "error"
        chain_issue = next(i for i in issues if i.issue_type == "Long redirect chain")
        assert chain_issue.severity == "warning"


def test_plain_single_hop_redirect_is_not_flagged_as_a_loop(isolated_db):
    """Regression test: chain[0] is always the page's own originally-requested
    URL (see fetch_page()'s redirect_history) -- an ordinary http -> https
    redirect (or any single healthy hop) must not be misreported as a
    "Redirect loop" error just because that URL trivially appears in its own
    chain."""
    crawl_id = _seed_crawl(isolated_db)
    with isolated_db() as db:
        db.add(Page(
            crawl_id=crawl_id, url="http://example.com/", normalized_url="http://example.com/",
            status_code=200,
            redirect_chain_json=["http://example.com/", "https://example.com/"],
        ))
        db.commit()

    site_audit.run_site_audit(crawl_id)

    with isolated_db() as db:
        issues = db.execute(select(Issue).where(Issue.crawl_id == crawl_id)).scalars().all()
        redirect_issues = [i for i in issues if i.issue_type in ("Redirect loop", "Long redirect chain")]
        assert redirect_issues == []


def test_detects_broken_internal_link_and_backfills_link_row(isolated_db):
    crawl_id = _seed_crawl(isolated_db)
    with isolated_db() as db:
        source = Page(crawl_id=crawl_id, url="https://example.com/source", normalized_url="https://example.com/source", status_code=200)
        db.add(source)
        db.add(Page(crawl_id=crawl_id, url="https://example.com/gone", normalized_url="https://example.com/gone", status_code=404))
        db.flush()
        link = Link(page_id=source.id, target_url="https://example.com/gone", link_type="internal")
        db.add(link)
        db.commit()
        link_id, source_id = link.id, source.id

    site_audit.run_site_audit(crawl_id)

    with isolated_db() as db:
        issues = db.execute(select(Issue).where(Issue.crawl_id == crawl_id, Issue.issue_type == "Broken internal link")).scalars().all()
        assert len(issues) == 1
        assert issues[0].page_id == source_id
        assert issues[0].explanation_json["status_code"] == 404
        assert issues[0].severity == "error"

        link_row = db.get(Link, link_id)
        assert link_row.is_broken is True
        assert link_row.status_code == 404


def test_detects_non_reciprocal_hreflang(isolated_db):
    crawl_id = _seed_crawl(isolated_db)
    with isolated_db() as db:
        db.add(Page(
            crawl_id=crawl_id, url="https://example.com/en", normalized_url="https://example.com/en", status_code=200,
            hreflang_json=[{"lang": "fr", "url": "https://example.com/fr"}],
        ))
        db.add(Page(
            crawl_id=crawl_id, url="https://example.com/fr", normalized_url="https://example.com/fr", status_code=200,
            hreflang_json=[],  # doesn't reciprocate
        ))
        db.commit()

    site_audit.run_site_audit(crawl_id)

    with isolated_db() as db:
        issues = db.execute(select(Issue).where(Issue.crawl_id == crawl_id, Issue.issue_type == "Non-reciprocal hreflang")).scalars().all()
        assert len(issues) == 1
        assert issues[0].explanation_json["target_url"] == "https://example.com/fr"
        assert issues[0].severity == "notice"


def test_get_thematic_report_groups_issues_with_severity_counts(isolated_db):
    crawl_id = _seed_crawl(isolated_db)
    with isolated_db() as db:
        db.add_all(
            [
                Issue(crawl_id=crawl_id, page_id=None, issue_type="Duplicate title", severity="warning",
                      explanation_json={"category": "Metadata", "recommendation": "fix it"}),
                Issue(crawl_id=crawl_id, page_id=None, issue_type="Broken internal link", severity="error",
                      explanation_json={"category": "Internal Links", "recommendation": "fix it"}),
                Issue(crawl_id=crawl_id, page_id=None, issue_type="Another broken link", severity="error",
                      explanation_json={"category": "Internal Links", "recommendation": "fix it"}),
            ]
        )
        db.commit()

    report = site_audit.get_thematic_report(crawl_id)

    assert report["Metadata"]["count"] == 1
    assert report["Metadata"]["by_severity"] == {"warning": 1}
    assert report["Links"]["count"] == 2
    assert report["Links"]["by_severity"] == {"error": 2}
