"""M5 T5.4 - Vercel Cron entry point, resumable server-side crawl executor,
and post-finalize regression-alert emails.

Everything runs against an isolated in-memory SQLite DB (no network, no real
crawler, no real email). `crawl_site`, `finalize_crawl` and `send_email` are
stubbed where a test only cares about the surrounding orchestration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from worker import crawl_diff, crawl_service, cron_runner, email_send, scheduler
from worker.db.models import (
    Base,
    Crawl,
    CrawlConfig,
    Issue,
    Membership,
    Organization,
    Page,
    Project,
    User,
)

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
PAST = NOW - timedelta(hours=1)


@pytest.fixture
def db_factory(monkeypatch):
    """One shared in-memory DB, patched onto every module that opens sessions."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for mod in (scheduler, crawl_service, crawl_diff, cron_runner):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    return factory


# --------------------------------------------------------------------------- #
# seeding helpers
# --------------------------------------------------------------------------- #
def _seed_project(factory, *, org_name="Org", root_url="https://example.com") -> tuple[int, int]:
    with factory() as db:
        org = Organization(name=org_name)
        db.add(org)
        db.flush()
        project = Project(org_id=org.id, name=root_url, root_url=root_url)
        db.add(project)
        db.commit()
        return org.id, project.id


def _seed_config(factory, project_id, *, schedule_cron="0 * * * *", next_run_at=PAST) -> int:
    with factory() as db:
        config = CrawlConfig(
            project_id=project_id,
            source_type="homepage",
            robots_mode="respect",
            max_pages=50,
            user_agent="default",
            schedule_cron=schedule_cron,
            next_run_at=next_run_at,
        )
        db.add(config)
        db.commit()
        return config.id


def _seed_crawl(factory, project_id, config_id, *, status="queued", started_at=None) -> int:
    with factory() as db:
        crawl = Crawl(project_id=project_id, crawl_config_id=config_id,
                      status=status, started_at=started_at)
        db.add(crawl)
        db.commit()
        return crawl.id


# =========================================================================== #
# process_due - eligibility / anti-race claim
# =========================================================================== #
def test_process_due_enqueues_and_runs_due_scheduled_crawl(db_factory, monkeypatch):
    _, project_id = _seed_project(db_factory)
    _seed_config(db_factory, project_id, next_run_at=PAST)

    claimed: list[int] = []

    def fake_chunk(crawl_id, time_budget_seconds=45):
        claimed.append(crawl_id)
        # simulate the crawl completing in one chunk
        with db_factory() as db:
            db.get(Crawl, crawl_id).status = "completed"
            db.commit()
        return {"crawl_id": crawl_id, "status": "completed", "finished": True}

    monkeypatch.setattr(cron_runner, "run_scheduled_crawl_chunk", fake_chunk)

    summary = cron_runner.process_due(now=NOW)

    assert len(summary["enqueued"]) == 1
    assert summary["ran"] == summary["enqueued"]
    assert summary["finished"] == summary["enqueued"]
    assert claimed == summary["enqueued"]

    # The claimed crawl was flipped running -> completed and started_at set.
    with db_factory() as db:
        crawl = db.get(Crawl, summary["enqueued"][0])
        assert crawl.status == "completed"
        assert crawl.started_at is not None


def test_process_due_ignores_unscheduled_queued_crawl(db_factory, monkeypatch):
    """A queued crawl whose CrawlConfig has no schedule_cron must never be
    claimed by the cron executor."""
    _, project_id = _seed_project(db_factory)
    config_id = _seed_config(db_factory, project_id, schedule_cron=None, next_run_at=None)
    manual_crawl = _seed_crawl(db_factory, project_id, config_id, status="queued")

    monkeypatch.setattr(cron_runner, "run_scheduled_crawl_chunk",
                        lambda *a, **k: pytest.fail("should not claim an unscheduled crawl"))

    summary = cron_runner.process_due(now=NOW)

    assert summary["ran"] == []
    with db_factory() as db:
        assert db.get(Crawl, manual_crawl).status == "queued"  # untouched


def test_process_due_does_not_hijack_a_started_crawl(db_factory, monkeypatch):
    """A scheduled crawl a browser already started (running + started_at set) is
    off-limits - the anti-hijack rule only claims queued + not-started."""
    _, project_id = _seed_project(db_factory)
    config_id = _seed_config(db_factory, project_id, schedule_cron="0 * * * *", next_run_at=None)
    browser_crawl = _seed_crawl(db_factory, project_id, config_id,
                                status="running", started_at=NOW - timedelta(minutes=5))

    ran: list[int] = []
    monkeypatch.setattr(cron_runner, "run_scheduled_crawl_chunk",
                        lambda cid, **k: ran.append(cid) or {"finished": False})

    cron_runner.process_due(now=NOW)

    assert browser_crawl not in ran
    with db_factory() as db:
        assert db.get(Crawl, browser_crawl).status == "running"  # not hijacked


def test_process_due_resumes_paused_scheduled_crawl(db_factory, monkeypatch):
    """A scheduled crawl a prior chunk left `paused` is resumed on a later tick."""
    _, project_id = _seed_project(db_factory)
    # Not due (future next_run_at), so enqueue creates nothing new - the only
    # eligible crawl is the paused one to resume.
    config_id = _seed_config(db_factory, project_id, schedule_cron="0 * * * *",
                             next_run_at=NOW + timedelta(hours=1))
    paused = _seed_crawl(db_factory, project_id, config_id, status="paused",
                         started_at=NOW - timedelta(minutes=1))

    ran: list[int] = []
    monkeypatch.setattr(cron_runner, "run_scheduled_crawl_chunk",
                        lambda cid, **k: ran.append(cid) or {"finished": True})

    summary = cron_runner.process_due(now=NOW)

    assert ran == [paused]
    assert summary["finished"] == [paused]


def test_process_due_respects_max_crawls(db_factory, monkeypatch):
    _, project_id = _seed_project(db_factory)
    # three separate scheduled configs -> three due crawls
    for _ in range(3):
        _seed_config(db_factory, project_id, next_run_at=PAST)

    ran: list[int] = []
    monkeypatch.setattr(cron_runner, "run_scheduled_crawl_chunk",
                        lambda cid, **k: ran.append(cid) or {"finished": True})

    cron_runner.process_due(now=NOW, max_crawls=2)

    assert len(ran) == 2  # capped


# =========================================================================== #
# run_scheduled_crawl_chunk - completion vs. resume, resumable execution
# =========================================================================== #
def _fake_crawl_site_factory(pages, stats):
    """Return a crawl_site stand-in that persists `pages` (list of urls, via a
    robots-skip outcome so persist_result writes minimal Page rows) and returns
    the given stats block."""
    def fake_crawl_site(config, on_result=None, resume_visited=None,
                        resume_frontier=None, should_stop=None):
        if on_result:
            for url in pages:
                on_result(url, {"skipped": "robots", "url": url})
        return {"pages": [], "stats": dict(stats)}
    return fake_crawl_site


def test_chunk_completes_when_frontier_exhausted(db_factory, monkeypatch):
    _, project_id = _seed_project(db_factory)
    config_id = _seed_config(db_factory, project_id)
    crawl_id = _seed_crawl(db_factory, project_id, config_id, status="running", started_at=NOW)

    monkeypatch.setattr(cron_runner, "crawl_site",
                        _fake_crawl_site_factory(["https://example.com/a"], {"timed_out": False}))
    finalized: list[tuple[int, str]] = []
    monkeypatch.setattr(cron_runner, "finalize_crawl",
                        lambda cid, status: finalized.append((cid, status)))

    result = cron_runner.run_scheduled_crawl_chunk(crawl_id, time_budget_seconds=5)

    assert result["finished"] is True
    assert result["status"] == "completed"
    assert finalized == [(crawl_id, "completed")]


def test_chunk_pauses_when_budget_runs_out(db_factory, monkeypatch):
    _, project_id = _seed_project(db_factory)
    config_id = _seed_config(db_factory, project_id)
    crawl_id = _seed_crawl(db_factory, project_id, config_id, status="running", started_at=NOW)

    monkeypatch.setattr(cron_runner, "crawl_site",
                        _fake_crawl_site_factory(["https://example.com/a"], {"timed_out": True}))
    monkeypatch.setattr(cron_runner, "finalize_crawl",
                        lambda *a, **k: pytest.fail("should not finalize a timed-out chunk"))

    result = cron_runner.run_scheduled_crawl_chunk(crawl_id, time_budget_seconds=5)

    assert result["finished"] is False
    assert result["status"] == "paused"
    with db_factory() as db:
        assert db.get(Crawl, crawl_id).status == "paused"  # resumable


def test_chunk_hard_error_with_no_pages_fails(db_factory, monkeypatch):
    _, project_id = _seed_project(db_factory)
    config_id = _seed_config(db_factory, project_id)
    crawl_id = _seed_crawl(db_factory, project_id, config_id, status="running", started_at=NOW)

    def boom(*a, **k):
        raise RuntimeError("crawler exploded")

    monkeypatch.setattr(cron_runner, "crawl_site", boom)
    finalized: list[tuple[int, str]] = []
    monkeypatch.setattr(cron_runner, "finalize_crawl",
                        lambda cid, status: finalized.append((cid, status)))

    result = cron_runner.run_scheduled_crawl_chunk(crawl_id, time_budget_seconds=5)

    assert result["status"] == "failed"
    assert finalized == [(crawl_id, "failed")]


# =========================================================================== #
# send_regression_alert
# =========================================================================== #
def _seed_two_crawls_with_regression(factory):
    """Old crawl: page X with issue A, health 90. New crawl: page X with issues
    A + B, health 80 -> new issue B + a health drop."""
    org_id, project_id = _seed_project(factory)
    with factory() as db:
        # org owner (admin)
        user = User(email="owner@example.com", password_hash="x")
        db.add(user)
        db.flush()
        db.add(Membership(user_id=user.id, org_id=org_id, role="admin"))

        old = Crawl(project_id=project_id, status="completed", health_score=90.0,
                    seo_score_avg=88.0, finished_at=NOW - timedelta(days=1))
        new = Crawl(project_id=project_id, status="completed", health_score=80.0,
                    seo_score_avg=70.0, finished_at=NOW)
        db.add_all([old, new])
        db.flush()

        old_page = Page(crawl_id=old.id, url="https://example.com/x",
                        normalized_url="https://example.com/x", seo_score=88.0)
        new_page = Page(crawl_id=new.id, url="https://example.com/x",
                        normalized_url="https://example.com/x", seo_score=70.0)
        db.add_all([old_page, new_page])
        db.flush()

        db.add(Issue(crawl_id=old.id, page_id=old_page.id, issue_type="A", severity="warning"))
        db.add(Issue(crawl_id=new.id, page_id=new_page.id, issue_type="A", severity="warning"))
        db.add(Issue(crawl_id=new.id, page_id=new_page.id, issue_type="B", severity="error"))
        db.commit()
        return old.id, new.id


def test_regression_alert_sends_to_org_admin(db_factory, monkeypatch):
    _, new_id = _seed_two_crawls_with_regression(db_factory)

    sent: list[tuple] = []
    monkeypatch.setattr(email_send, "email_enabled", lambda: True)
    monkeypatch.setattr(email_send, "send_email",
                        lambda to, subject, html: sent.append((to, subject, html)) or {"ok": True})

    crawl_service.send_regression_alert(new_id)

    assert len(sent) == 1
    to, subject, html = sent[0]
    assert to == "owner@example.com"
    assert "example.com" in subject
    assert "regression" in html.lower()


def test_regression_alert_noop_when_email_disabled(db_factory, monkeypatch):
    _, new_id = _seed_two_crawls_with_regression(db_factory)
    monkeypatch.setattr(email_send, "email_enabled", lambda: False)
    monkeypatch.setattr(email_send, "send_email",
                        lambda *a, **k: pytest.fail("must not send when email disabled"))
    crawl_service.send_regression_alert(new_id)  # no raise, no send


def test_regression_alert_noop_without_previous_crawl(db_factory, monkeypatch):
    _, project_id = _seed_project(db_factory)
    with db_factory() as db:
        crawl = Crawl(project_id=project_id, status="completed", health_score=80.0,
                      finished_at=NOW)
        db.add(crawl)
        db.commit()
        crawl_id = crawl.id

    monkeypatch.setattr(email_send, "email_enabled", lambda: True)
    monkeypatch.setattr(email_send, "send_email",
                        lambda *a, **k: pytest.fail("no previous crawl -> nothing to compare"))
    crawl_service.send_regression_alert(crawl_id)


def test_regression_alert_noop_when_nothing_regressed(db_factory, monkeypatch):
    """No new issues and health improved -> no email."""
    _, project_id = _seed_project(db_factory)
    with db_factory() as db:
        old = Crawl(project_id=project_id, status="completed", health_score=80.0,
                    finished_at=NOW - timedelta(days=1))
        new = Crawl(project_id=project_id, status="completed", health_score=90.0,
                    finished_at=NOW)
        db.add_all([old, new])
        db.commit()
        new_id = new.id

    monkeypatch.setattr(email_send, "email_enabled", lambda: True)
    monkeypatch.setattr(email_send, "send_email",
                        lambda *a, **k: pytest.fail("nothing regressed -> no email"))
    crawl_service.send_regression_alert(new_id)


def test_finalize_crawl_completed_invokes_alert_hook(db_factory, monkeypatch):
    """finalize_crawl(status='completed') fires the alert hook; a failed crawl
    does not."""
    _, project_id = _seed_project(db_factory)
    with db_factory() as db:
        crawl = Crawl(project_id=project_id, status="running")
        db.add(crawl)
        db.commit()
        crawl_id = crawl.id

    calls: list[int] = []
    monkeypatch.setattr(crawl_service, "send_regression_alert", lambda cid: calls.append(cid))
    # skip the sitewide aggregation pass - not what this test exercises
    monkeypatch.setattr(crawl_service, "run_site_audit", lambda cid: None)

    crawl_service.finalize_crawl(crawl_id, "completed")
    assert calls == [crawl_id]

    calls.clear()
    crawl_service.finalize_crawl(crawl_id, "failed")
    assert calls == []  # not fired for failed crawls


def test_alert_hook_swallows_errors(db_factory, monkeypatch):
    """A crashing alert must never break finalization."""
    _, project_id = _seed_project(db_factory)
    with db_factory() as db:
        crawl = Crawl(project_id=project_id, status="running")
        db.add(crawl)
        db.commit()
        crawl_id = crawl.id

    def boom(cid):
        raise RuntimeError("alert exploded")

    monkeypatch.setattr(crawl_service, "send_regression_alert", boom)
    monkeypatch.setattr(crawl_service, "run_site_audit", lambda cid: None)

    crawl_service.finalize_crawl(crawl_id, "completed")  # must not raise
    with db_factory() as db:
        assert db.get(Crawl, crawl_id).status == "completed"  # still finalized


# =========================================================================== #
# regression_alert_html rendering
# =========================================================================== #
def test_regression_alert_html_contains_key_facts():
    diff = {
        "new_issues": [{"url": "https://example.com/x", "issue_type": "B"}],
        "fixed_issues": [],
        "regressed_pages": [
            {"url": "https://example.com/x", "old_score": 88.0, "new_score": 70.0, "delta": -18.0}
        ],
        "health_score_delta": -10.0,
        "seo_score_avg_delta": -18.0,
    }
    html = email_send.regression_alert_html("https://example.com", diff,
                                            report_url="https://app/report/1")
    assert "https://example.com" in html
    assert "-10.0" in html  # health delta, signed
    assert "https://example.com/x" in html  # regressed page listed
    assert "View full report" in html  # button rendered when report_url given


# =========================================================================== #
# api/cron.py handler
# =========================================================================== #
def _load_cron_module():
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "cron.py")
    spec = importlib.util.spec_from_file_location("api_cron", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeHandler:
    """Minimal stand-in for a BaseHTTPRequestHandler instance: only .headers is
    read by do_GET, and send_json is patched to capture instead of writing."""
    def __init__(self, headers=None):
        self.headers = headers or {}


def _run_cron(cron_mod, monkeypatch, *, headers=None):
    captured = {}
    monkeypatch.setattr(cron_mod, "send_json",
                        lambda handler, status, data: captured.update(status=status, data=data))
    fake = _FakeHandler(headers)
    # bind do_GET to our fake instance
    cron_mod.handler.do_GET(fake)
    return captured


def test_cron_fires_schedules_execution_disabled_by_default(monkeypatch):
    cron_mod = _load_cron_module()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("CRON_EXECUTE_CRAWLS", raising=False)
    monkeypatch.setattr(cron_mod, "enqueue_due_crawls", lambda: [11, 12])
    monkeypatch.setattr(cron_mod.cron_runner, "process_due",
                        lambda *a, **k: pytest.fail("execution must be OFF by default"))

    result = _run_cron(cron_mod, monkeypatch)

    assert result["status"] == 200
    assert result["data"]["enqueued"] == [11, 12]
    assert result["data"]["execution_enabled"] is False
    assert "disabled" in result["data"]["note"].lower()


def test_cron_executes_when_flag_enabled(monkeypatch):
    cron_mod = _load_cron_module()
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.setenv("CRON_EXECUTE_CRAWLS", "1")
    monkeypatch.setattr(cron_mod, "enqueue_due_crawls", lambda: [5])
    monkeypatch.setattr(cron_mod.cron_runner, "process_due",
                        lambda *a, **k: {"enqueued": [], "ran": [5], "finished": [5]})

    result = _run_cron(cron_mod, monkeypatch)

    assert result["status"] == 200
    assert result["data"]["execution_enabled"] is True
    assert result["data"]["ran"] == [5]
    assert result["data"]["finished"] == [5]


def test_cron_requires_secret_when_set(monkeypatch):
    cron_mod = _load_cron_module()
    monkeypatch.setenv("CRON_SECRET", "topsecret")
    monkeypatch.setattr(cron_mod, "enqueue_due_crawls",
                        lambda: pytest.fail("must not run without a valid secret"))

    # missing / wrong header -> 401
    result = _run_cron(cron_mod, monkeypatch, headers={})
    assert result["status"] == 401

    result = _run_cron(cron_mod, monkeypatch, headers={"Authorization": "Bearer wrong"})
    assert result["status"] == 401


def test_cron_accepts_valid_secret(monkeypatch):
    cron_mod = _load_cron_module()
    monkeypatch.setenv("CRON_SECRET", "topsecret")
    monkeypatch.delenv("CRON_EXECUTE_CRAWLS", raising=False)
    monkeypatch.setattr(cron_mod, "enqueue_due_crawls", lambda: [])

    result = _run_cron(cron_mod, monkeypatch, headers={"Authorization": "Bearer topsecret"})
    assert result["status"] == 200
