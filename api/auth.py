"""Auth API - signup / login / logout / me. Same one-file/action-dispatch
convention as the other api/*.py handlers, but POST actions may also set or
clear the session cookie, so responses go through a small cookie-aware
responder instead of modules._http.send_json.

  POST /api/auth {"action": "signup", "email", "password", "orgName"}
  POST /api/auth {"action": "login",  "email", "password"}
  POST /api/auth {"action": "logout"}
  GET  /api/auth   -> {"user": {...}} or {"user": null}
"""

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import read_json_body, require_str, send_json  # noqa: E402
from worker.auth import (  # noqa: E402
    AuthError, build_session_cookie, create_session_token,
    create_email_reset_token, create_password_reset_request,
    generate_temp_password, get_session_user_id,
    list_password_reset_requests, list_users, login as auth_login,
    primary_org_id, require_admin, require_user_id,
    reset_password_with_token, resolve_password_reset_request,
    role_for_user, set_user_password, signup as auth_signup, verify_password,
)
from worker.db.models import User  # noqa: E402
from worker.db.session import SessionLocal  # noqa: E402
from worker.email_send import email_enabled, password_reset_html, send_email  # noqa: E402


def _base_url(handler) -> str:
    """Public origin for links in emails (e.g. password-reset). ALWAYS prefer the
    pinned APP_BASE_URL. On a real deploy we NEVER derive it from the request's
    Host/X-Forwarded-Host header, because an attacker can spoof that to send a
    victim a genuine reset email whose link points at attacker.com (Host-header
    poisoning -> token theft -> account takeover). Header-derived origin is used
    only in local dev, where APP_BASE_URL is typically unset."""
    override = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    if override:
        return override
    from worker.auth import dev_mode
    if not dev_mode():
        # Deployed but APP_BASE_URL missing: refuse to trust the Host header.
        # Callers treat "" as "no base URL" and skip building an external link.
        logger.error("APP_BASE_URL is not set on a deployed host; refusing to build a link from the Host header.")
        return ""
    host = handler.headers.get("x-forwarded-host") or handler.headers.get("Host") or ""
    proto = handler.headers.get("x-forwarded-proto") or "http"
    return f"{proto}://{host}".rstrip("/") if host else ""

logger = logging.getLogger(__name__)


def _send_json_with_cookie(handler, status, data, cookie: str | None = None):
    body = json.dumps(data, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    if cookie:
        handler.send_header("Set-Cookie", cookie)
    handler.end_headers()
    handler.wfile.write(body)


def _user_dto(db, user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "orgId": primary_org_id(db, user.id),
        "role": role_for_user(db, user.id),  # "admin" | "user"
    }


def _handle_signup(handler, payload):
    email = require_str(handler, payload, "email", field_name="email")
    if email is None:
        return
    password = require_str(handler, payload, "password", field_name="password")
    if password is None:
        return
    if len(password) < 8:
        send_json(handler, 400, {"error": "password must be at least 8 characters"})
        return
    org_name = (payload.get("orgName") or payload.get("org_name") or "").strip()

    try:
        with SessionLocal() as db:
            user = auth_signup(db, email, password, org_name)
            dto = _user_dto(db, user)
            cookie = build_session_cookie(create_session_token(user.id))
            _send_json_with_cookie(handler, 201, {"user": dto}, cookie)
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py signup failed")
        send_json(handler, 500, {"error": "Internal error during signup."})


def _handle_login(handler, payload):
    email = require_str(handler, payload, "email", field_name="email")
    if email is None:
        return
    password = require_str(handler, payload, "password", field_name="password")
    if password is None:
        return
    try:
        with SessionLocal() as db:
            user = auth_login(db, email, password)
            if user is None:
                send_json(handler, 401, {"error": "invalid email or password"})
                return
            dto = _user_dto(db, user)
            cookie = build_session_cookie(create_session_token(user.id))
            _send_json_with_cookie(handler, 200, {"user": dto}, cookie)
    except Exception:  # noqa: BLE001
        logger.exception("auth.py login failed")
        send_json(handler, 500, {"error": "Internal error during login."})


def _handle_logout(handler, payload):
    _send_json_with_cookie(handler, 200, {"ok": True}, build_session_cookie("", clear=True))


# --------------------------------------------------------------------------- #
# Password reset (admin-resolved) + self-service change-password
# --------------------------------------------------------------------------- #
def _handle_request_password_reset(handler, payload):
    """Public: a user reports they've forgotten their password. Always returns
    ok (no existence leak). If email is configured (RESEND_API_KEY), sends a
    reset link; otherwise falls back to the admin-resolved queue."""
    email = require_str(handler, payload, "email", field_name="email")
    if email is None:
        return
    try:
        if email_enabled():
            with SessionLocal() as db:
                token = create_email_reset_token(db, email)
            # Only send when the email matched a real account; either way the
            # response is identical so account existence never leaks.
            if token:
                base = _base_url(handler)
                reset_url = f"{base}/reset?token={token}" if base else f"/reset?token={token}"
                send_email(email.strip(), "Reset your password", password_reset_html(reset_url))
            send_json(handler, 200, {"ok": True, "emailed": True})
        else:
            with SessionLocal() as db:
                create_password_reset_request(db, email)
            send_json(handler, 200, {"ok": True, "emailed": False})
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py request-password-reset failed")
        send_json(handler, 500, {"error": "Internal error while submitting the request."})


def _handle_reset_with_token(handler, payload):
    """Public: complete a self-serve email reset using the token from the link."""
    token = require_str(handler, payload, "token", field_name="token")
    if token is None:
        return
    new_password = require_str(handler, payload, "newPassword", field_name="newPassword")
    if new_password is None:
        return
    try:
        with SessionLocal() as db:
            ok = reset_password_with_token(db, token, new_password)
        if not ok:
            send_json(handler, 400, {"error": "This reset link is invalid or has expired. Request a new one."})
            return
        send_json(handler, 200, {"ok": True})
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py reset-password-with-token failed")
        send_json(handler, 500, {"error": "Internal error while resetting the password."})


def _handle_change_password(handler, payload):
    """Self-service: a signed-in user changes their own password after
    confirming their current one."""
    current = require_str(handler, payload, "currentPassword", field_name="currentPassword")
    if current is None:
        return
    new_password = require_str(handler, payload, "newPassword", field_name="newPassword")
    if new_password is None:
        return
    try:
        with SessionLocal() as db:
            uid = require_user_id(handler)
            user = db.get(User, uid)
            if user is None or not verify_password(current, user.password_hash):
                send_json(handler, 400, {"error": "current password is incorrect"})
                return
            set_user_password(db, uid, new_password)
        send_json(handler, 200, {"ok": True})
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py change-password failed")
        send_json(handler, 500, {"error": "Internal error while changing the password."})


# --------------------------------------------------------------------------- #
# Admin-only: user management + resolving reset requests
# --------------------------------------------------------------------------- #
def _handle_admin_list_users(handler, payload):
    try:
        with SessionLocal() as db:
            require_admin(handler, db)
            send_json(handler, 200, {"users": list_users(db)})
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py admin-list-users failed")
        send_json(handler, 500, {"error": "Internal error while listing users."})


def _handle_admin_list_reset_requests(handler, payload):
    try:
        with SessionLocal() as db:
            require_admin(handler, db)
            status = (payload.get("status") or "pending").strip()
            send_json(handler, 200, {"requests": list_password_reset_requests(db, status)})
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py admin-list-reset-requests failed")
        send_json(handler, 500, {"error": "Internal error while listing reset requests."})


def _handle_admin_resolve_reset(handler, payload):
    """Admin resolves a reset request -> returns a one-time temp password to
    share with the user."""
    request_id = payload.get("requestId")
    if not isinstance(request_id, int):
        send_json(handler, 400, {"error": "requestId (integer) is required"})
        return
    try:
        with SessionLocal() as db:
            admin_uid = require_admin(handler, db)
            result = resolve_password_reset_request(db, request_id, admin_uid)
        send_json(handler, 200, result)
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py admin-resolve-reset failed")
        send_json(handler, 500, {"error": "Internal error while resolving the request."})


def _handle_admin_set_password(handler, payload):
    """Admin directly sets a chosen user's password (returns nothing sensitive)."""
    user_id = payload.get("userId")
    if not isinstance(user_id, int):
        send_json(handler, 400, {"error": "userId (integer) is required"})
        return
    new_password = require_str(handler, payload, "newPassword", field_name="newPassword")
    if new_password is None:
        return
    try:
        with SessionLocal() as db:
            require_admin(handler, db)
            ok = set_user_password(db, user_id, new_password)
        if not ok:
            send_json(handler, 404, {"error": "user not found"})
            return
        send_json(handler, 200, {"ok": True})
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py admin-set-password failed")
        send_json(handler, 500, {"error": "Internal error while setting the password."})


def _handle_admin_reset_user(handler, payload):
    """Admin resets a chosen user's password to a fresh temp one (returned once
    to share with the user). Proactive counterpart to admin-resolve-reset."""
    user_id = payload.get("userId")
    if not isinstance(user_id, int):
        send_json(handler, 400, {"error": "userId (integer) is required"})
        return
    try:
        with SessionLocal() as db:
            require_admin(handler, db)
            temp = generate_temp_password()
            ok = set_user_password(db, user_id, temp)
            if not ok:
                send_json(handler, 404, {"error": "user not found"})
                return
            user = db.get(User, user_id)
            send_json(handler, 200, {"ok": True, "tempPassword": temp, "email": user.email})
    except AuthError as e:
        send_json(handler, e.status, {"error": e.message})
    except Exception:  # noqa: BLE001
        logger.exception("auth.py admin-reset-user failed")
        send_json(handler, 500, {"error": "Internal error while resetting the password."})


_ACTIONS = {
    "signup": _handle_signup,
    "login": _handle_login,
    "logout": _handle_logout,
    "request-password-reset": _handle_request_password_reset,
    "reset-password-with-token": _handle_reset_with_token,
    "change-password": _handle_change_password,
    "admin-list-users": _handle_admin_list_users,
    "admin-list-reset-requests": _handle_admin_list_reset_requests,
    "admin-resolve-reset": _handle_admin_resolve_reset,
    "admin-set-password": _handle_admin_set_password,
    "admin-reset-user": _handle_admin_reset_user,
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # "me" - current session user, or null.
        try:
            uid = get_session_user_id(self)
            if uid is None:
                send_json(self, 200, {"user": None})
                return
            with SessionLocal() as db:
                user = db.get(User, uid)
                send_json(self, 200, {"user": _user_dto(db, user) if user else None})
        except Exception:  # noqa: BLE001
            logger.exception("auth.py me failed")
            send_json(self, 500, {"error": "Internal error."})

    def do_POST(self):
        try:
            payload = read_json_body(self)
        except Exception:  # noqa: BLE001
            logger.exception("auth.py request body could not be parsed")
            send_json(self, 500, {"error": "Internal error while processing the request."})
            return
        action = payload.get("action")
        fn = _ACTIONS.get(action)
        if fn is None:
            send_json(self, 400, {"error": f"Unknown or missing action (expected one of {sorted(_ACTIONS)})"})
            return
        fn(self, payload)
