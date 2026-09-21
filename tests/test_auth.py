"""Tests for the auth core (worker/auth.py) and the api/auth.py handler.

Unit-tests the scrypt hashing + stateless HMAC session token, then drives the
signup/login/logout/me flow through a MagicMock BaseHTTPRequestHandler exactly
like tests/test_api_crawls.py.
"""
import importlib.util
import io
import json
import os
import time
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from worker import auth
from worker.db.models import Base


def _load(name, relative_path):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auth_api = _load("auth_under_test", "api/auth.py")


def _mock_handler(body: dict | None = None, cookie: str | None = None):
    encoded = json.dumps(body or {}).encode()
    h = MagicMock()
    headers = {"Content-Length": str(len(encoded))}
    if cookie:
        headers["Cookie"] = cookie
    h.headers = headers
    h.rfile = io.BytesIO(encoded)
    h.wfile = io.BytesIO()
    return h


def _status_and_body(h):
    return h.send_response.call_args[0][0], json.loads(h.wfile.getvalue())


def _set_cookie(h) -> str | None:
    for call in h.send_header.call_args_list:
        if call[0][0] == "Set-Cookie":
            return call[0][1]
    return None


def _cookie_header_from(set_cookie: str) -> str:
    # "sa_session=<token>; HttpOnly; ..." -> "sa_session=<token>"
    return set_cookie.split(";", 1)[0]


ADMIN_EMAIL = "owner@acme.test"


@pytest.fixture
def isolated_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(auth_api, "SessionLocal", factory)
    return factory


@pytest.fixture(autouse=True)
def pinned_admin(monkeypatch):
    """Pin a deterministic workspace admin for every test (the real default is
    a hard-coded company email)."""
    monkeypatch.setattr(auth, "ADMIN_EMAIL", ADMIN_EMAIL)
    return ADMIN_EMAIL


def _signup(email, password="hunter2xy", org="Acme"):
    h = _mock_handler({"action": "signup", "email": email, "password": password, "orgName": org})
    auth_api.handler.do_POST(h)
    return h


# ---- password hashing ----

def test_hash_and_verify_password():
    stored = auth.hash_password("correct horse battery staple")
    assert stored.startswith("scrypt:")
    assert auth.verify_password("correct horse battery staple", stored)
    assert not auth.verify_password("wrong", stored)


def test_verify_rejects_malformed_hash():
    assert not auth.verify_password("x", "not-a-hash")
    assert not auth.verify_password("x", "scrypt:zz:zz")


# ---- session token ----

def test_session_token_roundtrip():
    tok = auth.create_session_token(42)
    assert auth.verify_session_token(tok) == 42


def test_session_token_tampered_is_rejected():
    tok = auth.create_session_token(42)
    payload, _, sig = tok.partition(".")
    assert auth.verify_session_token(f"{payload}.{sig}x") is None
    assert auth.verify_session_token("garbage") is None
    assert auth.verify_session_token(None) is None


def test_session_token_expired_is_rejected(monkeypatch):
    tok = auth.create_session_token(7)
    future = time.time() + auth.SESSION_TTL_SECONDS + 10
    monkeypatch.setattr(auth.time, "time", lambda: future)
    assert auth.verify_session_token(tok) is None


# ---- api/auth.py flow ----

def test_signup_sets_cookie_and_returns_user(isolated_db):
    h = _signup(ADMIN_EMAIL, org="Acme")
    status, body = _status_and_body(h)
    assert status == 201
    assert body["user"]["email"] == ADMIN_EMAIL
    assert body["user"]["orgId"] is not None
    assert _set_cookie(h) and _cookie_header_from(_set_cookie(h)).startswith("sa_session=")


def test_duplicate_signup_returns_409(isolated_db):
    _signup(ADMIN_EMAIL, org="A")
    h2 = _signup(ADMIN_EMAIL, org="B")
    status, _ = _status_and_body(h2)
    assert status == 409


def test_short_password_rejected(isolated_db):
    h = _signup(ADMIN_EMAIL, password="short")
    status, _ = _status_and_body(h)
    assert status == 400


def test_login_wrong_password_401_no_leak(isolated_db):
    _signup(ADMIN_EMAIL, org="A")
    h2 = _mock_handler({"action": "login", "email": ADMIN_EMAIL, "password": "nope"})
    auth_api.handler.do_POST(h2)
    status, body = _status_and_body(h2)
    assert status == 401
    # same message whether the email exists or not (no existence leak)
    h3 = _mock_handler({"action": "login", "email": "ghost@b.com", "password": "nope"})
    auth_api.handler.do_POST(h3)
    _, body3 = _status_and_body(h3)
    assert body["error"] == body3["error"]


def test_login_then_me_roundtrip(isolated_db):
    h = _signup(ADMIN_EMAIL, org="A")
    cookie = _cookie_header_from(_set_cookie(h))

    me = _mock_handler(cookie=cookie)
    auth_api.handler.do_GET(me)
    status, body = _status_and_body(me)
    assert status == 200
    assert body["user"]["email"] == ADMIN_EMAIL


# ---- roles: admin vs user in one shared workspace ----

def test_admin_email_signup_gets_admin_role(isolated_db):
    h = _signup(ADMIN_EMAIL)
    status, body = _status_and_body(h)
    assert status == 201
    assert body["user"]["role"] == "admin"


def test_admin_email_is_case_insensitive(isolated_db):
    h = _signup("Owner@ACME.test")
    status, body = _status_and_body(h)
    assert status == 201
    assert body["user"]["role"] == "admin"


def test_user_joins_admin_workspace_as_user_role(isolated_db):
    admin_h = _signup(ADMIN_EMAIL, org="HQ")
    _, admin_body = _status_and_body(admin_h)
    admin_org = admin_body["user"]["orgId"]

    user_h = _signup("teammate@acme.test")
    status, body = _status_and_body(user_h)
    assert status == 201
    assert body["user"]["role"] == "user"
    # a regular user lands in the SAME shared workspace as the admin
    assert body["user"]["orgId"] == admin_org


def test_user_signup_blocked_until_admin_exists(isolated_db):
    h = _signup("teammate@acme.test")
    status, _ = _status_and_body(h)
    assert status == 403


def test_require_admin_allows_admin_blocks_user(isolated_db):
    factory = isolated_db
    admin_cookie = _cookie_header_from(_set_cookie(_signup(ADMIN_EMAIL)))
    user_cookie = _cookie_header_from(_set_cookie(_signup("teammate@acme.test")))

    with factory() as db:
        # admin passes (returns their uid, no raise)
        assert auth.require_admin(_mock_handler(cookie=admin_cookie), db) > 0
        # a signed-in regular user is rejected with 403
        with pytest.raises(auth.AuthError) as ei:
            auth.require_admin(_mock_handler(cookie=user_cookie), db)
        assert ei.value.status == 403


def test_me_without_cookie_is_null(isolated_db):
    me = _mock_handler()
    auth_api.handler.do_GET(me)
    status, body = _status_and_body(me)
    assert status == 200 and body["user"] is None


def test_logout_clears_cookie(isolated_db):
    h = _mock_handler({"action": "logout"})
    auth_api.handler.do_POST(h)
    status, _ = _status_and_body(h)
    assert status == 200
    assert "Max-Age=0" in _set_cookie(h)


# ---- password reset (admin-resolved) + user management ----

def _post(action, body=None, cookie=None):
    h = _mock_handler({"action": action, **(body or {})}, cookie=cookie)
    auth_api.handler.do_POST(h)
    return h


def test_request_password_reset_always_ok_no_leak(isolated_db):
    _signup(ADMIN_EMAIL)
    # existing email
    h1 = _post("request-password-reset", {"email": ADMIN_EMAIL})
    s1, b1 = _status_and_body(h1)
    # unknown email - same ok response (no existence leak)
    h2 = _post("request-password-reset", {"email": "ghost@nope.test"})
    s2, b2 = _status_and_body(h2)
    assert s1 == 200 and b1.get("ok") is True
    assert s2 == 200 and b2.get("ok") is True


def test_admin_list_users_requires_admin(isolated_db):
    admin_cookie = _cookie_header_from(_set_cookie(_signup(ADMIN_EMAIL)))
    user_cookie = _cookie_header_from(_set_cookie(_signup("teammate@acme.test")))

    # admin sees both users
    ha = _post("admin-list-users", cookie=admin_cookie)
    sa, ba = _status_and_body(ha)
    assert sa == 200
    emails = {u["email"] for u in ba["users"]}
    assert ADMIN_EMAIL in emails and "teammate@acme.test" in emails

    # a regular user is blocked (403)
    hu = _post("admin-list-users", cookie=user_cookie)
    su, _ = _status_and_body(hu)
    assert su == 403


def test_forgot_then_admin_resolve_issues_working_temp_password(isolated_db):
    admin_cookie = _cookie_header_from(_set_cookie(_signup(ADMIN_EMAIL)))
    _signup("teammate@acme.test", password="original8")

    # user forgets password -> request queued
    _post("request-password-reset", {"email": "teammate@acme.test"})
    lst = _post("admin-list-reset-requests", cookie=admin_cookie)
    _, lb = _status_and_body(lst)
    assert len(lb["requests"]) == 1
    req_id = lb["requests"][0]["id"]

    # admin resolves -> gets a one-time temp password
    res = _post("admin-resolve-reset", {"requestId": req_id}, cookie=admin_cookie)
    sr, rb = _status_and_body(res)
    assert sr == 200 and rb["email"] == "teammate@acme.test"
    temp = rb["tempPassword"]
    assert temp

    # the temp password actually works for login; the old one no longer does
    ok = _post("login", {"email": "teammate@acme.test", "password": temp})
    assert _status_and_body(ok)[0] == 200
    bad = _post("login", {"email": "teammate@acme.test", "password": "original8"})
    assert _status_and_body(bad)[0] == 401

    # request no longer pending
    lst2 = _post("admin-list-reset-requests", cookie=admin_cookie)
    assert len(_status_and_body(lst2)[1]["requests"]) == 0


def test_admin_reset_user_directly(isolated_db):
    admin_cookie = _cookie_header_from(_set_cookie(_signup(ADMIN_EMAIL)))
    uh = _signup("teammate@acme.test", password="original8")
    _, ub = _status_and_body(uh)
    uid = ub["user"]["id"]

    res = _post("admin-reset-user", {"userId": uid}, cookie=admin_cookie)
    sr, rb = _status_and_body(res)
    assert sr == 200
    temp = rb["tempPassword"]
    assert _status_and_body(_post("login", {"email": "teammate@acme.test", "password": temp}))[0] == 200


def test_change_password_self_service(isolated_db):
    h = _signup(ADMIN_EMAIL, password="original8")
    cookie = _cookie_header_from(_set_cookie(h))

    # wrong current password rejected
    bad = _post("change-password", {"currentPassword": "wrongpass", "newPassword": "newpass99"}, cookie=cookie)
    assert _status_and_body(bad)[0] == 400

    # correct current password succeeds, new one works
    ok = _post("change-password", {"currentPassword": "original8", "newPassword": "newpass99"}, cookie=cookie)
    assert _status_and_body(ok)[0] == 200
    assert _status_and_body(_post("login", {"email": ADMIN_EMAIL, "password": "newpass99"}))[0] == 200


def test_admin_actions_blocked_without_session_in_prod(isolated_db, monkeypatch):
    # deployed (VERCEL set) with no session -> 401
    monkeypatch.setenv("VERCEL", "1")
    h = _post("admin-list-users")
    assert _status_and_body(h)[0] == 401


# ---- email-based reset (token) ----

def test_email_reset_token_roundtrip(isolated_db):
    factory = isolated_db
    _signup(ADMIN_EMAIL)  # admin must exist before a user can join
    _signup("teammate@acme.test", password="original8")
    with factory() as db:
        token = auth.create_email_reset_token(db, "teammate@acme.test")
        assert token
        # unknown email yields no token, but doesn't raise
        assert auth.create_email_reset_token(db, "ghost@nope.test") is None
    # complete the reset via the API action
    ok = _post("reset-password-with-token", {"token": token, "newPassword": "brandnew9"})
    assert _status_and_body(ok)[0] == 200
    assert _status_and_body(_post("login", {"email": "teammate@acme.test", "password": "brandnew9"}))[0] == 200
    # token is single-use now -> reject
    again = _post("reset-password-with-token", {"token": token, "newPassword": "another99"})
    assert _status_and_body(again)[0] == 400


def test_email_reset_token_expired_rejected(isolated_db):
    factory = isolated_db
    _signup(ADMIN_EMAIL)  # admin must exist before a user can join
    _signup("teammate@acme.test", password="original8")
    with factory() as db:
        token = auth.create_email_reset_token(db, "teammate@acme.test", ttl_seconds=-1)
        assert token
    bad = _post("reset-password-with-token", {"token": token, "newPassword": "brandnew9"})
    assert _status_and_body(bad)[0] == 400


def test_reset_with_bad_token_rejected(isolated_db):
    h = _post("reset-password-with-token", {"token": "not-a-real-token", "newPassword": "brandnew9"})
    assert _status_and_body(h)[0] == 400
