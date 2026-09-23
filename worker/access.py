"""Per-request org resolution + ownership checks (05-INFRASTRUCTURE-AND-OPS.md
§4: every org-scoped query must derive its org_id from the authenticated
session, never a client-supplied id).

Deployed (VERCEL) environments require a valid session and use its org - an
unauthenticated request to an org-scoped endpoint gets 401. Local/dev and the
test suite (no VERCEL, no auth infra) fall back to a single default org so the
existing flows and pytest keep working without seeding a login.
"""
from __future__ import annotations

from sqlalchemy import select

from worker.auth import AuthError, dev_mode, get_session_user_id, primary_org_id
from worker.db.models import Crawl, Project


def resolve_org_id(handler, db) -> int | None:
    """The org this request acts within, or None for 'no scoping'.

    - Authenticated session -> that user's org (isolation enforced).
    - Deployed (VERCEL) but no valid session -> AuthError(401).
    - Dev/test (no VERCEL, no session) -> None, meaning the caller applies no
      org filter, preserving the pre-auth single-tenant behavior and keeping the
      existing test suite green.
    """
    uid = get_session_user_id(handler)
    if uid is not None:
        oid = primary_org_id(db, uid)
        if oid is not None:
            return oid
    # Fail CLOSED on any real deploy, consistent with worker/auth.py's gating.
    # (Previously keyed on raw VERCEL env, which fails OPEN on a non-Vercel host
    # that doesn't set VERCEL - an unauthenticated request would get no scoping
    # instead of 401.) dev_mode() is only true locally/in tests.
    if not dev_mode():
        raise AuthError(401, "authentication required")
    return None


def get_or_create_project(db, org_id: int, root_url: str) -> Project:
    """Get-or-create a project scoped to (org_id, root_url) - so two orgs
    crawling the same URL get separate projects rather than sharing one."""
    proj = db.execute(
        select(Project).where(Project.org_id == org_id, Project.root_url == root_url)
    ).scalar_one_or_none()
    if proj is not None:
        return proj
    proj = Project(org_id=org_id, name=root_url, root_url=root_url)
    db.add(proj)
    db.flush()
    return proj


def crawl_for_org(db, crawl_id: int, org_id: int) -> Crawl | None:
    """The crawl only if it belongs to org_id, else None (callers return 404 -
    no existence leak between tenants)."""
    return db.execute(
        select(Crawl)
        .join(Project, Crawl.project_id == Project.id)
        .where(Crawl.id == crawl_id, Project.org_id == org_id)
    ).scalar_one_or_none()
