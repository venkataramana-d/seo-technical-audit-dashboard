"""Transactional email via Resend (https://resend.com) — used for self-serve
password-reset links. Dependency-free (just `requests`, already required); the
whole feature is optional and degrades gracefully:

- If RESEND_API_KEY is not set, `email_enabled()` is False and callers fall
  back to the admin-resolved reset flow (no email sent).
- RESEND_FROM sets the verified sender, e.g. "SEO Audit <noreply@yourdomain>".
  Resend's shared sandbox sender "onboarding@resend.dev" works without a
  verified domain for initial testing.
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
_DEFAULT_FROM = "SEO Audit <onboarding@resend.dev>"


def email_enabled() -> bool:
    """True when a Resend API key is configured (email features are usable)."""
    return bool((os.environ.get("RESEND_API_KEY") or "").strip())


def _from_address() -> str:
    return (os.environ.get("RESEND_FROM") or _DEFAULT_FROM).strip()


def send_email(to: str, subject: str, html: str) -> dict:
    """Send one email. Returns {"ok": True, "id": ...} or {"ok": False,
    "error": ...}. Never raises — callers treat email as best-effort."""
    api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not api_key:
        return {"ok": False, "error": "email is not configured (RESEND_API_KEY not set)"}
    try:
        resp = requests.post(
            _RESEND_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"from": _from_address(), "to": [to], "subject": subject, "html": html},
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.error("Resend send failed: %s %s", resp.status_code, resp.text[:500])
            return {"ok": False, "error": f"email provider returned {resp.status_code}"}
        data = resp.json() if resp.content else {}
        return {"ok": True, "id": data.get("id")}
    except Exception as e:  # noqa: BLE001 - email is best-effort, never fatal
        logger.exception("Resend send raised")
        return {"ok": False, "error": str(e)}


def password_reset_html(reset_url: str) -> str:
    """The reset-link email body. Plain, inline-styled, client-safe HTML."""
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;color:#1a1a2e">
  <h2 style="color:#4f46e5;margin:0 0 12px">Reset your password</h2>
  <p style="font-size:14px;line-height:1.6;color:#444">
    We received a request to reset the password for your SEO Technical Audit
    account. Click the button below to choose a new password. This link expires
    in 1 hour. If you didn't request this, you can safely ignore this email.
  </p>
  <p style="margin:24px 0">
    <a href="{reset_url}"
       style="background:#4f46e5;color:#fff;text-decoration:none;padding:11px 20px;border-radius:8px;font-size:14px;font-weight:600;display:inline-block">
      Reset password
    </a>
  </p>
  <p style="font-size:12px;color:#888;word-break:break-all">
    Or paste this link into your browser:<br>{reset_url}
  </p>
</div>"""
