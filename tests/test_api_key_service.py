"""Tests for worker/api_key_service.py - set/get/list/delete against an
in-memory SQLite DB (matching tests/test_worker_crawl.py's pattern), with
particular attention to the one hard rule: list_api_keys() must never
surface a decrypted value anywhere in its output."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from worker import api_key_service as svc
from worker.db.models import Base


def _isolated_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture
def isolated_db():
    return _isolated_session_factory()


def test_set_get_round_trip(isolated_db):
    with isolated_db() as db:
        org = svc.get_or_create_default_org(db)
        svc.set_api_key(db, org.id, "psi", "AIzaSecretValue1234")

        assert svc.get_api_key(db, org.id, "psi") == "AIzaSecretValue1234"


def test_set_upserts_on_second_save(isolated_db):
    with isolated_db() as db:
        org = svc.get_or_create_default_org(db)
        svc.set_api_key(db, org.id, "groq", "first-value")
        svc.set_api_key(db, org.id, "groq", "second-value")

        assert svc.get_api_key(db, org.id, "groq") == "second-value"
        assert len(svc.list_api_keys(db, org.id)) == 1  # one row, not two


def test_list_never_exposes_decrypted_value(isolated_db):
    with isolated_db() as db:
        org = svc.get_or_create_default_org(db)
        secret = "super-secret-do-not-leak-12345"
        svc.set_api_key(db, org.id, "openai", secret)

        listing = svc.list_api_keys(db, org.id)

        assert len(listing) == 1
        entry = listing[0]
        assert entry["provider"] == "openai"
        assert secret not in repr(entry)  # nowhere in the returned structure
        assert entry["maskedPreview"] != secret
        assert entry["maskedPreview"].endswith(secret[-4:])
        assert "encrypted_value" not in entry


def test_delete_removes_the_key(isolated_db):
    with isolated_db() as db:
        org = svc.get_or_create_default_org(db)
        svc.set_api_key(db, org.id, "gemini", "value")

        assert svc.delete_api_key(db, org.id, "gemini") is True
        assert svc.get_api_key(db, org.id, "gemini") is None
        assert svc.delete_api_key(db, org.id, "gemini") is False  # already gone


def test_rejects_unknown_provider(isolated_db):
    with isolated_db() as db:
        org = svc.get_or_create_default_org(db)
        with pytest.raises(ValueError, match="Unknown provider"):
            svc.set_api_key(db, org.id, "not-a-real-provider", "value")


def test_get_default_org_vaulted_key_returns_saved_value(monkeypatch, isolated_db):
    monkeypatch.setattr(svc, "SessionLocal", isolated_db)
    with isolated_db() as db:
        org = svc.get_or_create_default_org(db)
        svc.set_api_key(db, org.id, "psi", "vault-psi-key")

    assert svc.get_default_org_vaulted_key("psi") == "vault-psi-key"


def test_get_default_org_vaulted_key_fails_closed_on_db_error(monkeypatch):
    def _boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(svc, "SessionLocal", _boom)

    # Must not raise - callers (api/audit-pipeline.py, api/ai.py) ran with
    # zero DB dependency before the vault existed and must keep working.
    assert svc.get_default_org_vaulted_key("psi") is None
