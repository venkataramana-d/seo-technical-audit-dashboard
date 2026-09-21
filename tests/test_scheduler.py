"""Tests for worker/scheduler.py's enqueue_due_crawls() - the Phase 3
"Always-on" tick. No real time.sleep()/cron daemon involved: this directly
calls the tick function with hand-set next_run_at values."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from worker import scheduler
from worker.db.models import Base, Crawl, CrawlConfig, Job, Organization, Project


def _isolated_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def isolated_db(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(scheduler, "SessionLocal", session_factory)
    return session_factory


def _seed_project_and_config(session_factory, *, schedule_cron, next_run_at) -> int:
    with session_factory() as db:
        org = Organization(name="Test Org")
        db.add(org)
        db.flush()
        project = Project(org_id=org.id, name="example.com", root_url="https://example.com")
        db.add(project)
        db.flush()
        config = CrawlConfig(
            project_id=project.id,
            source_type="homepage",
            robots_mode="respect",
            max_pages=50,
            schedule_cron=schedule_cron,
            next_run_at=next_run_at,
        )
        db.add(config)
        db.commit()
        return config.id


def test_due_config_gets_a_new_crawl_and_job_enqueued(isolated_db):
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=1)
    config_id = _seed_project_and_config(isolated_db, schedule_cron="0 * * * *", next_run_at=past)

    new_ids = scheduler.enqueue_due_crawls(now=now)

    assert len(new_ids) == 1
    with isolated_db() as db:
        crawl = db.get(Crawl, new_ids[0])
        assert crawl.status == "queued"
        assert crawl.crawl_config_id == config_id

        jobs = db.execute(select(Job).where(Job.job_type == "crawl.start")).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].payload_json == {"crawl_id": new_ids[0]}

        config = db.get(CrawlConfig, config_id)
        # SQLite's DateTime storage drops tzinfo on round-trip (a pre-existing
        # characteristic of this schema, not scheduler-specific) - compare as
        # naive UTC.
        assert config.next_run_at > now.replace(tzinfo=None)  # advanced into the future


def test_config_with_next_run_at_none_is_triggered_immediately(isolated_db):
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    _seed_project_and_config(isolated_db, schedule_cron="0 * * * *", next_run_at=None)

    new_ids = scheduler.enqueue_due_crawls(now=now)

    assert len(new_ids) == 1


def test_future_schedule_is_not_triggered(isolated_db):
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    future = now + timedelta(hours=1)
    _seed_project_and_config(isolated_db, schedule_cron="0 * * * *", next_run_at=future)

    new_ids = scheduler.enqueue_due_crawls(now=now)

    assert new_ids == []


def test_unscheduled_config_is_never_triggered(isolated_db):
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    past = now - timedelta(hours=1)
    _seed_project_and_config(isolated_db, schedule_cron=None, next_run_at=past)

    new_ids = scheduler.enqueue_due_crawls(now=now)

    assert new_ids == []


def test_create_crawl_with_schedule_cron_sets_next_run_at(isolated_db):
    from worker import crawl_service

    with isolated_db() as db:
        crawl = crawl_service.create_crawl(db, "https://example.com", max_pages=5, schedule_cron="0 0 * * *")
        config = db.get(CrawlConfig, crawl.crawl_config_id)
        assert config.schedule_cron == "0 0 * * *"
        assert config.next_run_at is not None
        assert config.next_run_at > datetime.now(timezone.utc).replace(tzinfo=None)


def test_set_crawl_config_schedule_updates_existing_row(isolated_db):
    from worker import crawl_service

    with isolated_db() as db:
        crawl = crawl_service.create_crawl(db, "https://example.com", max_pages=5)
        config_id = crawl.crawl_config_id
        assert db.get(CrawlConfig, config_id).schedule_cron is None

        updated = crawl_service.set_crawl_config_schedule(db, config_id, "0 0 * * *")

        assert updated.id == config_id  # same row, not a new one
        assert updated.schedule_cron == "0 0 * * *"
        assert updated.next_run_at is not None
        assert updated.next_run_at > datetime.now(timezone.utc).replace(tzinfo=None)


def test_set_crawl_config_schedule_with_none_clears_it(isolated_db):
    from worker import crawl_service

    with isolated_db() as db:
        crawl = crawl_service.create_crawl(db, "https://example.com", max_pages=5, schedule_cron="0 0 * * *")
        config_id = crawl.crawl_config_id
        assert db.get(CrawlConfig, config_id).schedule_cron is not None

        updated = crawl_service.set_crawl_config_schedule(db, config_id, None)

        assert updated.schedule_cron is None
        assert updated.next_run_at is None


def test_set_crawl_config_schedule_rejects_unknown_config_id(isolated_db):
    from worker import crawl_service

    with isolated_db() as db:
        with pytest.raises(ValueError, match="no such crawl_config_id"):
            crawl_service.set_crawl_config_schedule(db, 9999, "0 0 * * *")


def test_set_crawl_config_schedule_rejects_invalid_cron(isolated_db):
    from worker import crawl_service

    with isolated_db() as db:
        crawl = crawl_service.create_crawl(db, "https://example.com", max_pages=5)
        with pytest.raises(ValueError):
            crawl_service.set_crawl_config_schedule(db, crawl.crawl_config_id, "not a cron expression")
