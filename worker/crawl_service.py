"""Bridges the Phase 0 DB schema and the existing `modules.crawler.crawl_site`
BFS engine: creating a crawl, adapting DB rows to the dataclass config
`crawl_site` actually takes, persisting each page/link/issue as the crawl
runs (via `crawl_site`'s `on_result` hook), and finalizing - running the
Phase 2 site-wide aggregation pass (`worker/site_audit.py`) then computing
the two summary scores - once it completes.

`create_crawl`/`get_or_create_default_project` are today's entry point for
starting a crawl (manual/test use) - a real `POST /projects/:id/crawls` API
route is a later phase, not part of Phase 1.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select

logger = logging.getLogger(__name__)

from modules.crawler import CrawlConfig as ModuleCrawlConfig
from modules.crawler import normalize_url
from worker.db.models import Crawl, CrawlConfig, Issue, Link, Membership, Organization, Page, Project, User
from worker.db.session import SessionLocal
from worker.site_audit import run_site_audit

# Existing audit modules emit a 5-tier severity scale (Critical/High/Warning/
# Medium/Low - confirmed by grep across heading_auditor.py/image_auditor.py/
# link_auditor.py/etc.). The Phase 0 schema's Issue.severity column instead
# holds the newer Ahrefs-style 3-tier model (02-AUDIT-ENGINE.md §3). Map down
# rather than lose data - the original string is preserved in
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
    actually seeded any rows (no login flow yet - see worker/README.md). This
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
    """Creates a CrawlConfig row (folding fields with no dedicated column -
    max_depth/include_subdomains/patterns/seed_source/url_list/crawl_delay/
    run_full_audit - into scope_json) and a queued Crawl row tied to it.

    `schedule_cron`, if given, attaches a Phase 3 recurring schedule to the
    new CrawlConfig and computes its first `next_run_at` - after this initial
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
    """Updates an *existing* CrawlConfig's schedule - unlike create_crawl(),
    which only sets one at creation time. Recurring runs
    (worker/scheduler.py::enqueue_due_crawls()) are tied to one persistent
    CrawlConfig, so editing a schedule updates that same row rather than
    creating a new one. Passing schedule_cron=None turns the schedule off:
    both columns go to NULL, and enqueue_due_crawls()'s own
    `WHERE schedule_cron IS NOT NULL` filter naturally stops picking it up
    - no separate "disabled" flag needed."""
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
    """The `on_result` callback body - one call per URL `crawl_site()`
    processes. Opens its own short session per call: `crawl_site`'s callback
    fires from the single calling thread (not from its internal
    ThreadPoolExecutor workers), so sequential short sessions are safe and
    keep each page durable as soon as it's produced, matching the "streaming
    persistence" goal - a crash mid-crawl only loses the in-flight page, not
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

            # Compact MinHash signature for fuzzy near-duplicate detection at
            # scale (M3 Tier B) - stored instead of the full text. Best-effort.
            content_signature = None
            if content_text:
                try:
                    from modules.near_duplicate import content_signature as _sig
                    sig = _sig(content_text)
                    if sig.shingle_count > 0:
                        content_signature = {"minhash": list(sig.minhash),
                                             "shingle_count": sig.shingle_count}
                except Exception:  # noqa: BLE001
                    content_signature = None

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
                content_signature_json=content_signature,
            )
            db.add(page)
            db.flush()  # need page.id for the Link/Issue rows below

            # crawl_site() now runs the per-page audit with check_links=True
            # (pure DOM parsing, no extra HTTP requests), so the full
            # per-link metadata modules/link_auditor.py already extracts -
            # anchor text, rel=nofollow, DOM location - is available here.
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


def load_resume_state(crawl_id: int) -> tuple[list[str], list[str]]:
    """Reconstruct a crawl's resume state from what's already persisted (M2 T2.3).

    Because every crawled page is streamed to the DB as it completes, an
    interrupted crawl can continue without re-crawling: the already-crawled pages
    ARE the visited set, and the internal links discovered from them (minus the
    visited ones) ARE the pending frontier. Returns (visited_urls, frontier_urls),
    both normalized. An empty visited set means "nothing crawled yet - start
    fresh".
    """
    with SessionLocal() as db:
        visited = list(
            db.execute(select(Page.normalized_url).where(Page.crawl_id == crawl_id))
            .scalars()
            .all()
        )
        visited_set = set(visited)
        discovered = (
            db.execute(
                select(Link.target_url)
                .join(Page, Link.page_id == Page.id)
                .where(Page.crawl_id == crawl_id, Link.link_type == "internal")
            )
            .scalars()
            .all()
        )
    # De-dup discovered internal targets and drop anything already crawled.
    frontier = [u for u in dict.fromkeys(discovered) if u not in visited_set]
    return visited, frontier


def _persist_link_scores(db, crawl_id: int) -> None:
    """Compute internal-PageRank Link Score (+ in/out link counts) over the whole
    crawl graph and write it onto each Page (M3 Tier B). Also computes each page's
    shortest click-depth from the homepage (M3 Tier C #7) from the same link graph
    and writes Page.depth (0 = homepage, None = unreachable/orphan). Runs inside
    the caller's session so it commits with the rest of finalize_crawl.
    Best-effort: any error is swallowed so it never blocks finalization."""
    try:
        from modules.crawl_graph import bfs_depths, build_link_graph
        from modules.link_score import build_link_score_report
        from modules.sitewide import SiteLink

        pages = db.execute(select(Page).where(Page.crawl_id == crawl_id)).scalars().all()
        if not pages:
            return
        url_by_id = {p.id: p.normalized_url for p in pages}
        page_urls = set(url_by_id.values())
        link_rows = db.execute(
            select(Link.page_id, Link.target_url, Link.link_type)
            .join(Page, Link.page_id == Page.id)
            .where(Page.crawl_id == crawl_id)
        ).all()
        site_links = [
            SiteLink(source_url=url_by_id[pid], target_url=t, link_type=lt)
            for (pid, t, lt) in link_rows if pid in url_by_id
        ]
        crawl = db.get(Crawl, crawl_id)
        project = db.get(Project, crawl.project_id) if crawl else None
        root = normalize_url(project.root_url) if project and project.root_url else None
        report = build_link_score_report(page_urls, site_links, root=root)

        # Per-page click-depth from the homepage (M3 Tier C #7): BFS over the
        # internal link graph (edges kept only to crawled pages), root depth 0.
        depths: dict[str, int] = {}
        if root is not None:
            graph = build_link_graph(site_links, page_urls | {root})
            depths = bfs_depths(root, graph)

        for p in pages:
            p.link_score = report.scores.get(p.normalized_url)
            p.inlinks = report.inlinks.get(p.normalized_url)
            p.outlinks = report.outlinks.get(p.normalized_url)
            p.depth = depths.get(p.normalized_url)
    except Exception:  # noqa: BLE001
        logger.exception("link-score persistence failed for crawl %s", crawl_id)


def _persist_theme_scores(db, crawl_id: int) -> None:
    """Compute a per-theme 0-100 score for the crawl from its Issue rows and store
    {theme: score} on Crawl.theme_scores_json (M3 Tier B #5). Groups issues into
    themes with the same THEMES/keyword matching scoring.py uses, then scores each
    theme as max(0, 100 - sum(PENALTY[severity])). The original (5-tier) severity
    string preserved in explanation_json drives PENALTY - the mapped 3-tier
    column would collapse Critical/High and lose that granularity. Runs inside
    the caller's session; best-effort so it never blocks finalization."""
    try:
        from modules.scoring import PENALTY, get_thematic_issues

        issue_rows = db.execute(select(Issue).where(Issue.crawl_id == crawl_id)).scalars().all()
        issues = [
            {
                "category": (i.explanation_json or {}).get("category", "Other"),
                "severity": (i.explanation_json or {}).get("original_severity", "Low"),
            }
            for i in issue_rows
        ]
        grouped = get_thematic_issues(issues)
        theme_scores = {
            theme: max(0, 100 - sum(PENALTY.get(iss.get("severity", "Low"), 2) for iss in members))
            for theme, members in grouped.items()
        }
        crawl = db.get(Crawl, crawl_id)
        if crawl is not None:
            crawl.theme_scores_json = theme_scores
    except Exception:  # noqa: BLE001
        logger.exception("theme-score persistence failed for crawl %s", crawl_id)


def _resolve_org_owner_email(db, org_id: int) -> str | None:
    """The email to send an org's regression alert to: the org's admin member,
    falling back to any member. None if the org has no members with an email
    (e.g. the seeded local-dev org, which has no users/memberships)."""
    admin = db.execute(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id, Membership.role == "admin")
        .order_by(User.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if admin:
        return admin
    return db.execute(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id)
        .order_by(User.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def send_regression_alert(crawl_id: int) -> None:
    """M5 T5.4 - email the org owner when a just-finalized crawl regressed vs.
    the previous completed crawl of the same project. Best-effort: never raises
    (email must never break crawl finalization).

    A regression is "at least one new issue appeared, OR the health score
    dropped". If nothing regressed - no new issues and health_score_delta is
    None or >= 0 - no email is sent. Also silently no-ops when email isn't
    configured (`email_enabled()` False) or there's no previous crawl to diff
    against. email_send/crawl_diff are imported lazily so importing this module
    stays cheap and side-effect-free.
    """
    try:
        from worker.crawl_diff import compare_crawls, get_previous_completed_crawl
        from worker.email_send import email_enabled, regression_alert_html, send_email

        if not email_enabled():
            return

        prev = get_previous_completed_crawl(crawl_id)
        if prev is None:
            return  # nothing to compare against (first completed crawl)

        diff = compare_crawls(prev.id, crawl_id)
        new_issues = diff.get("new_issues") or []
        health_delta = diff.get("health_score_delta")
        if not new_issues and (health_delta is None or health_delta >= 0):
            return  # nothing got worse

        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                return
            project = db.get(Project, crawl.project_id)
            if project is None:
                return
            root_url = project.root_url
            owner_email = _resolve_org_owner_email(db, project.org_id)

        if not owner_email:
            logger.info("regression alert for crawl %s skipped: no org owner email", crawl_id)
            return

        subject = f"SEO regression detected: {root_url}"
        result = send_email(owner_email, subject, regression_alert_html(root_url, diff))
        if result.get("ok"):
            logger.info("regression alert for crawl %s sent to %s", crawl_id, owner_email)
        else:
            logger.warning("regression alert for crawl %s not sent: %s", crawl_id, result.get("error"))
    except Exception:  # noqa: BLE001 - alerting is best-effort, never fatal
        logger.exception("send_regression_alert failed for crawl %s", crawl_id)


def finalize_crawl(crawl_id: int, status: str) -> None:
    """Runs the Phase 2 post-crawl aggregation pass (only on success - a
    failed crawl's partial data isn't a meaningful basis for sitewide
    duplicate/orphan/redirect findings), then computes the two summary
    scores (02-AUDIT-ENGINE.md §4) and closes out the Crawl row.

    run_site_audit() runs first, in its own committed session, so the
    sitewide "error"-severity issues it produces (broken internal links,
    redirect loops) are already in the Issue table by the time health_score
    is computed below - a page with a broken outbound link should count
    against that page's "clean" status just like a per-page audit error
    would. Health Score's denominator is pages that actually got audited
    (have a seo_score) - robots-skips/fetch-errors have no issue data to
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

        # Link Score needs the whole graph, so it's computed here (crawl done),
        # not per page. Only for completed crawls (a partial graph misleads).
        if status == "completed":
            _persist_link_scores(db, crawl_id)
            # Per-theme crawl scores for the theme-score trend (M3 Tier B #5).
            _persist_theme_scores(db, crawl_id)

        crawl.status = status
        crawl.finished_at = datetime.now(timezone.utc)
        db.commit()

    # Regression-alert hook (M5 T5.4): only for successful crawls, and wrapped
    # so any failure is swallowed - email must never break finalization. This
    # covers BOTH the browser finalize path (worker/tasks.py) and the cron
    # executor (worker/cron_runner.py), since both funnel through here.
    if status == "completed":
        try:
            send_regression_alert(crawl_id)
        except Exception:  # noqa: BLE001
            logger.exception("regression alert hook failed for crawl %s", crawl_id)
