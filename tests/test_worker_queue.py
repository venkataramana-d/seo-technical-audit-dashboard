"""Tests for worker/queue.py - the DB-backed queue standing in for
Redis+Celery/arq locally. Verifies the enqueue -> claim -> process ->
complete/fail cycle in isolation, against an in-memory SQLite DB (not the
dev.db file used by the actual worker process)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from worker import queue as queue_module
from worker.db.models import Base, Job


def _isolated_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_enqueue_creates_queued_job(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(queue_module, "SessionLocal", session_factory)

    job_id = queue_module.enqueue("test.echo", {"x": 1})

    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job.status == "queued"
        assert job.job_type == "test.echo"
        assert job.payload_json == {"x": 1}


def test_run_once_processes_registered_handler(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(queue_module, "SessionLocal", session_factory)

    @queue_module.register("test.echo")
    def _echo(payload):
        return {"echoed": payload}

    job_id = queue_module.enqueue("test.echo", {"x": 1})
    worker = queue_module.Worker()

    processed = worker.run_once()

    assert processed is True
    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job.status == "completed"
        assert job.result_json == {"echoed": {"x": 1}}
        assert job.started_at is not None
        assert job.finished_at is not None


def test_run_once_returns_false_when_queue_empty(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(queue_module, "SessionLocal", session_factory)

    assert queue_module.Worker().run_once() is False


def test_run_once_marks_job_failed_on_handler_exception(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(queue_module, "SessionLocal", session_factory)

    @queue_module.register("test.boom")
    def _boom(payload):
        raise ValueError("deliberate failure")

    job_id = queue_module.enqueue("test.boom", {})
    queue_module.Worker().run_once()

    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert "deliberate failure" in job.error


def test_run_once_marks_job_failed_when_no_handler_registered(monkeypatch):
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(queue_module, "SessionLocal", session_factory)

    job_id = queue_module.enqueue("test.unregistered_type_xyz", {})
    queue_module.Worker().run_once()

    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job.status == "failed"
        assert "no handler registered" in job.error
