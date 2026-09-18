"""Bridges the Phase 0 DB schema and the existing `modules.crawler.crawl_site`
BFS engine: creating a crawl, adapting DB rows to the dataclass config
`crawl_site` actually takes, persisting each page/link/issue as the crawl
runs (via `crawl_site`'s `on_result` hook), and finalizing — running the
Phase 2 site-wide aggregation pass (`worker/site_audit.py`) then computing
the two summary scores — once it completes.

`create_crawl`/`get_or_create_default_project` are today's entry point for
starting a crawl (manual/test use) — a real `POST /projects/:id/crawls` API
route is a later phase, not part of Phase 1.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

from modules.crawler import CrawlConfig as ModuleCrawlConfig
from modules.crawler import normalize_url
from worker.db.models import Crawl, CrawlConfig, Issue, Link, Organization, Page, Project
from worker.db.session import SessionLocal
from worker.site_audit import run_site_audit

# Existing audit modules emit a 5-tier severity scale (Critical/High/Warning/
# Medium/Low — confirmed by grep across heading_auditor.py/image_auditor.py/
# link_auditor.py/etc.). The Phase 0 schema's Issue.severity column instead
# holds the newer Ahrefs-style 3-tier model (02-AUDIT-ENGINE.md §3). Map down
# rather than lose data — the original string is preserved in
# explanation_json["original_severity"], and IssueTypeConfig exists precisely
# so a project can override this default mapping later.
_SEVERITY_MAP = {
    "Critical": "error",
    "High": "error",
    "Warning": "warning",
    "Medium": "warning",
    "Low": "notice",
}


def _map_severity(original: str) -> str:
    return _SEVERITY_MAP.get(original, "notice")


def _compute_next_run_at(schedule_cron: str | None) -> datetime | None:
    """Shared by create_crawl() (first schedule) and
    set_crawl_config_schedule() (editing an existing one) so there's one
    place computing "when does this cron expression next fire"."""
    if not schedule_cron:
        return None
    return croniter(schedule_cron, datetime.now(timezone.utc)).get_next(datetime)


def get_or_create_default_project(db, root_url: str) -> Project:
    """Phase 0 designed users/organizations/memberships tables but never
    actually seeded any rows (no login flow yet — see worker/README.md). This
    get-or-creates a single local-dev org/project on demand so a Crawl has a
    project_id to attach to."""
    project = db.execute(select(Project).where(Project.root_url == root_url)).scalar_one_or_none()
    if project is not None:
        return project

    org = db.execute(select(Organization).where(Organization.name == "Local Dev")).scalar_one_or_none()
    if org is None:
        org = Organization(name="Local Dev", plan_tier="free")
        db.add(org)
        db.flush()

    project = Project(org_id=org.id, name=root_url, root_url=root_url)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def create_crawl(
    db,
    root_url: str,
    *,
    seed_source: str = "homepage",
    url_list: list | None = None,
    include_patterns: list | None = None,
    exclude_patterns: list | None = None,
    max_depth: int = 3,
    max_pages: int = 50,
    include_subdomains: bool = False,
    user_agent: str = "default",
    robots_mode: str = "respect",
    crawl_delay: float = 0.0,
    max_workers: int = 4,
    run_full_audit: bool = True,
    render_js: bool = False,
    render_timeout_ms: int = 5000,
    requests_per_second: float = 1.0,
    max_duration_minutes: int = 60,
    schedule_cron: str | None = None,
    org_id: int | None = None,
) -> Crawl:
    """Creates a CrawlConfig row (folding fields with no dedicated column —
    max_depth/include_subdomains/patterns/seed_source/url_list/crawl_delay/
    run_full_audit — into scope_json) and a queued Crawl row tied to it.

    `schedule_cron`, if given, attaches a Phase 3 recurring schedule to the
    new CrawlConfig and computes its first `next_run_at` — after this initial
    (manual) crawl, `worker/scheduler.py::enqueue_due_crawls()` takes over,
    reusing this same CrawlConfig for every subsequent scheduled run."""
    if org_id is not None:
        from worker.access import get_or_create_project
        project = get_or_create_project(db, org_id, root_url)
    else:
        project = get_or_create_default_project(db, root_url)

    scope_json = {
        "max_depth": max_depth,
        "include_subdomains": include_subdomains,
        "include_patterns": include_patterns or [],
        "exclude_patterns": exclude_patterns or [],
        "seed_source": seed_source,
        "url_list": url_list or [],
        "crawl_delay": crawl_delay,
        "run_full_audit": run_full_audit,
        "render_timeout_ms": render_timeout_ms,
    }
    crawl_config = CrawlConfig(
        project_id=project.id,
        source_type=seed_source,
        scope_json=scope_json,
        robots_mode=robots_mode,
        render_js=render_js,
        max_pages=max_pages,
        max_duration_minutes=max_duration_minutes,
        concurrency=max_workers,
        requests_per_second=requests_per_second,
        user_agent=user_agent,
        schedule_cron=schedule_cron,
    )
    crawl_config.next_run_at = _compute_next_run_at(schedule_cron)
    db.add(crawl_config)
    db.flush()

    crawl = Crawl(
        project_id=project.id,
        crawl_config_id=crawl_config.id,
        status="queued",
        pages_total_estimate=max_pages,
    )
    db.add(crawl)
    db.commit()
    db.refresh(crawl)
    return crawl


def set_crawl_config_schedule(db, crawl_config_id: int, schedule_cron: str | None) -> CrawlConfig:
    """Updates an *existing* CrawlConfig's schedule — unlike create_crawl(),
    which only sets one at creation time. Recurring runs
    (worker/scheduler.py::enqueue_due_crawls()) are tied to one persistent
    CrawlConfig, so editing a schedule updates that same row rather than
    creating a new one. Passing schedule_cron=None turns the schedule off:
    both columns go to NULL, and enqueue_due_crawls()'s own
    `WHERE schedule_cron IS NOT NULL` filter naturally stops picking it up
    — no separate "disabled" flag needed."""
    crawl_config = db.get(CrawlConfig, crawl_config_id)
    if crawl_config is None:
        raise ValueError(f"no such crawl_config_id={crawl_config_id!r}")

    crawl_config.schedule_cron = schedule_cron
    crawl_config.next_run_at = _compute_next_run_at(schedule_cron)
    db.commit()
    db.refresh(crawl_config)
    return crawl_config


def build_module_crawl_config(project: Project, crawl_config: CrawlConfig) -> ModuleCrawlConfig:
    """Adapts the DB rows into the dataclass `modules.crawler.crawl_site()`
    actually takes."""
    scope = crawl_config.scope_json or {}
    return ModuleCrawlConfig(
        seed_url=project.root_url,
        seed_source=scope.get("seed_source", "homepage"),
        url_list=scope.get("url_list", []),
        include_patterns=scope.get("include_patterns", []),
        exclude_patterns=scope.get("exclude_patterns", []),
        max_depth=scope.get("max_depth", 3),
        max_pages=crawl_config.max_pages,
        include_subdomains=scope.get("include_subdomains", False),
        user_agent=crawl_config.user_agent,
        robots_mode=crawl_config.robots_mode,
        crawl_delay=scope.get("crawl_delay", 0.0),
        max_workers=crawl_config.concurrency,
        run_full_audit=scope.get("run_full_audit", True),
        render_js=crawl_config.render_js,
        render_timeout_ms=scope.get("render_timeout_ms", 5000),
    )


def persist_result(crawl_id: int, url: str, outcome: dict) -> None:
    """The `on_result` callback body — one call per URL `crawl_site()`
    processes. Opens its own short session per call: `crawl_site`'s callback
    fires from the single calling thread (not from its internal
    ThreadPoolExecutor workers), so sequential short sessions are safe and
    keep each page durable as soon as it's produced, matching the "streaming
    persistence" goal — a crash mid-crawl only loses the in-flight page, not
    everything crawled so far.

    A `Page` row is written for every outcome, including robots-skips and
    fetch-errors (with seo_score left null), so gaps are visible rather than
    silently missing pages. Only a successful page gets `Link`/`Issue` rows.
    """
    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        if crawl is None:
            return

        if outcome.get("skipped") == "robots" or "error" in outcome:
            db.add(Page(crawl_id=crawl_id, url=url, normalized_url=url, status_code=None))
        else:
            page_data = outcome["page"]
            audit = page_data.get("audit") or {}
            metadata = audit.get("metadata") or {}
            headings = audit.get("headings") or {}
            h1_texts = headings.get("h1_texts") or []
            canonical = audit.get("canonical") or {}
            indexability = audit.get("indexability") or {}
            advanced = audit.get("advanced") or {}
            content = audit.get("content") or {}

            content_text = content.get("text")
            content_hash = hashlib.md5(content_text.encode("utf-8")).hexdigest() if content_text else None

            page = Page(
                crawl_id=crawl_id,
                url=page_data["url"],
                # Must be the canonicalized form: site_audit.py and
                # api/analyze.py match normalized Link.target_url against this,
                # so an un-normalized value here silently breaks broken-internal
                # -link and orphan/duplicate detection (audit finding).
                normalized_url=normalize_url(page_data["url"]),
                status_code=page_data.get("status_code"),
                redirect_chain_json=audit.get("redirect_chain") or [],
                content_hash=content_hash,
                title=metadata.get("title"),
                meta_description=metadata.get("description"),
                h1=h1_texts[0] if h1_texts else None,
                seo_score=audit.get("seo_score"),
                canonical_url=canonical.get("canonical_url"),
                is_indexable=indexability.get("is_indexable"),
                hreflang_json=advanced.get("hreflang_tags") or [],
                schema_types_json=advanced.get("schema_types") or [],
            )
            db.add(page)
            db.flush()  # need page.id for the Link/Issue rows below

            # crawl_site() now runs the per-page audit with check_links=True
            # (pure DOM parsing, no extra HTTP requests), so the full
            # per-link metadata modules/link_auditor.py already extracts —
            # anchor text, rel=nofollow, DOM location — is available here.
            # Internal target_url is normalized the same way Page.url is so
            # site_audit.py::_detect_broken_internal_links()'s string match
            # against crawled pages keeps working; external/special links
            # aren't matched against anything, so they're stored as resolved.
            for link in (audit.get("internal_links") or {}).get("links", []):
                db.add(
                    Link(
                        page_id=page.id,
                        target_url=normalize_url(link["url"]),
                        link_type="internal",
                        dom_location=link.get("location"),
                        anchor_text=link.get("anchor_text"),
                        is_nofollow=link.get("is_nofollow", False),
                        is_dofollow=link.get("is_dofollow", True),
                    )
                )
            for link in (audit.get("external_links") or {}).get("links", []):
                db.add(
                    Link(
                        page_id=page.id,
                        target_url=link["url"],
                        link_type="external",
                        dom_location=link.get("location"),
                        anchor_text=link.get("anchor_text"),
                        is_nofollow=link.get("is_nofollow", False),
                        is_dofollow=link.get("is_dofollow", True),
                    )
                )
            for kind, items in (audit.get("special_links") or {}).items():
                for item in items:
                    db.add(
                        Link(
                            page_id=page.id,
                            target_url=item["href"],
                            link_type=kind,
                            dom_location=item.get("location"),
                            anchor_text=item.get("anchor_text"),
                        )
                    )

            for issue in audit.get("all_issues", []):
                original_severity = issue.get("severity", "Low")
                db.add(
                    Issue(
                        crawl_id=crawl_id,
                        page_id=page.id,
                        issue_type=issue.get("issue", "unknown"),
                        severity=_map_severity(original_severity),
                        impact_score=issue.get("impact_score"),
                        effort_level=issue.get("effort"),
                        explanation_json={
                            "category": issue.get("category"),
                            "recommendation": issue.get("recommendation"),
                            "original_severity": original_severity,
                        },
                    )
                )

        crawl.pages_crawled = (crawl.pages_crawled or 0) + 1
        db.commit()


def finalize_crawl(crawl_id: int, status: str) -> None:
    """Runs the Phase 2 post-crawl aggregation pass (only on success — a
    failed crawl's partial data isn't a meaningful basis for sitewide
    duplicate/orphan/redirect findings), then computes the two summary
    scores (02-AUDIT-ENGINE.md §4) and closes out the Crawl row.

    run_site_audit() runs first, in its own committed session, so the
    sitewide "error"-severity issues it produces (broken internal links,
    redirect loops) are already in the Issue table by the time health_score
    is computed below — a page with a broken outbound link should count
    against that page's "clean" status just like a per-page audit error
    would. Health Score's denominator is pages that actually got audited
    (have a seo_score) — robots-skips/fetch-errors have no issue data to
    evaluate and are excluded, matching Ahrefs' "% of crawled pages" framing
    rather than "% of discovered URLs"."""
    if status == "completed":
        run_site_audit(crawl_id)

    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        if crawl is None:
            return

        audited_pages = (
            db.execute(select(Page).where(Page.crawl_id == crawl_id, Page.seo_score.isnot(None)))
            .scalars()
            .all()
        )
        if audited_pages:
            error_page_ids = set(
                db.execute(
                    select(Issue.page_id).where(Issue.crawl_id == crawl_id, Issue.severity == "error")
                )
                .scalars()
                .all()
            )
            clean_pages = sum(1 for p in audited_pages if p.id not in error_page_ids)
            crawl.health_score = round(100 * clean_pages / len(audited_pages), 2)
            crawl.seo_score_avg = round(sum(p.seo_score for p in audited_pages) / len(audited_pages), 2)

        crawl.status = status
        crawl.finished_at = datetime.now(timezone.utc)
        db.commit()
