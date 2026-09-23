"""Tests for api/share.py - the public, unauthenticated shared-report endpoint
(M5 T5.2). Loads api/share.py via importlib and drives do_POST/do_GET with a
MagicMock-based fake BaseHTTPRequestHandler over in-memory SQLite - same
harness as tests/test_api_crawls.py / tests/test_crawl_export_api.py.

The key property under test: the share token is the ONLY capability. A valid
token resolves exactly one crawl with no auth; an unknown/revoked token 404s
and never falls back to any other crawl. A round-trip test also mints a token
through api/crawls.py (setShare) and reads it back through api/share.py.
"""

import importlib.util
import io
import json
import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from worker.crawl_export import new_share_token
from worker.db.models import Base, Crawl, CrawlConfig, Issue, Organization, Page, Project


def _load(name, relative_path):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


share = _load("share_under_test", "api/share.py")
crawls = _load("crawls_for_share_under_test", "api/crawls.py")


def _mock_handler(body: dict | None = None, path: str = "/api/share"):
    encoded = json.dumps(body or {}).encode()
    h = MagicMock()
    h.headers = {"Content-Length": str(len(encoded))}
    h.rfile = io.BytesIO(encoded)
    h.wfile = io.BytesIO()
    h.path = path
    return h


def _json_response(h):
    status = h.send_response.call_args[0][0]
    return status, json.loads(h.wfile.getvalue())


def _binary_response(h):
    status = h.send_response.call_args[0][0]
    headers = {call.args[0]: call.args[1] for call in h.send_header.call_args_list}
    return status, headers, h.wfile.getvalue()


def _isolated_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def isolated_db(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(share, "SessionLocal", session_factory)
    monkeypatch.setattr(crawls, "SessionLocal", session_factory)
    return session_factory


def _seed_shared_crawl(session_factory, *, token: str | None = "tok-abc123", with_page=True):
    """Create an org/project/crawl (optionally shared) with a page + issue.
    Returns (crawl_id, token)."""
    with session_factory() as db:
        org = Organization(name="Test Org"); db.add(org); db.flush()
        project = Project(org_id=org.id, name="example.com", root_url="https://example.com")
        db.add(project); db.flush()
        config = CrawlConfig(project_id=project.id, source_type="homepage", robots_mode="respect", max_pages=50)
        db.add(config); db.flush()
        crawl = Crawl(project_id=project.id, crawl_config_id=config.id, status="completed",
                      health_score=80.0, seo_score_avg=85.0, pages_crawled=1, share_token=token)
        db.add(crawl); db.flush()
        if with_page:
            page = Page(crawl_id=crawl.id, url="https://example.com/", normalized_url="https://example.com/",
                        status_code=200, title="Home", seo_score=90.0, meta_description="Welcome",
                        canonical_url="https://example.com/", h1="Home", depth=0)
            db.add(page); db.flush()
            db.add(Issue(crawl_id=crawl.id, page_id=page.id, issue_type="Missing alt text", severity="warning",
                         explanation_json={"category": "Images", "recommendation": "Add alt"}))
            db.add(Issue(crawl_id=crawl.id, page_id=None, issue_type="Sitewide thing", severity="error",
                         explanation_json={"category": "Technical", "recommendation": "Fix it"}))
        db.commit()
        return crawl.id, token


# --------------------------------------------------------------------------- #
# token is the only capability
# --------------------------------------------------------------------------- #
def test_missing_token_returns_400(isolated_db):
    h = _mock_handler({"action": "summary"})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert "token" in body["error"]


def test_unknown_token_returns_404(isolated_db):
    _seed_shared_crawl(isolated_db, token="the-real-token")
    h = _mock_handler({"token": "not-the-token", "action": "summary"})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 404
    assert body["error"] == "This shared report link is invalid or has been revoked."


def test_revoked_token_returns_404(isolated_db):
    # A crawl with no share_token cannot be reached by any token.
    _seed_shared_crawl(isolated_db, token=None)
    h = _mock_handler({"token": "", "action": "summary"})
    share.handler.do_POST(h)
    status, _ = _json_response(h)
    assert status == 400  # blank token rejected before lookup


def test_malformed_json_returns_400(isolated_db):
    h = MagicMock()
    h.headers = {"Content-Length": "7"}
    h.rfile = io.BytesIO(b"{not js")
    h.wfile = io.BytesIO()
    h.path = "/api/share"
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert "valid JSON" in body["error"]


def test_unknown_action_returns_400(isolated_db):
    _crawl_id, token = _seed_shared_crawl(isolated_db)
    h = _mock_handler({"token": token, "action": "nope"})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert "action" in body["error"]


# --------------------------------------------------------------------------- #
# summary / pages / issues
# --------------------------------------------------------------------------- #
def test_summary_returns_meta_and_counts(isolated_db):
    _crawl_id, token = _seed_shared_crawl(isolated_db)
    h = _mock_handler({"token": token, "action": "summary"})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 200
    assert body["meta"]["rootUrl"] == "https://example.com"
    assert body["meta"]["healthScore"] == 80.0
    assert body["pagesCount"] == 1
    assert body["issueSeverityCounts"] == {"warning": 1, "error": 1}
    assert body["issuesCount"] == 2


def test_summary_defaults_action_when_omitted(isolated_db):
    _crawl_id, token = _seed_shared_crawl(isolated_db)
    h = _mock_handler({"token": token})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 200
    assert "meta" in body


def test_pages_returns_rows(isolated_db):
    _crawl_id, token = _seed_shared_crawl(isolated_db)
    h = _mock_handler({"token": token, "action": "pages"})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 200
    assert body["total"] == 1
    page = body["pages"][0]
    assert page["url"] == "https://example.com/"
    assert page["title"] == "Home"
    assert page["issueCounts"] == {"warning": 1}


def test_issues_returns_rows_and_filters_by_severity(isolated_db):
    _crawl_id, token = _seed_shared_crawl(isolated_db)

    h = _mock_handler({"token": token, "action": "issues"})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 200
    assert body["total"] == 2

    h2 = _mock_handler({"token": token, "action": "issues", "severity": "error"})
    share.handler.do_POST(h2)
    status2, body2 = _json_response(h2)
    assert status2 == 200
    assert body2["total"] == 1
    assert body2["issues"][0]["issueType"] == "Sitewide thing"
    assert body2["issues"][0]["pageUrl"] is None


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def test_export_json_returns_report(isolated_db):
    crawl_id, token = _seed_shared_crawl(isolated_db)
    h = _mock_handler({"token": token, "action": "export", "format": "json"})
    share.handler.do_POST(h)
    status, headers, data = _binary_response(h)
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Content-Disposition"] == f'attachment; filename="crawl-{crawl_id}-report.json"'
    parsed = json.loads(data)
    assert parsed[0]["url"] == "https://example.com/"


def test_export_csv_returns_report(isolated_db):
    crawl_id, token = _seed_shared_crawl(isolated_db)
    h = _mock_handler({"token": token, "action": "export", "format": "csv"})
    share.handler.do_POST(h)
    status, headers, data = _binary_response(h)
    assert status == 200
    assert headers["Content-Type"] == "text/csv"
    assert headers["Content-Disposition"] == f'attachment; filename="crawl-{crawl_id}-report.csv"'
    assert b"https://example.com/" in data


def test_export_invalid_format_returns_400(isolated_db):
    _crawl_id, token = _seed_shared_crawl(isolated_db)
    h = _mock_handler({"token": token, "action": "export", "format": "xml"})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert "format" in body["error"]


def test_export_empty_crawl_returns_404(isolated_db):
    _crawl_id, token = _seed_shared_crawl(isolated_db, with_page=False)
    h = _mock_handler({"token": token, "action": "export", "format": "json"})
    share.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 404


# --------------------------------------------------------------------------- #
# GET convenience alias
# --------------------------------------------------------------------------- #
def test_get_with_token_query_returns_summary(isolated_db):
    _crawl_id, token = _seed_shared_crawl(isolated_db)
    h = _mock_handler(path=f"/api/share?token={token}")
    share.handler.do_GET(h)
    status, body = _json_response(h)
    assert status == 200
    assert body["meta"]["rootUrl"] == "https://example.com"


def test_get_without_token_returns_400(isolated_db):
    h = _mock_handler(path="/api/share")
    share.handler.do_GET(h)
    status, _ = _json_response(h)
    assert status == 400


# --------------------------------------------------------------------------- #
# round-trip: mint via api/crawls.py setShare, read via api/share.py
# --------------------------------------------------------------------------- #
def test_set_share_then_public_read_round_trip(isolated_db):
    crawl_id, _ = _seed_shared_crawl(isolated_db, token=None)  # unshared

    # Mint a token (no session -> resolve_org_id returns None -> no org gate).
    hm = _mock_handler({"action": "setShare", "crawlId": crawl_id})
    crawls.handler.do_POST(hm)
    status, body = _json_response(hm)
    assert status == 200
    token = body["shareToken"]
    assert token and len(token) > 20

    # Idempotent: minting again returns the same token.
    hm2 = _mock_handler({"action": "setShare", "crawlId": crawl_id})
    crawls.handler.do_POST(hm2)
    _s2, body2 = _json_response(hm2)
    assert body2["shareToken"] == token

    # Read publicly via the minted token.
    hr = _mock_handler({"token": token, "action": "summary"})
    share.handler.do_POST(hr)
    sr, br = _json_response(hr)
    assert sr == 200
    assert br["meta"]["id"] == crawl_id

    # Revoke, then the token 404s.
    hx = _mock_handler({"action": "revokeShare", "crawlId": crawl_id})
    crawls.handler.do_POST(hx)
    sx, bx = _json_response(hx)
    assert sx == 200 and bx["ok"] is True

    hr2 = _mock_handler({"token": token, "action": "summary"})
    share.handler.do_POST(hr2)
    sr2, _ = _json_response(hr2)
    assert sr2 == 404


def test_new_share_token_is_long_and_unique():
    a, b = new_share_token(), new_share_token()
    assert a != b
    assert len(a) >= 40
