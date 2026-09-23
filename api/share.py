"""Public, unauthenticated read endpoint for a shared crawl report (M5 T5.2).

The ONLY key is the share token minted by api/crawls.py (setShare). There is
NO auth here and NO crawlId parameter: the long, unguessable token IS the
capability. Given a valid token we resolve exactly one crawl (bypassing org
scope via worker.crawl_export.get_crawl_by_share_token) and serve read-only
summary/pages/issues listings plus report downloads. An unknown or revoked
token returns 404 - never a fallback to org scope, never any other crawl.

POST {"token": <str>, "action": "summary"|"pages"|"issues"|"export", ...}
GET  ?token=<str>            -> convenience alias for the summary action
"""

import logging
import os
import sys
from urllib.parse import parse_qs, urlparse

from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import read_json_body, send_json  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from worker.crawl_export import build_crawl_results, crawl_meta, get_crawl_by_share_token  # noqa: E402
from worker.db.models import Issue, Page  # noqa: E402
from worker.db.session import SessionLocal  # noqa: E402

logger = logging.getLogger(__name__)

# Same pagination bounds as api/crawls.py's listings.
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200

# Report formats -> (generator-selecting key, MIME type). csv/xlsx/pdf reuse
# modules/report_generator; json is dumped inline.
_EXPORT_MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "json": "application/json",
}

_INVALID_TOKEN = "This shared report link is invalid or has been revoked."


def _parse_pagination(payload) -> tuple[int, int]:
    page_num = max(1, int(payload.get("page", 1) or 1))
    page_size = min(max(1, int(payload.get("pageSize", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    return page_num, page_size


def _summary(handler, db, crawl):
    """Header metadata + lightweight counts (pages, issue severities) for the
    public summary card."""
    pages_count = db.execute(
        select(func.count()).select_from(Page).where(Page.crawl_id == crawl.id)
    ).scalar_one()
    severity_rows = db.execute(
        select(Issue.severity, func.count())
        .where(Issue.crawl_id == crawl.id)
        .group_by(Issue.severity)
    ).all()
    severity_counts = {severity: count for severity, count in severity_rows}
    send_json(handler, 200, {
        "meta": crawl_meta(db, crawl.id),
        "pagesCount": pages_count,
        "issueSeverityCounts": severity_counts,
        "issuesCount": sum(severity_counts.values()),
    })


def _pages(handler, db, crawl, payload):
    """Paginated, read-only page listing - the minimal, replicated shape of
    api/crawls.py's `pages` action (no private-handler import)."""
    page_num, page_size = _parse_pagination(payload)
    search = (payload.get("search") or "").strip()

    filters = [Page.crawl_id == crawl.id]
    if search:
        filters.append(Page.url.ilike(f"%{search}%"))

    total = db.execute(select(func.count()).select_from(Page).where(*filters)).scalar_one()
    rows = db.execute(
        select(Page)
        .where(*filters)
        .order_by(Page.id.asc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    # One aggregate query for this page's severity counts (no N+1).
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
            "depth": p.depth,
            "issueCounts": counts_by_page.get(p.id, {}),
        }
        for p in rows
    ]
    send_json(handler, 200, {"pages": pages_out, "total": total, "page": page_num, "pageSize": page_size})


def _issues(handler, db, crawl, payload):
    """Paginated, read-only issue listing - the minimal, replicated shape of
    api/crawls.py's `issues` action."""
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
        select(Issue, Page.url)
        .outerjoin(Page, Issue.page_id == Page.id)
        .where(*filters)
        .order_by(Issue.id.asc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    ).all()

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
        }
        for issue, url in rows
    ]
    send_json(handler, 200, {"issues": issues_out, "total": total, "page": page_num, "pageSize": page_size})


def _export(handler, db, crawl, payload):
    """Build and stream a downloadable report for the shared crawl. Reuses the
    exact same generators as the authenticated export path (api/export.py) via
    build_crawl_results()."""
    import json as _json

    fmt = (payload.get("format") or "csv").strip().lower()
    if fmt not in _EXPORT_MIME:
        send_json(handler, 400, {"error": "format must be csv, xlsx, pdf, or json"})
        return

    results = build_crawl_results(db, crawl.id)
    if not results:
        send_json(handler, 404, {"error": "This crawl has no pages to export."})
        return

    if fmt == "json":
        data = _json.dumps(results, default=str, indent=2).encode("utf-8")
    else:
        from modules.report_generator import generate_csv, generate_excel, generate_pdf
        if fmt == "csv":
            data = generate_csv(results)
        elif fmt == "xlsx":
            data = generate_excel(results)
        else:
            data = generate_pdf(results)

    handler.send_response(200)
    handler.send_header("Content-Type", _EXPORT_MIME[fmt])
    handler.send_header("Content-Disposition", f'attachment; filename="crawl-{crawl.id}-report.{fmt}"')
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _dispatch(handler, token, payload):
    """Resolve the token to a crawl (or 404) and run the requested action."""
    action = (payload.get("action") or "summary").strip()
    with SessionLocal() as db:
        crawl = get_crawl_by_share_token(db, token)
        if crawl is None:
            send_json(handler, 404, {"error": _INVALID_TOKEN})
            return
        if action == "summary":
            _summary(handler, db, crawl)
        elif action == "pages":
            _pages(handler, db, crawl, payload)
        elif action == "issues":
            _issues(handler, db, crawl, payload)
        elif action == "export":
            _export(handler, db, crawl, payload)
        else:
            send_json(handler, 400, {"error": "Unknown action (expected summary, pages, issues, or export)."})


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            payload = read_json_body(self)
        except ValueError:  # malformed JSON (JSONDecodeError subclasses ValueError)
            send_json(self, 400, {"error": "Request body must be valid JSON."})
            return
        except Exception:  # noqa: BLE001
            logger.exception("share.py request body could not be parsed")
            send_json(self, 500, {"error": "Internal error while processing the request."})
            return

        # Token may also arrive via ?token= (GET-style) for convenience.
        token = (payload.get("token") or "").strip()
        if not token:
            token = (parse_qs(urlparse(self.path).query).get("token", [""])[0] or "").strip()
        if not token:
            send_json(self, 400, {"error": "token is required"})
            return

        try:
            _dispatch(self, token, payload)
        except Exception:  # noqa: BLE001
            logger.exception("share.py request failed")
            send_json(self, 500, {"error": "Internal error while loading the shared report."})

    def do_GET(self):
        """Convenience alias: GET ?token=<str> returns the summary card."""
        token = (parse_qs(urlparse(self.path).query).get("token", [""])[0] or "").strip()
        if not token:
            send_json(self, 400, {"error": "token is required"})
            return
        try:
            _dispatch(self, token, {"action": "summary"})
        except Exception:  # noqa: BLE001
            logger.exception("share.py GET request failed")
            send_json(self, 500, {"error": "Internal error while loading the shared report."})
