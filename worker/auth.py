"""Authentication core (Python port of web/lib/auth.ts) —
05-INFRASTRUCTURE-AND-OPS.md §4: every API query must scope by an org_id
derived from the authenticated session, never a client-supplied id.

Dependency-free by design: passwords use hashlib.scrypt, sessions are
stateless HMAC-SHA256-signed cookies — no bcrypt / jwt / auth libs. The live
tool's backend is Python serverless functions, so both signing and verifying
happen here; a revocable server-side session store can replace the stateless
token later without changing the api/*.py call sites (require_user_id).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from http.cookies import SimpleCookie

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import secrets

from worker.db.models import (
    Membership,
    Organization,
    PasswordResetRequest,
    User,
)

SESSION_COOKIE = "sa_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days

# --------------------------------------------------------------------------- #
# Roles / workspace admin
#
# Two account types only: the designated ADMIN_EMAIL is the workspace "admin"
# (owner of the single shared org); every other signup joins that same
# workspace as a "user". Overridable via the ADMIN_EMAIL env var so the admin
# can be renamed on a deploy without a code change.
# --------------------------------------------------------------------------- #
ADMIN_EMAIL = (os.environ.get("ADMIN_EMAIL") or "venkat.r@edstellar.com").strip().lower()
ROLE_ADMIN = "admin"
ROLE_USER = "user"


def is_admin_email(email: str) -> bool:
    return email.strip().lower() == ADMIN_EMAIL

# scrypt cost parameters (interop not required — Python signs and verifies).
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
_SCRYPT_MAXMEM = 128 * 1024 * 1024


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _auth_secret() -> str:
    s = os.environ.get("AUTH_SECRET")
    if s and len(s) >= 16:
        return s
    # VERCEL=1 is set for every deployed (prod + preview) invocation.
    if os.environ.get("VERCEL"):
        raise AuthError(500, "AUTH_SECRET must be set (>= 16 chars) in production")
    return "dev-insecure-secret-do-not-use-in-prod"


def _is_prod() -> bool:
    return bool(os.environ.get("VERCEL"))


# --------------------------------------------------------------------------- #
# password hashing (scrypt)
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN, maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt:{salt.hex()}:{derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    parts = (stored or "").split(":")
    if len(parts) != 3 or parts[0] != "scrypt":
        return False
    try:
        salt = bytes.fromhex(parts[1])
        expected = bytes.fromhex(parts[2])
    except ValueError:
        return False
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=len(expected), maxmem=_SCRYPT_MAXMEM,
    )
    return hmac.compare_digest(expected, derived)


# --------------------------------------------------------------------------- #
# stateless session token (HMAC-SHA256)
# --------------------------------------------------------------------------- #
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    sig = hmac.new(_auth_secret().encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return _b64url(sig)


def create_session_token(user_id: int) -> str:
    exp = int(time.time()) + SESSION_TTL_SECONDS
    payload = _b64url(json.dumps({"uid": int(user_id), "exp": exp}).encode("utf-8"))
    return f"{payload}.{_sign(payload)}"


def verify_session_token(token: str | None) -> int | None:
    if not token or "." not in token:
        return None
    payload, _, sig = token.partition(".")
    if not payload or not sig:
        return None
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        obj = json.loads(_b64url_decode(payload))
        uid, exp = obj.get("uid"), obj.get("exp")
        if not isinstance(uid, int) or not isinstance(exp, int):
            return None
        if exp < int(time.time()):
            return None
        return uid
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# cookie helpers for the http.server-style handlers
# --------------------------------------------------------------------------- #
def build_session_cookie(token: str, *, clear: bool = False) -> str:
    parts = [
        f"{SESSION_COOKIE}={'' if clear else token}",
        "HttpOnly",
        "SameSite=Lax",
        "Path=/",
        f"Max-Age={0 if clear else SESSION_TTL_SECONDS}",
    ]
    if _is_prod():
        parts.append("Secure")
    return "; ".join(parts)


def read_session_cookie(handler) -> str | None:
    raw = handler.headers.get("Cookie")
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:  # noqa: BLE001
        return None
    morsel = jar.get(SESSION_COOKIE)
    return morsel.value if morsel else None


def get_session_user_id(handler) -> int | None:
    """The authenticated user id from the request's session cookie, or None."""
    return verify_session_token(read_session_cookie(handler))


def require_user_id(handler) -> int:
    """Authenticated user id, or raise AuthError(401)."""
    uid = get_session_user_id(handler)
    if uid is None:
        raise AuthError(401, "authentication required")
    return uid


# --------------------------------------------------------------------------- #
# DB operations (users / organizations / memberships)
# --------------------------------------------------------------------------- #
def signup(db, email: str, password: str, org_name: str) -> User:
    """Create a user account.

    The designated ADMIN_EMAIL creates and owns the single shared workspace
    (role 'admin'); every other signup joins that same workspace as a 'user'.
    Raises AuthError(409) if the email already exists, or AuthError(403) if a
    non-admin tries to sign up before the admin workspace has been created."""
    email = email.strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise AuthError(409, "an account with this email already exists")

    admin = is_admin_email(email)

    # A regular user can only join once the admin workspace exists.
    join_org_id = None
    if not admin:
        join_org_id = _admin_org_id(db)
        if join_org_id is None:
            raise AuthError(
                403,
                "This workspace isn't set up yet. Ask your administrator to create the account first.",
            )

    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    try:
        db.flush()
        if admin:
            org = Organization(name=org_name.strip() or f"{email}'s workspace")
            db.add(org)
            db.flush()
            db.add(Membership(user_id=user.id, org_id=org.id, role=ROLE_ADMIN))
        else:
            db.add(Membership(user_id=user.id, org_id=join_org_id, role=ROLE_USER))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AuthError(409, "an account with this email already exists")
    return user


def _admin_org_id(db) -> int | None:
    """The org owned by the designated admin, or None if the admin hasn't
    registered yet (so non-admin signups can be held back until then)."""
    admin_user_id = db.scalar(select(User.id).where(User.email == ADMIN_EMAIL))
    if admin_user_id is None:
        return None
    return primary_org_id(db, admin_user_id)


def role_for_user(db, user_id: int) -> str | None:
    """The user's role in their primary org ('admin' | 'user'), or None if the
    user has no membership."""
    return db.scalar(
        select(Membership.role)
        .where(Membership.user_id == user_id)
        .order_by(Membership.org_id)
    )


def is_admin(db, user_id: int) -> bool:
    return role_for_user(db, user_id) == ROLE_ADMIN


def require_admin(handler, db) -> int:
    """Authenticated user id if they are the workspace admin, else raise.

    Mirrors access.resolve_org_id's dev/test fallback: deployed (VERCEL) with
    no session -> 401; a signed-in non-admin -> 403; local/dev and the test
    suite (no VERCEL, no session) fall through as privileged so the existing
    single-tenant flows and pytest keep working without seeding a login."""
    uid = get_session_user_id(handler)
    if uid is None:
        if os.environ.get("VERCEL"):
            raise AuthError(401, "authentication required")
        return 0
    if not is_admin(db, uid):
        raise AuthError(403, "admin privileges required")
    return uid


_dummy_hash: str | None = None


def login(db, email: str, password: str) -> User | None:
    """Return the user on valid credentials, else None (no existence leak — the
    no-such-user path still runs a scrypt verify against a dummy hash so its
    response time matches the wrong-password path, closing the timing oracle)."""
    global _dummy_hash
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password("timing-equalizer-not-a-real-password")
        verify_password(password, _dummy_hash)  # constant-time-ish miss path
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def primary_org_id(db, user_id: int) -> int | None:
    """The org id for the user's first membership (owner workspace)."""
    return db.scalar(
        select(Membership.org_id).where(Membership.user_id == user_id).order_by(Membership.org_id)
    )


# --------------------------------------------------------------------------- #
# Admin user management + password reset (admin-resolved, no email service)
# --------------------------------------------------------------------------- #
def list_users(db) -> list[dict]:
    """Every user with their role — for the admin portal's Users screen."""
    rows = db.execute(
        select(User.id, User.email, User.created_at, Membership.role)
        .join(Membership, Membership.user_id == User.id, isouter=True)
        .order_by(User.id)
    ).all()
    return [
        {
            "id": r.id,
            "email": r.email,
            "role": r.role,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def set_user_password(db, user_id: int, new_password: str) -> bool:
    """Set a user's password (admin action or self-service). Returns False if
    the user doesn't exist. Raises AuthError(400) if the password is too short."""
    if len(new_password) < 8:
        raise AuthError(400, "password must be at least 8 characters")
    user = db.get(User, user_id)
    if user is None:
        return False
    user.password_hash = hash_password(new_password)
    db.commit()
    return True


def generate_temp_password() -> str:
    """A readable, URL-safe temporary password (12 chars) the admin can hand to
    a user after resolving a reset request."""
    return secrets.token_urlsafe(9)  # ~12 chars


def create_password_reset_request(db, email: str) -> None:
    """Record a 'forgot password' request. Always succeeds silently (no
    existence leak): links to a user_id when the email matches, else stores the
    raw email for the admin to see as 'no matching account'. Collapses repeated
    pending requests for the same email into one."""
    email = (email or "").strip().lower()
    if not email:
        raise AuthError(400, "email is required")
    user_id = db.scalar(select(User.id).where(User.email == email))
    existing = db.scalar(
        select(PasswordResetRequest.id).where(
            PasswordResetRequest.email == email,
            PasswordResetRequest.status == "pending",
        )
    )
    if existing is not None:
        return  # already pending — don't pile up duplicates
    db.add(PasswordResetRequest(email=email, user_id=user_id, status="pending"))
    db.commit()


def list_password_reset_requests(db, status: str = "pending") -> list[dict]:
    """Reset requests for the admin portal, newest first."""
    stmt = select(PasswordResetRequest).order_by(PasswordResetRequest.created_at.desc())
    if status:
        stmt = stmt.where(PasswordResetRequest.status == status)
    out = []
    for req in db.scalars(stmt):
        out.append(
            {
                "id": req.id,
                "email": req.email,
                "userId": req.user_id,
                "hasAccount": req.user_id is not None,
                "status": req.status,
                "createdAt": req.created_at.isoformat() if req.created_at else None,
                "resolvedAt": req.resolved_at.isoformat() if req.resolved_at else None,
            }
        )
    return out


def resolve_password_reset_request(db, request_id: int, admin_user_id: int) -> dict:
    """Resolve a reset request by setting a fresh temporary password on the
    target user. Returns {ok, tempPassword, email} — the temp password is
    returned exactly once for the admin to share; it is never stored in
    plaintext. Raises AuthError(404) if the request or its user is gone."""
    import datetime as _dt

    # Naive UTC, matching the DateTime (no tz) columns and safe on Postgres.
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)

    req = db.get(PasswordResetRequest, request_id)
    if req is None or req.status != "pending":
        raise AuthError(404, "reset request not found or already resolved")
    if req.user_id is None:
        # No account for that email — mark resolved so it leaves the queue.
        req.status = "resolved"
        req.resolved_at = now
        req.resolved_by = admin_user_id or None
        db.commit()
        raise AuthError(404, "no account exists for that email; request cleared")

    temp = generate_temp_password()
    user = db.get(User, req.user_id)
    if user is None:
        raise AuthError(404, "the user for this request no longer exists")
    user.password_hash = hash_password(temp)
    req.status = "resolved"
    req.resolved_at = now
    req.resolved_by = admin_user_id or None
    db.commit()
    return {"ok": True, "tempPassword": temp, "email": user.email}
