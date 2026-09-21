"""Transactional email for self-serve password-reset links.

Two backends, picked automatically:
  1. SMTP (e.g. Gmail) - set SMTP_USER + SMTP_PASSWORD (+ optional SMTP_HOST /
     SMTP_PORT / SMTP_FROM). For Gmail, SMTP_PASSWORD must be a Google
     **App Password** (16 chars, requires 2-Step Verification on the account) -
     a normal account password will NOT work.
  2. Resend (https://resend.com) - set RESEND_API_KEY (+ optional RESEND_FROM).

The whole feature is optional and degrades gracefully: if neither is
configured, `email_enabled()` is False and callers fall back to the
admin-resolved reset flow (no email sent). Nothing here ever raises to the
caller - email is best-effort.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

import requests

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
_DEFAULT_FROM = "SEO Audit <onboarding@resend.dev>"


# --------------------------------------------------------------------------- #
# capability detection
# --------------------------------------------------------------------------- #
def _smtp_configured() -> bool:
    return bool((os.environ.get("SMTP_USER") or "").strip()
                and (os.environ.get("SMTP_PASSWORD") or "").strip())


def _resend_configured() -> bool:
    return bool((os.environ.get("RESEND_API_KEY") or "").strip())


def email_enabled() -> bool:
    """True when any email backend is configured (email features are usable)."""
    return _smtp_configured() or _resend_configured()


def _from_address() -> str:
    return (
        os.environ.get("SMTP_FROM")
        or os.environ.get("RESEND_FROM")
        or os.environ.get("SMTP_USER")
        or _DEFAULT_FROM
    ).strip()


# --------------------------------------------------------------------------- #
# backends
# --------------------------------------------------------------------------- #
def _send_via_smtp(to: str, subject: str, html: str) -> dict:
    host = (os.environ.get("SMTP_HOST") or "smtp.gmail.com").strip()
    port = int(os.environ.get("SMTP_PORT") or 587)
    user = os.environ["SMTP_USER"].strip()
    password = os.environ["SMTP_PASSWORD"]  # Gmail App Password, keep spaces out

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _from_address()
    msg["To"] = to
    msg.set_content("Open this email in an HTML-capable client to reset your password.")
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.send_message(msg)
    return {"ok": True, "via": "smtp"}


def _send_via_resend(to: str, subject: str, html: str) -> dict:
    api_key = os.environ["RESEND_API_KEY"].strip()
    resp = requests.post(
        _RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": _from_address(), "to": [to], "subject": subject, "html": html},
        timeout=15,
    )
    if resp.status_code >= 400:
        logger.error("Resend send failed: %s %s", resp.status_code, resp.text[:500])
        return {"ok": False, "error": f"email provider returned {resp.status_code}"}
    data = resp.json() if resp.content else {}
    return {"ok": True, "via": "resend", "id": data.get("id")}


def send_email(to: str, subject: str, html: str) -> dict:
    """Send one email via the configured backend. Returns {"ok": bool, ...}.
    Never raises - email is best-effort; failures are logged and reported."""
    try:
        if _smtp_configured():
            return _send_via_smtp(to, subject, html)
        if _resend_configured():
            return _send_via_resend(to, subject, html)
        return {"ok": False, "error": "email is not configured"}
    except Exception as e:  # noqa: BLE001 - email is best-effort, never fatal
        logger.exception("email send raised")
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
