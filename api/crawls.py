"""Frontend API for the crawler platform: start a crawl, watch it run, see
its results, browse its pages/issues, compare it against a prior crawl, and
schedule it to repeat. Same one-file/action-dispatch convention as
api/audit-pipeline.py - POST {"action": ..., ...} rather than REST
verbs/path params.

Deferred (not this file's job yet): the site-structure graph. An
incremental addition to this same dispatch table, same as "links" was.

Talks straight to worker/*.py - same DB file the worker process reads/
writes (worker/db/session.py resolves an absolute path), no separate
network layer, exactly like this repo's existing api/*.py already import
modules/*.py directly.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import bulk_url_cap, read_json_body, require_str, send_json  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from worker.crawl_diff import compare_crawls, get_previous_completed_crawl, get_score_trend  # noqa: E402
from worker.crawl_export import build_crawl_results, crawl_meta, get_crawl_by_share_token, new_share_token  # noqa: E402
from modules.report_generator import generate_csv, generate_excel, generate_pdf  # noqa: E402
import json  # noqa: E402

# Report export MIME types, shared by the authenticated `export` action and the
# public `shareExport` action (M5 T5.2 - both consolidated into this function to
# stay under the Hobby plan's 12-serverless-function cap).
_EXPORT_MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "json": "application/json",
}
_INVALID_TOKEN = "This shared report link is invalid or has been revoked."
from worker.crawl_service import create_crawl, finalize_crawl, persist_result, set_crawl_config_schedule  # noqa: E402
from worker.db.models import Crawl, CrawlConfig, Issue, Link, Page, Project  # noqa: E402
from worker.db.session import SessionLocal  # noqa: E402
from worker.access import crawl_for_org, resolve_org_id  # noqa: E402
from worker.auth import AuthError  # noqa: E402
from worker.queue import enqueue  # noqa: E402

# Actions that operate on a specific crawl id - gated by per-org ownership.
_CRAWL_SCOPED = {"status", "thematic", "trend", "compare", "setSchedule", "pages", "issues", "links", "ingest", "finalize", "pause", "resume", "setShare", "revokeShare", "export"}
# Actions that resolve an org (list/create derive scope from the session too).
_ORG_SCOPED = {"list", "create"} | _CRAWL_SCOPED
from worker.site_audit import get_thematic_report  # noqa: E402

# Pagination bounds shared by the "pages" and "issues" listing actions.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200

logger = logging.getLogger(__name__)


def _crawl_summary(crawl: Crawl, root_url: str | None = None, crawl_config: "CrawlConfig | None" = None) -> dict:
    return {
        "id": crawl.id,
        "rootUrl": root_url,
        "status": crawl.status,
        "healthScore": crawl.health_score,
        "seoScoreAvg": crawl.seo_score_avg,
        "pagesCrawled": crawl.pages_crawled,
        "pagesTotalEstimate": crawl.pages_total_estimate,
        "startedAt": crawl.started_at.isoformat() if crawl.started_at else None,
        "finishedAt": crawl.finished_at.isoformat() if crawl.finished_at else None,
        "scheduleCron": crawl_config.schedule_cron if crawl_config else None,
        "nextRunAt": crawl_config.next_run_at.isoformat() if crawl_config and crawl_config.next_run_at else None,
    }


def _parse_crawl_id(handler, payload) -> int | None:
    try:
        return int(payload.get("crawlId"))
    except (TypeError, ValueError):
        send_json(handler, 400, {"error": "crawlId is required and must be an integer"})
        return None


def _handle_list(handler, payload):
    try:
        org_id = getattr(handler, "_org_id", None)
        with SessionLocal() as db:
            query = (
                select(Crawl, Project.root_url)
                .join(Project, Crawl.project_id == Project.id)
                .order_by(Crawl.id.desc())
                .limit(50)
            )
            if org_id is not None:
                query = query.where(Project.org_id == org_id)
            rows = db.execute(query).all()
            crawls = [_crawl_summary(crawl, root_url) for crawl, root_url in rows]
        send_json(handler, 200, {"crawls": crawls})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (list) request failed")
        send_json(handler, 500, {"error": "Internal error while listing crawls."})


def _handle_create(handler, payload):
    try:
        root_url = require_str(handler, payload, "rootUrl")
        if root_url is None:
            return

        # Clamp to [1, cap]: a zero/negative maxPages previously stored fine and
        # only blew up later in the worker's CrawlConfig (deferred failure).
        max_pages = max(1, min(int(payload.get("maxPages", 50) or 50), bulk_url_cap()))
        max_depth = max(0, int(payload.get("maxDepth", 3) or 3))
        robots_mode = payload.get("robotsMode") or "respect"
        render_js = bool(payload.get("renderJs", False))
        schedule_cron = (payload.get("scheduleCron") or "").strip() or None

        org_id = getattr(handler, "_org_id", None)
        with SessionLocal() as db:
            crawl = create_crawl(
                db, root_url, max_pages=max_pages, max_depth=max_depth,
                robots_mode=robots_mode, render_js=render_js, schedule_cron=schedule_cron,
                org_id=org_id,
            )
            crawl_id = crawl.id

        enqueue("crawl.start", {"crawl_id": crawl_id})
        send_json(handler, 200, {"crawlId": crawl_id})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (create) request failed")
        send_json(handler, 500, {"error": "Internal error while starting the crawl."})


def _handle_ingest(handler, payload):
    """Persist one audited page into a crawl (Vercel-only: the browser drives
    the crawl and streams each page here, replacing the always-on worker).
    Body: {crawlId, url, outcome} where `outcome` is the per-URL result shape
    worker/crawl_service.persist_result() expects: {"page": {"url", "status_code",
    "audit": <api/audit-pipeline "audit" result>}}."""
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    url = require_str(handler, payload, "url")
    if url is None:
        return
    outcome = payload.get("outcome")
    if not isinstance(outcome, dict):
        send_json(handler, 400, {"error": "outcome (object) is required"})
        return
    # A success outcome must carry page.url (persist_result indexes it directly);
    # skip/error outcomes are fine as-is. Reject a malformed body with 400, not 500.
    is_skip_or_error = outcome.get("skipped") == "robots" or "error" in outcome
    if not is_skip_or_error:
        page = outcome.get("page")
        if not isinstance(page, dict) or not page.get("url"):
            send_json(handler, 400, {"error": "outcome.page.url is required for a successful result"})
            return
    try:
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": "Crawl not found."})
                return
            if crawl.status == "queued":
                crawl.status = "running"
                if crawl.started_at is None:
                    crawl.started_at = datetime.now(timezone.utc)
                db.commit()
        persist_result(crawl_id, url, outcome)
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            send_json(handler, 200, {"ok": True, "pagesCrawled": crawl.pages_crawled if crawl else None})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py ingest failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while saving the page."})


def _handle_finalize(handler, payload):
    """Close out a browser-driven crawl: runs the site-wide aggregation pass and
    computes the health/SEO scores (worker/crawl_service.finalize_crawl)."""
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    status = (payload.get("status") or "completed").strip()
    if status not in ("completed", "failed"):
        status = "completed"
    try:
        finalize_crawl(crawl_id, status)
        send_json(handler, 200, {"ok": True, "status": status})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py finalize failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while finalizing the crawl."})


def _handle_status(handler, payload):
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": f"No crawl with id {crawl_id}"})
                return
            project = db.get(Project, crawl.project_id)
            crawl_config = db.get(CrawlConfig, crawl.crawl_config_id) if crawl.crawl_config_id else None
            send_json(handler, 200, _crawl_summary(crawl, project.root_url if project else None, crawl_config))
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (status) request failed")
        send_json(handler, 500, {"error": "Internal error while fetching crawl status."})


def _handle_thematic(handler, payload):
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        send_json(handler, 200, {"themes": get_thematic_report(crawl_id)})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (thematic) request failed")
        send_json(handler, 500, {"error": "Internal error while building the thematic report."})


def _handle_trend(handler, payload):
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": f"No crawl with id {crawl_id}"})
                return
            project_id = crawl.project_id
        send_json(handler, 200, {"trend": get_score_trend(project_id)})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (trend) request failed")
        send_json(handler, 500, {"error": "Internal error while building the score trend."})


def _handle_compare(handler, payload):
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return

        compare_to_id = payload.get("compareToId")
        try:
            compare_to_id = int(compare_to_id) if compare_to_id is not None else None
        except (TypeError, ValueError):
            send_json(handler, 400, {"error": "compareToId must be an integer"})
            return

        if compare_to_id is None:
            previous = get_previous_completed_crawl(crawl_id)
            if previous is None:
                # A project's first crawl (or one with no earlier completed
                # run) has nothing to diff against - a normal state, not an
                # error.
                send_json(handler, 200, {"available": False})
                return
            compare_to_id = previous.id

        # Ownership: the compare-against crawl is also client-supplied, so it
        # must belong to the same org (the auto-selected previous crawl already
        # does - same project - but an explicit compareToId must be verified, or
        # a tenant could diff against any other org's crawl and read its data).
        org_id = getattr(handler, "_org_id", None)
        if org_id is not None:
            with SessionLocal() as db:
                if crawl_for_org(db, compare_to_id, org_id) is None:
                    send_json(handler, 404, {"error": "Crawl not found."})
                    return

        diff = compare_crawls(compare_to_id, crawl_id)
        send_json(handler, 200, {
            "available": True,
            "compareToId": compare_to_id,
            "diff": {
                "newIssues": diff["new_issues"],
                "fixedIssues": diff["fixed_issues"],
                "regressedPages": diff["regressed_pages"],
                "improvedPages": diff["improved_pages"],
                "healthScoreDelta": diff["health_score_delta"],
                "seoScoreAvgDelta": diff["seo_score_avg_delta"],
            },
        })
    except ValueError as e:
        # compare_crawls() raises ValueError if either crawl_id doesn't exist
        # (e.g. a stale compareToId from the client) - a 400, not a 500.
        send_json(handler, 400, {"error": str(e)})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (compare) request failed")
        send_json(handler, 500, {"error": "Internal error while comparing crawls."})


def _handle_set_schedule(handler, payload):
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        # "" and null both mean "turn the schedule off" - the client sends
        # either depending on how the picker's empty state is represented.
        schedule_cron = (payload.get("scheduleCron") or "").strip() or None

        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": f"No crawl with id {crawl_id}"})
                return
            if crawl.crawl_config_id is None:
                send_json(handler, 400, {"error": "This crawl has no associated config to schedule."})
                return
            crawl_config = set_crawl_config_schedule(db, crawl.crawl_config_id, schedule_cron)
            send_json(handler, 200, {
                "scheduleCron": crawl_config.schedule_cron,
                "nextRunAt": crawl_config.next_run_at.isoformat() if crawl_config.next_run_at else None,
            })
    except ValueError as e:
        send_json(handler, 400, {"error": str(e)})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (setSchedule) request failed")
        send_json(handler, 500, {"error": "Internal error while updating the schedule."})


def _parse_pagination(payload) -> tuple[int, int]:
    page_num = max(1, int(payload.get("page", 1) or 1))
    page_size = min(max(1, int(payload.get("pageSize", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    return page_num, page_size


def _handle_pages(handler, payload):
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        page_num, page_size = _parse_pagination(payload)
        search = (payload.get("search") or "").strip()
        sort = (payload.get("sort") or "").strip()

        with SessionLocal() as db:
            filters = [Page.crawl_id == crawl_id]
            if search:
                filters.append(Page.url.ilike(f"%{search}%"))

            # Sortable columns (M3 Tier B: Link Score; M3 Tier C #7: Depth).
            # Default is discovery order.
            order = {
                "linkScore": Page.link_score.desc(),
                "linkScoreAsc": Page.link_score.asc(),
                "seoScore": Page.seo_score.desc(),
                "depth": Page.depth.asc(),  # shallowest first
            }.get(sort, Page.id.asc())

            total = db.execute(select(func.count()).select_from(Page).where(*filters)).scalar_one()
            rows = db.execute(
                select(Page)
                .where(*filters)
                .order_by(order, Page.id.asc())
                .offset((page_num - 1) * page_size)
                .limit(page_size)
            ).scalars().all()

            # One aggregate query for this page's severity counts, not N+1
            # per-row queries.
            page_ids = [p.id for p in rows]
            counts_by_page: dict[int, dict[str, int]] = {}
            if page_ids:
                severity_rows = db.execute(
                    select(Issue.page_id, Issue.severity, func.count())
                    .where(Issue.page_id.in_(page_ids))
                    .group_by(Issue.page_id, Issue.severity)
                ).all()
                for pid, severity, count in severity_rows:
                    counts_by_page.setdefault(pid, {})[severity] = count

            pages_out = [
                {
                    "id": p.id,
                    "url": p.url,
                    "statusCode": p.status_code,
                    "title": p.title,
                    "metaDescription": p.meta_description,
                    "canonicalUrl": p.canonical_url,
                    "h1": p.h1,
                    "seoScore": p.seo_score,
                    "linkScore": p.link_score,
                    "inlinks": p.inlinks,
                    "outlinks": p.outlinks,
                    "depth": p.depth,
                    "fetchedAt": p.fetched_at.isoformat() if p.fetched_at else None,
                    "issueCounts": counts_by_page.get(p.id, {}),
                }
                for p in rows
            ]

        send_json(handler, 200, {"pages": pages_out, "total": total, "page": page_num, "pageSize": page_size})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (pages) request failed")
        send_json(handler, 500, {"error": "Internal error while listing pages."})


def _handle_issues(handler, payload):
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        page_num, page_size = _parse_pagination(payload)
        severity = (payload.get("severity") or "").strip() or None
        category = (payload.get("category") or "").strip() or None
        search = (payload.get("search") or "").strip()
        page_id = payload.get("pageId")

        with SessionLocal() as db:
            filters = [Issue.crawl_id == crawl_id]
            if severity:
                filters.append(Issue.severity == severity)
            if search:
                filters.append(Issue.issue_type.ilike(f"%{search}%"))
            if page_id is not None:
                filters.append(Issue.page_id == int(page_id))

            # category lives inside explanation_json, not a column - SQL-level
            # JSON-path filtering would be fragile/dialect-specific at this
            # scale, so severity/search are filtered in SQL and category is
            # filtered in Python below (matches worker/site_audit.py's own
            # precedent for local-scale data).
            rows = db.execute(
                select(Issue, Page.url)
                .outerjoin(Page, Issue.page_id == Page.id)
                .where(*filters)
                .order_by(Issue.id.asc())
            ).all()

            all_categories = sorted({(issue.explanation_json or {}).get("category", "Other") for issue, _ in rows})
            if category:
                rows = [(issue, url) for issue, url in rows if (issue.explanation_json or {}).get("category", "Other") == category]

            total = len(rows)
            start = (page_num - 1) * page_size
            page_rows = rows[start:start + page_size]

            issues_out = [
                {
                    "id": issue.id,
                    "issueType": issue.issue_type,
                    "severity": issue.severity,
                    "category": (issue.explanation_json or {}).get("category", "Other"),
                    "recommendation": (issue.explanation_json or {}).get("recommendation", ""),
                    "impactScore": issue.impact_score,
                    "effortLevel": issue.effort_level,
                    "pageUrl": url,
                    "createdAt": issue.created_at.isoformat() if issue.created_at else None,
                }
                for issue, url in page_rows
            ]

        send_json(handler, 200, {
            "issues": issues_out,
            "total": total,
            "page": page_num,
            "pageSize": page_size,
            "categories": all_categories,
        })
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (issues) request failed")
        send_json(handler, 500, {"error": "Internal error while listing issues."})


def _handle_links(handler, payload):
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        page_num, page_size = _parse_pagination(payload)
        link_type = (payload.get("linkType") or "").strip() or None
        broken_only = bool(payload.get("brokenOnly"))
        search = (payload.get("search") or "").strip()

        with SessionLocal() as db:
            filters = [Page.crawl_id == crawl_id]
            if link_type:
                filters.append(Link.link_type == link_type)
            if broken_only:
                # Only internal links are ever checked for brokenness (see
                # site_audit.py::_detect_broken_internal_links) - external/
                # special links leave is_broken NULL, so this filter never
                # needs an explicit link_type == "internal" alongside it.
                filters.append(Link.is_broken.is_(True))
            if search:
                filters.append(Link.target_url.ilike(f"%{search}%") | Link.anchor_text.ilike(f"%{search}%"))

            total = (
                db.execute(select(func.count()).select_from(Link).join(Page, Link.page_id == Page.id).where(*filters))
                .scalar_one()
            )
            rows = db.execute(
                select(Link, Page.url)
                .join(Page, Link.page_id == Page.id)
                .where(*filters)
                .order_by(Link.id.asc())
                .offset((page_num - 1) * page_size)
                .limit(page_size)
            ).all()

            link_type_counts = dict(
                db.execute(
                    select(Link.link_type, func.count())
                    .join(Page, Link.page_id == Page.id)
                    .where(Page.crawl_id == crawl_id)
                    .group_by(Link.link_type)
                ).all()
            )

            links_out = [
                {
                    "id": link.id,
                    "targetUrl": link.target_url,
                    "linkType": link.link_type,
                    "domLocation": link.dom_location,
                    "anchorText": link.anchor_text,
                    "isNofollow": link.is_nofollow,
                    "isDofollow": link.is_dofollow,
                    "statusCode": link.status_code,
                    "isBroken": link.is_broken,
                    "pageUrl": page_url,
                }
                for link, page_url in rows
            ]

        send_json(handler, 200, {
            "links": links_out,
            "total": total,
            "page": page_num,
            "pageSize": page_size,
            "linkTypeCounts": link_type_counts,
        })
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (links) request failed")
        send_json(handler, 500, {"error": "Internal error while listing links."})


def _handle_pause(handler, payload):
    """Request a running (or queued) crawl to stop cleanly and stay resumable
    (M2 T2.3). The worker polls the crawl's status between depth batches and
    stops when it sees `paused`; already-crawled pages are kept."""
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": f"No crawl with id {crawl_id}"})
                return
            if crawl.status not in ("queued", "running"):
                send_json(handler, 400, {"error": f"Crawl is {crawl.status}, cannot pause."})
                return
            crawl.status = "paused"
            db.commit()
            send_json(handler, 200, {"crawlId": crawl_id, "status": "paused"})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (pause) request failed")
        send_json(handler, 500, {"error": "Internal error while pausing the crawl."})


def _handle_resume(handler, payload):
    """Resume a paused/failed crawl: re-queue it and enqueue crawl.start. The
    worker's load_resume_state continues from the already-persisted pages, so it
    does not re-crawl. Requires the always-on worker to be running (M2 T2.1)."""
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": f"No crawl with id {crawl_id}"})
                return
            if crawl.status not in ("paused", "failed"):
                send_json(handler, 400, {"error": f"Crawl is {crawl.status}, cannot resume."})
                return
            crawl.status = "queued"
            db.commit()
        enqueue("crawl.start", {"crawl_id": crawl_id})
        send_json(handler, 200, {"crawlId": crawl_id, "status": "queued"})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (resume) request failed")
        send_json(handler, 500, {"error": "Internal error while resuming the crawl."})


def _handle_set_share(handler, payload):
    """Mint (or return the existing) public share token for a crawl (M5 T5.2).
    Crawl-scoped: the do_POST org gate has already verified the caller's org
    owns this crawl. Idempotent - re-sharing an already-shared crawl returns
    the same token rather than rotating it."""
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": f"No crawl with id {crawl_id}"})
                return
            if crawl.share_token is None:
                crawl.share_token = new_share_token()
                db.commit()
            send_json(handler, 200, {"shareToken": crawl.share_token})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (setShare) request failed")
        send_json(handler, 500, {"error": "Internal error while creating the share link."})


def _handle_revoke_share(handler, payload):
    """Revoke a crawl's public share token (M5 T5.2). Crawl-scoped: ownership
    already verified by the do_POST org gate. Idempotent - revoking an
    unshared crawl is a no-op that still returns ok."""
    try:
        crawl_id = _parse_crawl_id(handler, payload)
        if crawl_id is None:
            return
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": f"No crawl with id {crawl_id}"})
                return
            crawl.share_token = None
            db.commit()
            send_json(handler, 200, {"ok": True})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (revokeShare) request failed")
        send_json(handler, 500, {"error": "Internal error while revoking the share link."})


def _write_report(handler, crawl_ref, fmt, results):
    """Serialize `results` in the requested format and stream it as a file
    download. Shared by the authenticated `export` and public `shareExport`
    actions. Assumes fmt is already validated against _EXPORT_MIME."""
    if fmt == "csv":
        data = generate_csv(results)
    elif fmt == "xlsx":
        data = generate_excel(results)
    elif fmt == "json":
        data = json.dumps(results, default=str, indent=2).encode("utf-8")
    else:
        data = generate_pdf(results)
    handler.send_response(200)
    handler.send_header("Content-Type", _EXPORT_MIME[fmt])
    handler.send_header("Content-Disposition", f'attachment; filename="crawl-{crawl_ref}-report.{fmt}"')
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _handle_export(handler, payload):
    """M5 T5.2: export a persisted crawl (its Page/Issue/Link rows) as CSV/
    Excel/PDF/JSON, reusing modules/report_generator via worker/crawl_export.
    Crawl-scoped: the do_POST org gate already verified ownership."""
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    fmt = (payload.get("format") or "csv").strip().lower()
    if fmt not in _EXPORT_MIME:
        send_json(handler, 400, {"error": "format must be csv, xlsx, pdf, or json"})
        return
    try:
        with SessionLocal() as db:
            results = build_crawl_results(db, crawl_id)
            if not results:
                send_json(handler, 400, {"error": "This crawl has no pages to export yet."})
                return
            meta = crawl_meta(db, crawl_id)
        _write_report(handler, meta.get("id") or crawl_id, fmt, results)
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (export) request failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while generating the export."})


# --------------------------------------------------------------------------- #
# Public shared-report actions (M5 T5.2). NO auth, NO crawlId: the long,
# unguessable share token (minted by setShare) IS the capability. These are
# deliberately NOT in _ORG_SCOPED so the do_POST org gate never runs for them;
# each resolves exactly one crawl by token (bypassing org scope) or 404s.
# --------------------------------------------------------------------------- #
def _resolve_share_crawl(handler, db, payload):
    token = (payload.get("token") or "").strip()
    if not token:
        send_json(handler, 400, {"error": "token is required"})
        return None
    crawl = get_crawl_by_share_token(db, token)
    if crawl is None:
        send_json(handler, 404, {"error": _INVALID_TOKEN})
        return None
    return crawl


def _handle_share_summary(handler, payload):
    try:
        with SessionLocal() as db:
            crawl = _resolve_share_crawl(handler, db, payload)
            if crawl is None:
                return
            pages_count = db.execute(
                select(func.count()).select_from(Page).where(Page.crawl_id == crawl.id)
            ).scalar_one()
            severity_rows = db.execute(
                select(Issue.severity, func.count()).where(Issue.crawl_id == crawl.id).group_by(Issue.severity)
            ).all()
            severity_counts = {severity: count for severity, count in severity_rows}
            send_json(handler, 200, {
                "meta": crawl_meta(db, crawl.id),
                "pagesCount": pages_count,
                "issueSeverityCounts": severity_counts,
                "issuesCount": sum(severity_counts.values()),
            })
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (shareSummary) request failed")
        send_json(handler, 500, {"error": "Internal error while loading the shared report."})


def _handle_share_pages(handler, payload):
    try:
        with SessionLocal() as db:
            crawl = _resolve_share_crawl(handler, db, payload)
            if crawl is None:
                return
            page_num, page_size = _parse_pagination(payload)
            search = (payload.get("search") or "").strip()
            filters = [Page.crawl_id == crawl.id]
            if search:
                filters.append(Page.url.ilike(f"%{search}%"))
            total = db.execute(select(func.count()).select_from(Page).where(*filters)).scalar_one()
            rows = db.execute(
                select(Page).where(*filters).order_by(Page.id.asc())
                .offset((page_num - 1) * page_size).limit(page_size)
            ).scalars().all()
            page_ids = [p.id for p in rows]
            counts_by_page: dict[int, dict[str, int]] = {}
            if page_ids:
                for pid, severity, count in db.execute(
                    select(Issue.page_id, Issue.severity, func.count())
                    .where(Issue.page_id.in_(page_ids)).group_by(Issue.page_id, Issue.severity)
                ).all():
                    counts_by_page.setdefault(pid, {})[severity] = count
            pages_out = [
                {
                    "id": p.id, "url": p.url, "statusCode": p.status_code, "title": p.title,
                    "metaDescription": p.meta_description, "canonicalUrl": p.canonical_url,
                    "h1": p.h1, "seoScore": p.seo_score, "depth": p.depth,
                    "issueCounts": counts_by_page.get(p.id, {}),
                }
                for p in rows
            ]
            send_json(handler, 200, {"pages": pages_out, "total": total, "page": page_num, "pageSize": page_size})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (sharePages) request failed")
        send_json(handler, 500, {"error": "Internal error while loading the shared report."})


def _handle_share_issues(handler, payload):
    try:
        with SessionLocal() as db:
            crawl = _resolve_share_crawl(handler, db, payload)
            if crawl is None:
                return
            page_num, page_size = _parse_pagination(payload)
            severity = (payload.get("severity") or "").strip() or None
            search = (payload.get("search") or "").strip()
            filters = [Issue.crawl_id == crawl.id]
            if severity:
                filters.append(Issue.severity == severity)
            if search:
                filters.append(Issue.issue_type.ilike(f"%{search}%"))
            total = db.execute(select(func.count()).select_from(Issue).where(*filters)).scalar_one()
            rows = db.execute(
                select(Issue, Page.url).outerjoin(Page, Issue.page_id == Page.id).where(*filters)
                .order_by(Issue.id.asc()).offset((page_num - 1) * page_size).limit(page_size)
            ).all()
            issues_out = [
                {
                    "id": issue.id, "issueType": issue.issue_type, "severity": issue.severity,
                    "category": (issue.explanation_json or {}).get("category", "Other"),
                    "recommendation": (issue.explanation_json or {}).get("recommendation", ""),
                    "impactScore": issue.impact_score, "effortLevel": issue.effort_level, "pageUrl": url,
                }
                for issue, url in rows
            ]
            send_json(handler, 200, {"issues": issues_out, "total": total, "page": page_num, "pageSize": page_size})
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (shareIssues) request failed")
        send_json(handler, 500, {"error": "Internal error while loading the shared report."})


def _handle_share_export(handler, payload):
    try:
        fmt = (payload.get("format") or "csv").strip().lower()
        if fmt not in _EXPORT_MIME:
            send_json(handler, 400, {"error": "format must be csv, xlsx, pdf, or json"})
            return
        with SessionLocal() as db:
            crawl = _resolve_share_crawl(handler, db, payload)
            if crawl is None:
                return
            results = build_crawl_results(db, crawl.id)
            if not results:
                send_json(handler, 404, {"error": "This crawl has no pages to export."})
                return
            crawl_ref = crawl.id
        _write_report(handler, crawl_ref, fmt, results)
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (shareExport) request failed")
        send_json(handler, 500, {"error": "Internal error while generating the export."})


def _handle_cron(handler, payload):
    """M5 T5.4: fire due schedules (and, if opted in, run a bounded server-side
    crawl chunk). Public action - guarded by the CRON_SECRET bearer token when
    set - so an external scheduler (cron-job.org, GitHub Actions, etc.) can
    trigger it, since Vercel's built-in cron is plan-gated on Hobby. Not in
    _ORG_SCOPED, so the do_POST org gate never runs for it."""
    try:
        secret = (os.environ.get("CRON_SECRET") or "").strip()
        if secret and (handler.headers.get("Authorization") or "") != f"Bearer {secret}":
            send_json(handler, 401, {"error": "unauthorized"})
            return
        from worker.scheduler import enqueue_due_crawls
        enqueued = enqueue_due_crawls()
        execute = str(os.environ.get("CRON_EXECUTE_CRAWLS") or "").strip().lower() in ("1", "true", "yes", "on")
        summary = {"enqueued": enqueued, "execution_enabled": execute}
        if execute:
            from worker import cron_runner
            run_summary = cron_runner.process_due()
            summary["ran"] = run_summary.get("ran", [])
            summary["finished"] = run_summary.get("finished", [])
        else:
            summary["note"] = "server-side crawl execution is disabled; set CRON_EXECUTE_CRAWLS=1 to enable it"
        send_json(handler, 200, summary)
    except Exception:  # noqa: BLE001
        logger.exception("crawls.py (cron) request failed")
        send_json(handler, 500, {"error": "Internal error while running cron."})


_ACTIONS = {
    "list": _handle_list,
    "create": _handle_create,
    "ingest": _handle_ingest,
    "finalize": _handle_finalize,
    "status": _handle_status,
    "thematic": _handle_thematic,
    "trend": _handle_trend,
    "pages": _handle_pages,
    "issues": _handle_issues,
    "links": _handle_links,
    "compare": _handle_compare,
    "setSchedule": _handle_set_schedule,
    "pause": _handle_pause,
    "resume": _handle_resume,
    "setShare": _handle_set_share,
    "revokeShare": _handle_revoke_share,
    "export": _handle_export,
    "shareSummary": _handle_share_summary,
    "sharePages": _handle_share_pages,
    "shareIssues": _handle_share_issues,
    "shareExport": _handle_share_export,
    "cron": _handle_cron,
}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            payload = read_json_body(self)
        except ValueError:  # malformed JSON (JSONDecodeError subclasses ValueError)
            send_json(self, 400, {"error": "Request body must be valid JSON."})
            return
        except Exception:  # noqa: BLE001
            logger.exception("crawls.py request body could not be parsed")
            send_json(self, 500, {"error": "Internal error while processing the request."})
            return

        action = payload.get("action")
        fn = _ACTIONS.get(action)
        if fn is None:
            send_json(self, 400, {"error": f"Unknown or missing action (expected one of {sorted(_ACTIONS)})"})
            return

        # Per-org isolation (05 §4): derive the org from the session and verify
        # crawl ownership before the handler runs. `resolve_org_id` returns None
        # in dev/test (no session, no VERCEL) - no scoping, preserving the
        # single-tenant behavior; in production an unauthenticated org-scoped
        # request is rejected with 401.
        self._org_id = None
        if action in _ORG_SCOPED:
            with SessionLocal() as db:
                try:
                    self._org_id = resolve_org_id(self, db)
                except AuthError as e:
                    send_json(self, e.status, {"error": e.message})
                    return
                if self._org_id is not None and action in _CRAWL_SCOPED:
                    try:
                        crawl_id = int(payload.get("crawlId"))
                    except (TypeError, ValueError):
                        crawl_id = None
                    if crawl_id is not None and crawl_for_org(db, crawl_id, self._org_id) is None:
                        send_json(self, 404, {"error": "Crawl not found."})
                        return
        fn(self, payload)
