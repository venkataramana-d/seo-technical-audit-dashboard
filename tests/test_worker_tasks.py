"""Tests for worker/tasks.py - proves the job handlers are transparent
wrappers around the existing modules/*.py functions, not a reimplementation.
`audit_url` itself is monkeypatched so this test doesn't depend on network
access; the point being verified is that the handler passes payload through
and returns the result unchanged, byte-for-byte."""

from worker import tasks


def test_handle_audit_page_passes_payload_through_unchanged(monkeypatch):
    captured = {}
    fake_result = {"seo_score": 87.5, "all_issues": [], "url": "https://example.com"}

    def fake_audit_url(**kwargs):
        captured.update(kwargs)
        return fake_result

    monkeypatch.setattr(tasks, "audit_url", fake_audit_url)

    payload = {
        "url": "https://example.com",
        "audit_type": "auto",
        "check_links": True,
        "validate_links": False,
        "fetch_pagespeed": False,
    }
    result = tasks.handle_audit_page(payload)

    assert captured == payload
    assert result is fake_result
