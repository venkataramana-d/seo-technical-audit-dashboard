"""SQLAlchemy models for the crawl/audit platform (Phase 0 foundations).

Deliberately dialect-neutral: `JSON` (not `postgresql.JSONB`) and integer
autoincrement primary keys (not `UUID`) so the exact same models run
unchanged against local SQLite (dev) and Postgres (production) — swapping
`DATABASE_URL` in `worker/db/session.py` is the only change needed later.

Table shapes follow `03-DATA-MODEL-AND-API.md` from the rebuild plan.
`schedules`/`alert_rules` stay deferred (Phase 3 folded scheduling into
`CrawlConfig` instead — see `worker/scheduler.py`); `api_keys` was added in
Phase 5 (`worker/vault.py`/`worker/api_key_service.py`). `users`/
`organizations`/`memberships` exist so foreign keys resolve, but there is no
login flow yet — see the Phase 0 plan's scope note.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Tenancy (schema only — see Phase 0 plan's auth scope decision)
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(50), default="free", server_default="free")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    projects: Mapped[list["Project"]] = relationship(back_populates="organization")


class Membership(Base):
    __tablename__ = "memberships"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    # Roles are "admin" | "user" (worker/auth.py). Default to the least-privileged
    # "user" so a stray row is never accidentally treated as privileged.
    role: Mapped[str] = mapped_column(String(50), default="user", server_default="user")


class PasswordResetRequest(Base):
    """A user's "I forgot my password" request. The admin resolves it from the
    admin portal by setting a new (temporary) password — no email service is
    involved. `email` is stored denormalized so a request can be raised for an
    address even if it doesn't match a user (shown to the admin as "no such
    account") without leaking existence back to the requester."""

    __tablename__ = "password_reset_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )  # pending|resolved
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Self-serve email reset (optional path, when RESEND_API_KEY is set): the
    # emailed link carries a random token; only its SHA-256 hash is stored, with
    # a short expiry. NULL for admin-resolved requests.
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Projects & crawl config
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="projects")
    crawl_configs: Mapped[list["CrawlConfig"]] = relationship(back_populates="project")
    crawls: Mapped[list["Crawl"]] = relationship(back_populates="project")


class CrawlConfig(Base):
    __tablename__ = "crawl_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # website|sitemap|url_list
    scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    robots_mode: Mapped[str] = mapped_column(String(50), default="respect")  # respect|ignore|ignore_report
    render_js: Mapped[bool] = mapped_column(Boolean, default=False)
    max_pages: Mapped[int] = mapped_column(Integer, default=1000)
    max_duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    concurrency: Mapped[int] = mapped_column(Integer, default=5)
    requests_per_second: Mapped[float] = mapped_column(Float, default=1.0)
    user_agent: Mapped[str] = mapped_column(String(255), default="SEOAuditBot/1.0")
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Phase 3: when this config's schedule is next due. NULL means either
    # unscheduled (schedule_cron is also NULL) or due immediately once set.
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="crawl_configs")


# ---------------------------------------------------------------------------
# A single crawl run + everything it produces
# ---------------------------------------------------------------------------


class Crawl(Base):
    __tablename__ = "crawls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    # Which CrawlConfig snapshot this run used — a project can accumulate
    # multiple crawl_configs over time (settings changed between runs), so
    # this pins a Crawl to the exact config it was launched with rather than
    # requiring an ambiguous "latest config for this project" lookup.
    crawl_config_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_configs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="queued")  # queued|running|paused|completed|failed
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0)
    pages_total_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    seo_score_avg: Mapped[float | None] = mapped_column(Float, nullable=True)

    project: Mapped["Project"] = relationship(back_populates="crawls")
    pages: Mapped[list["Page"]] = relationship(back_populates="crawl")
    issues: Mapped[list["Issue"]] = relationship(back_populates="crawl")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crawl_id: Mapped[int] = mapped_column(ForeignKey("crawls.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redirect_chain_json: Mapped[list] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    h1: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_html_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    rendered_html_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    seo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Phase 2 additions — all already computed per-page by modules/auditor.py
    # and modules/advanced_checks.py; these columns are what finally persist
    # them so worker/site_audit.py can aggregate across a whole crawl.
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    is_indexable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hreflang_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    schema_types_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    crawl: Mapped["Crawl"] = relationship(back_populates="pages")
    links: Mapped[list["Link"]] = relationship(back_populates="page")
    issues: Mapped[list["Issue"]] = relationship(back_populates="page")


class Link(Base):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), nullable=False)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    link_type: Mapped[str] = mapped_column(String(50), nullable=False)  # internal|external|mailto|tel|anchor|js
    dom_location: Mapped[str | None] = mapped_column(String(50), nullable=True)  # nav|header|footer|sidebar|breadcrumb|body
    anchor_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_nofollow: Mapped[bool] = mapped_column(Boolean, default=False)
    is_dofollow: Mapped[bool] = mapped_column(Boolean, default=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_broken: Mapped[bool] = mapped_column(Boolean, default=False)

    page: Mapped["Page"] = relationship(back_populates="links")


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    crawl_id: Mapped[int] = mapped_column(ForeignKey("crawls.id"), nullable=False)
    page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id"), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # error|warning|notice
    impact_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effort_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    explanation_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    crawl: Mapped["Crawl"] = relationship(back_populates="issues")
    page: Mapped["Page"] = relationship(back_populates="issues")


class IssueTypeConfig(Base):
    """Lets an org reclassify an issue type's default severity (e.g. 'missing
    alt text' as Warning instead of Notice) — per-org override, global
    default otherwise."""

    __tablename__ = "issue_type_config"

    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    issue_type: Mapped[str] = mapped_column(String(100), primary_key=True)
    severity_override: Mapped[str] = mapped_column(String(20), nullable=False)


# ---------------------------------------------------------------------------
# Local job queue (worker/queue.py) — stands in for Redis+Celery/arq locally
# ---------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|completed|failed
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Credential vault (Phase 5) — worker/vault.py encrypts, worker/api_key_service.py owns access
# ---------------------------------------------------------------------------


class ApiKey(Base):
    """One encrypted provider credential per (org, provider). `encrypted_value`
    is a Fernet token (see worker/vault.py) — never stored or returned as
    plaintext; only worker/api_key_service.py::get_api_key() decrypts it, for
    server-side use only."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # psi|groq|gsc|ga4|openai|anthropic|gemini
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
