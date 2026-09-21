"""Tests for the api/audit-pipeline.py and api/ai.py action-dispatch tables.

These two files consolidate what used to be 9 separate api/*.py Vercel
functions into 2, to cut Vercel's per-function Python dependency install
time at build (see the module docstring-equivalent comment in each file).
The dispatch logic (_ACTIONS dict + do_POST routing) is new, real logic
introduced by that consolidation, so it gets direct coverage here - unlike
the thin per-action handler bodies themselves, which just call straight
into modules/*.py functions already covered by their own test files
(test_ai_assist.py, test_ssrf.py, etc.) and follow this repo's established
convention of not testing the handler classes.

Filenames contain a hyphen (audit-pipeline.py), so they can't be imported
via a normal `from api.audit_pipeline import ...` statement; loaded via
importlib instead.
"""

import importlib.util
import io
import json
import os
from unittest.mock import MagicMock

import pytest


def _load(name, relative_path):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_pipeline = _load("audit_pipeline_under_test", "api/audit-pipeline.py")
ai = _load("ai_under_test", "api/ai.py")


def _mock_handler(body: dict):
    encoded = json.dumps(body).encode()
    h = MagicMock()
    h.headers = {"Content-Length": str(len(encoded))}
    h.rfile = io.BytesIO(encoded)
    h.wfile = io.BytesIO()
    return h


def _sent_status_and_body(h):
    status = h.send_response.call_args[0][0]
    return status, json.loads(h.wfile.getvalue())


class TestAuditPipelineDispatch:
    def test_all_five_actions_registered(self):
        assert set(audit_pipeline._ACTIONS) == {"audit", "sitemap", "crawl", "site-health", "pagespeed"}

    def test_unknown_action_returns_400(self):
        h = _mock_handler({"action": "not-a-real-action"})
        audit_pipeline.handler.do_POST(h)
        status, body = _sent_status_and_body(h)
        assert status == 400
        assert "Unknown or missing action" in body["error"]

    def test_missing_action_returns_400(self):
        h = _mock_handler({"url": "https://example.com/"})
        audit_pipeline.handler.do_POST(h)
        status, _ = _sent_status_and_body(h)
        assert status == 400

    def test_audit_action_routes_to_audit_handler(self, monkeypatch):
        called = {}

        def fake_handle_audit(handler, payload):
            called["hit"] = True
            audit_pipeline.send_json(handler, 200, {"ok": True})

        monkeypatch.setitem(audit_pipeline._ACTIONS, "audit", fake_handle_audit)
        h = _mock_handler({"action": "audit", "url": "https://example.com/"})
        audit_pipeline.handler.do_POST(h)
        assert called.get("hit") is True

    def test_malformed_body_returns_400_not_crash(self):
        # A malformed JSON body is a client error (400), not a server error -
        # see audit finding #4. (Previously mapped to a generic 500.)
        h = MagicMock()
        h.headers = {"Content-Length": "9"}
        h.rfile = io.BytesIO(b"not json!")
        h.wfile = io.BytesIO()
        audit_pipeline.handler.do_POST(h)
        status, body = _sent_status_and_body(h)
        assert status == 400
        assert "error" in body


class TestAiDispatch:
    def test_ai_actions_registered(self):
        # "chat" was removed in Session 24 (chatbot dropped). "content-draft"
        # was added when the rebuild's Anthropic-backed Content Agent was folded
        # in (draft-only title/meta suggestions).
        assert set(ai._ACTIONS) == {"summary", "fix-suggestion", "content-draft"}

    def test_unknown_action_returns_400(self):
        h = _mock_handler({"action": "nope"})
        ai.handler.do_POST(h)
        status, body = _sent_status_and_body(h)
        assert status == 400
        assert body["ok"] is False

    def test_chat_action_routes_to_chat_handler(self, monkeypatch):
        called = {}

        def fake_handle_chat(handler, payload):
            called["hit"] = True
            ai.send_json(handler, 200, {"ok": True, "reply": "hi"})

        monkeypatch.setitem(ai._ACTIONS, "chat", fake_handle_chat)
        h = _mock_handler({"action": "chat", "messages": [{"role": "user", "content": "hi"}]})
        ai.handler.do_POST(h)
        assert called.get("hit") is True

    def test_get_returns_config_status_shape(self, monkeypatch):
        monkeypatch.delenv("PSI_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        h = MagicMock()
        h.wfile = io.BytesIO()
        ai.handler.do_GET(h)
        _, body = _sent_status_and_body(h)
        assert body == {"psiConfigured": False, "groqConfigured": False}

    def test_get_reflects_configured_keys(self, monkeypatch):
        monkeypatch.setenv("PSI_API_KEY", "x")
        monkeypatch.setenv("GROQ_API_KEY", "y")
        h = MagicMock()
        h.wfile = io.BytesIO()
        ai.handler.do_GET(h)
        _, body = _sent_status_and_body(h)
        assert body == {"psiConfigured": True, "groqConfigured": True}
