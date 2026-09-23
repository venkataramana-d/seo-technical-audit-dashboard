"""Tests for api/crawl-export.py (M5 T5.2 server-side crawl export). Loads the
hyphen-named api/*.py via importlib and drives do_POST with a MagicMock-based
fake BaseHTTPRequestHandler over an in-memory SQLite DB - same harness as
tests/test_api_crawls.py."""

import importlib.util
import io
import json
import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from worker.auth import create_session_token, hash_password
from worker.db.models import (
    Base, Crawl, CrawlConfig, Issue, Link, Membership, Organization, Page, Project, User,
)


def _load(name, relative_path):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# api/crawl-export.py was consolidated into api/crawls.py (action "export") to
# stay under the Hobby plan's 12-serverless-function cap; the module var name is
# kept so the existing do_POST test bodies (all {"action": "export", ...}) stand.
crawl_export_api = _load("crawls_for_export_under_test", "api/crawls.py")


def _mock_handler(body: dict, cookie: str | None = None):
    encoded = json.dumps(body).encode()
    h = MagicMock()
    headers = {"Content-Length": str(len(encoded))}
    if cookie:
        headers["Cookie"] = cookie
    h.headers = headers
    h.rfile = io.BytesIO(encoded)
    h.wfile = io.BytesIO()
    return h


def _json_response(h):
    """Status + parsed JSON body, for the error paths (which use send_json)."""
    status = h.send_response.call_args[0][0]
    return status, json.loads(h.wfile.getvalue())


def _binary_response(h):
    """Status + response headers dict + raw bytes, for the success path (which
    writes the binary export straight to wfile, not via send_json)."""
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
    monkeypatch.setattr(crawl_export_api, "SessionLocal", session_factory)
    return session_factory


def _seed_crawl_with_page(session_factory, *, org_name="Test Org", with_page=True):
    """Create an org/project/crawl (+ optionally a page with an issue and a
    link). Returns (org_id, crawl_id)."""
    with session_factory() as db:
        org = Organization(name=org_name)
        db.add(org)
        db.flush()
        project = Project(org_id=org.id, name="example.com", root_url="https://example.com")
        db.add(project)
        db.flush()
        config = CrawlConfig(project_id=project.id, source_type="homepage", robots_mode="respect", max_pages=50)
        db.add(config)
        db.flush()
        crawl = Crawl(project_id=project.id, crawl_config_id=config.id, status="completed",
                      health_score=80.0, seo_score_avg=85.0, pages_crawled=1)
        db.add(crawl)
        db.flush()
        if with_page:
            page = Page(crawl_id=crawl.id, url="https://example.com/", normalized_url="https://example.com/",
                        status_code=200, title="Home", seo_score=90.0,
                        meta_description="Welcome", canonical_url="https://example.com/", h1="Home")
            db.add(page)
            db.flush()
            db.add(Issue(crawl_id=crawl.id, page_id=page.id, issue_type="Missing alt text",
                         severity="warning", explanation_json={"category": "Images", "recommendation": "Add alt"}))
            db.add(Link(page_id=page.id, target_url="https://example.com/about", link_type="internal",
                        anchor_text="About", is_dofollow=True, is_nofollow=False))
        db.commit()
        return org.id, crawl.id


def _seed_user_org_crawl(session_factory, email: str):
    """A user + their own org + a crawl with one page. Returns (user_id, crawl_id)."""
    with session_factory() as db:
        user = User(email=email, password_hash=hash_password("pw12345678"))
        db.add(user); db.flush()
        org = Organization(name=f"{email} org"); db.add(org); db.flush()
        db.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
        project = Project(org_id=org.id, name="site", root_url=f"https://{email}.example")
        db.add(project); db.flush()
        config = CrawlConfig(project_id=project.id, source_type="homepage", robots_mode="respect", max_pages=50)
        db.add(config); db.flush()
        crawl = Crawl(project_id=project.id, crawl_config_id=config.id, status="completed", pages_crawled=1)
        db.add(crawl); db.flush()
        db.add(Page(crawl_id=crawl.id, url=f"https://{email}.example/", normalized_url=f"https://{email}.example/",
                    status_code=200, title="Home"))
        db.commit()
        return user.id, crawl.id


# --------------------------------------------------------------------------- #
# request-shape validation
# --------------------------------------------------------------------------- #
def test_malformed_json_returns_400(isolated_db):
    h = MagicMock()
    h.headers = {"Content-Length": "7"}
    h.rfile = io.BytesIO(b"{not js")
    h.wfile = io.BytesIO()
    crawl_export_api.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert "valid JSON" in body["error"]


def test_unknown_action_returns_400(isolated_db):
    h = _mock_handler({"action": "nope", "crawlId": 1, "format": "csv"})
    crawl_export_api.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert "action" in body["error"]


def test_missing_crawl_id_returns_400(isolated_db):
    h = _mock_handler({"action": "export", "format": "csv"})
    crawl_export_api.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert "crawlId" in body["error"]


def test_invalid_format_returns_400(isolated_db):
    _org, crawl_id = _seed_crawl_with_page(isolated_db)
    h = _mock_handler({"action": "export", "crawlId": crawl_id, "format": "xml"})
    crawl_export_api.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert "format" in body["error"]


def test_empty_crawl_returns_400(isolated_db):
    _org, crawl_id = _seed_crawl_with_page(isolated_db, with_page=False)
    h = _mock_handler({"action": "export", "crawlId": crawl_id, "format": "csv"})
    crawl_export_api.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 400
    assert body["error"] == "This crawl has no pages to export yet."


# --------------------------------------------------------------------------- #
# success paths (one per format)
# --------------------------------------------------------------------------- #
def test_export_json_returns_page_data(isolated_db):
    _org, crawl_id = _seed_crawl_with_page(isolated_db)
    h = _mock_handler({"action": "export", "crawlId": crawl_id, "format": "json"})
    crawl_export_api.handler.do_POST(h)
    status, headers, data = _binary_response(h)
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Content-Disposition"] == f'attachment; filename="crawl-{crawl_id}-report.json"'
    assert headers["Content-Length"] == str(len(data))
    parsed = json.loads(data)
    assert parsed[0]["url"] == "https://example.com/"


def test_export_csv_sets_headers_and_body(isolated_db):
    _org, crawl_id = _seed_crawl_with_page(isolated_db)
    h = _mock_handler({"action": "export", "crawlId": crawl_id, "format": "csv"})
    crawl_export_api.handler.do_POST(h)
    status, headers, data = _binary_response(h)
    assert status == 200
    assert headers["Content-Type"] == "text/csv"
    assert headers["Content-Disposition"] == f'attachment; filename="crawl-{crawl_id}-report.csv"'
    assert len(data) > 0
    assert b"https://example.com/" in data


def test_export_xlsx_sets_mime(isolated_db):
    pytest.importorskip("xlsxwriter")  # optional engine pandas needs for .xlsx
    _org, crawl_id = _seed_crawl_with_page(isolated_db)
    h = _mock_handler({"action": "export", "crawlId": crawl_id, "format": "xlsx"})
    crawl_export_api.handler.do_POST(h)
    status, headers, data = _binary_response(h)
    assert status == 200
    assert headers["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert data[:2] == b"PK"  # xlsx is a zip container


def test_export_pdf_sets_mime(isolated_db):
    _org, crawl_id = _seed_crawl_with_page(isolated_db)
    h = _mock_handler({"action": "export", "crawlId": crawl_id, "format": "pdf"})
    crawl_export_api.handler.do_POST(h)
    status, headers, data = _binary_response(h)
    assert status == 200
    assert headers["Content-Type"] == "application/pdf"
    assert data[:4] == b"%PDF"


# --------------------------------------------------------------------------- #
# org isolation
# --------------------------------------------------------------------------- #
def test_cross_tenant_export_returns_404(isolated_db):
    uid_a, _crawl_a = _seed_user_org_crawl(isolated_db, "alice")
    _uid_b, crawl_b = _seed_user_org_crawl(isolated_db, "bob")
    cookie_a = f"sa_session={create_session_token(uid_a)}"

    h = _mock_handler({"action": "export", "crawlId": crawl_b, "format": "csv"}, cookie=cookie_a)
    crawl_export_api.handler.do_POST(h)
    status, body = _json_response(h)
    assert status == 404
    assert body["error"] == "Crawl not found."


def test_owner_can_export_own_crawl(isolated_db):
    uid_a, crawl_a = _seed_user_org_crawl(isolated_db, "alice")
    cookie_a = f"sa_session={create_session_token(uid_a)}"

    h = _mock_handler({"action": "export", "crawlId": crawl_a, "format": "json"}, cookie=cookie_a)
    crawl_export_api.handler.do_POST(h)
    status, headers, data = _binary_response(h)
    assert status == 200
    assert headers["Content-Type"] == "application/json"
