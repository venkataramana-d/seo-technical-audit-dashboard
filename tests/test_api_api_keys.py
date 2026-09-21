"""Tests for api/api-keys.py - following tests/test_api_crawls.py's
importlib + mock-handler pattern, combined with the in-memory-SQLite
monkeypatch pattern used across tests/test_worker_*.py. fetch_pagespeed/
_chat (the two "Test Connection" testers) are mocked so no real network
call happens."""

import importlib.util
import io
import json
import os
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from worker.db.models import Base


def _load(name, relative_path):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_keys = _load("api_keys_under_test", "api/api-keys.py")


def _mock_handler(body: dict | None = None):
    encoded = json.dumps(body or {}).encode()
    h = MagicMock()
    h.headers = {"Content-Length": str(len(encoded))}
    h.rfile = io.BytesIO(encoded)
    h.wfile = io.BytesIO()
    return h


def _sent_status_and_body(h):
    status = h.send_response.call_args[0][0]
    return status, json.loads(h.wfile.getvalue())


def _isolated_session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def isolated_db(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    session_factory = _isolated_session_factory()
    monkeypatch.setattr(api_keys, "SessionLocal", session_factory)
    return session_factory


def test_all_three_actions_registered():
    assert set(api_keys._ACTIONS) == {"set", "delete", "test"}


def test_unknown_action_returns_400(isolated_db):
    h = _mock_handler({"action": "not-a-real-action"})
    api_keys.handler.do_POST(h)
    status, body = _sent_status_and_body(h)
    assert status == 400
    assert "Unknown or missing action" in body["error"]


def test_get_lists_no_keys_when_none_saved(isolated_db):
    h = _mock_handler()
    api_keys.handler.do_GET(h)
    status, body = _sent_status_and_body(h)
    assert status == 200
    assert body["apiKeys"] == []


def test_set_then_get_shows_masked_entry(isolated_db):
    h = _mock_handler({"action": "set", "provider": "psi", "value": "my-real-key-9999"})
    api_keys.handler.do_POST(h)
    status, body = _sent_status_and_body(h)
    assert status == 200
    assert body["ok"] is True

    h2 = _mock_handler()
    api_keys.handler.do_GET(h2)
    _, body2 = _sent_status_and_body(h2)
    assert len(body2["apiKeys"]) == 1
    entry = body2["apiKeys"][0]
    assert entry["provider"] == "psi"
    assert "my-real-key-9999" not in json.dumps(entry)
    assert entry["maskedPreview"].endswith("9999")


def test_set_rejects_unknown_provider(isolated_db):
    h = _mock_handler({"action": "set", "provider": "bogus", "value": "x"})
    api_keys.handler.do_POST(h)
    status, body = _sent_status_and_body(h)
    assert status == 400
    assert "Unknown provider" in body["error"]


def test_delete_removes_a_saved_key(isolated_db):
    h = _mock_handler({"action": "set", "provider": "groq", "value": "gsk_test"})
    api_keys.handler.do_POST(h)

    h2 = _mock_handler({"action": "delete", "provider": "groq"})
    api_keys.handler.do_POST(h2)
    status, body = _sent_status_and_body(h2)
    assert status == 200
    assert body["deleted"] is True

    h3 = _mock_handler()
    api_keys.handler.do_GET(h3)
    _, body3 = _sent_status_and_body(h3)
    assert body3["apiKeys"] == []


def test_test_connection_reports_no_key_saved(isolated_db):
    h = _mock_handler({"action": "test", "provider": "psi"})
    api_keys.handler.do_POST(h)
    status, body = _sent_status_and_body(h)
    assert status == 200
    assert body["ok"] is False
    assert "No psi key saved" in body["error"]


def test_test_connection_unsupported_provider_is_explicit(isolated_db):
    h = _mock_handler({"action": "set", "provider": "openai", "value": "sk-test"})
    api_keys.handler.do_POST(h)

    h2 = _mock_handler({"action": "test", "provider": "openai"})
    api_keys.handler.do_POST(h2)
    status, body = _sent_status_and_body(h2)
    assert status == 200
    assert body["ok"] is False
    assert "isn't available for 'openai' yet" in body["error"]


def test_test_connection_psi_success(monkeypatch, isolated_db):
    h = _mock_handler({"action": "set", "provider": "psi", "value": "real-psi-key"})
    api_keys.handler.do_POST(h)

    monkeypatch.setattr(api_keys, "fetch_pagespeed", lambda url, api_key=None: {"success": True})
    h2 = _mock_handler({"action": "test", "provider": "psi"})
    api_keys.handler.do_POST(h2)
    status, body = _sent_status_and_body(h2)
    assert status == 200
    assert body["ok"] is True


def test_test_connection_groq_failure_surfaces_error(monkeypatch, isolated_db):
    h = _mock_handler({"action": "set", "provider": "groq", "value": "bad-key"})
    api_keys.handler.do_POST(h)

    def _boom(messages, api_key, **kwargs):
        raise RuntimeError("Groq API unavailable (HTTP 401)")

    monkeypatch.setattr(api_keys, "_chat", _boom)
    h2 = _mock_handler({"action": "test", "provider": "groq"})
    api_keys.handler.do_POST(h2)
    status, body = _sent_status_and_body(h2)
    assert status == 200
    assert body["ok"] is False
    assert "401" in body["error"]
