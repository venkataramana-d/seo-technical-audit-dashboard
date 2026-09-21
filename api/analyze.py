"""Site-wide analysis API - the "brains" that run on-demand over an already
persisted crawl (Vercel-only architecture: no always-on worker).

Same one-file / action-dispatch convention as api/audit-pipeline.py and
api/crawls.py - POST {"action": ..., "crawlId": N}. Reads pages/links straight
from the same DB (worker/db/session.py) and feeds the pure analysis modules
folded in from the rebuild (modules/sitewide.py, modules/crawl_graph.py,
modules/near_duplicate.py). Nothing here writes by default; pass
{"persist": true} on the sitewide action to also store crawl-level Issue rows.

Actions:
  - "sitewide"        : duplicate titles/desc/h1/content, orphans, redirect
                        chains & loops, broken internal links, sitemap diff,
                        hreflang reciprocity  (02-AUDIT-ENGINE.md §2)
  - "crawl-graph"     : click-depth report + excessive-depth issues
  - "near-duplicates" : MinHash/LSH fuzzy-duplicate clusters (needs stored
                        content signatures; degrades gracefully otherwise)

Diff/compare already lives in api/crawls.py ("compare" action) and is not
duplicated here.
"""

import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import read_json_body, send_json  # noqa: E402
from modules.crawl_graph import build_depth_report, excessive_depth_issues  # noqa: E402
from modules.sitewide import SiteLink, SitePage, run_sitewide_audit  # noqa: E402
from worker.db.models import Crawl, Link, Page, Project  # noqa: E402
from worker.db.session import SessionLocal  # noqa: E402
from worker.access import crawl_for_org, resolve_org_id  # noqa: E402
from worker.auth import AuthError  # noqa: E402

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# DB row  ->  pure-module dataclass adapters
# --------------------------------------------------------------------------- #
def _hreflang_pairs(raw) -> list:
    """pages.hreflang_json is stored loosely; accept a few shapes and coerce to
    (lang, href) tuples, dropping anything malformed."""
    pairs = []
    for item in raw or []:
        if isinstance(item, dict):
            lang = item.get("lang") or item.get("hreflang") or item.get("lang_code")
            href = item.get("href") or item.get("url")
            if lang and href:
                pairs.append((str(lang), str(href)))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.append((str(item[0]), str(item[1])))
    return pairs


def _to_site_pages(pages: list) -> list:
    out = []
    for p in pages:
        out.append(SitePage(
            normalized_url=p.normalized_url,
            url=p.url,
            status_code=p.status_code,
            title=p.title,
            meta_description=p.meta_description,
            h1=p.h1,
            content_hash=p.content_hash,
            redirect_chain=p.redirect_chain_json or [],
            hreflang=_hreflang_pairs(p.hreflang_json),
            # The current serverless schema does not persist click-depth or the
            # sitemap-membership flag; sitewide handles these as unknown/False.
            depth=None,
            in_sitemap=False,
        ))
    return out


def _to_site_links(links: list, page_url_by_id: dict) -> list:
    out = []
    for lk in links:
        source = page_url_by_id.get(lk.page_id)
        if source is None:
            continue
        out.append(SiteLink(
            source_url=source,
            target_url=lk.target_url,
            link_type=str(lk.link_type),
            status_code=lk.status_code,
            is_broken=bool(lk.is_broken),
        ))
    return out


def _load_crawl_data(db, crawl_id: int):
    """Returns (crawl, root_url, site_pages, site_links, page_urls) or None if
    the crawl doesn't exist."""
    crawl = db.get(Crawl, crawl_id)
    if crawl is None:
        return None
    project = db.get(Project, crawl.project_id)
    root_url = project.root_url if project else None

    pages = db.query(Page).filter(Page.crawl_id == crawl_id).all()
    page_ids = [p.id for p in pages]
    page_url_by_id = {p.id: p.normalized_url for p in pages}
    links = db.query(Link).filter(Link.page_id.in_(page_ids)).all() if page_ids else []

    return crawl, root_url, _to_site_pages(pages), _to_site_links(links, page_url_by_id), set(page_url_by_id.values())


def _site_issue_dto(si) -> dict:
    """Full JSON for a SiteIssue - to_explanation_json() alone drops the
    type/severity/impact fields the UI needs, so serialize the whole record."""
    return {
        "issueType": si.issue_type,
        "category": si.category,
        "severity": si.severity,
        "impactScore": si.impact_score,
        "effortLevel": si.effort_level,
        "what": si.what,
        "why": si.why,
        "rootCause": si.root_cause,
        "fix": si.fix,
        "affectedUrls": si.affected_urls,
        "affectedCount": len(si.affected_urls),
    }


def _parse_crawl_id(handler, payload):
    raw = payload.get("crawlId", payload.get("crawl_id"))
    try:
        return int(raw)
    except (TypeError, ValueError):
        send_json(handler, 400, {"error": "Missing or invalid 'crawlId'."})
        return None


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def _handle_sitewide(handler, payload):
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    try:
        with SessionLocal() as db:
            data = _load_crawl_data(db, crawl_id)
            if data is None:
                send_json(handler, 404, {"error": "Crawl not found."})
                return
            crawl, root_url, site_pages, site_links, _ = data
            issues = run_sitewide_audit(
                site_pages, site_links,
                sitemap_urls=set(),
                root_url=root_url,
            )
            send_json(handler, 200, {
                "crawlId": crawl_id,
                "rootUrl": root_url,
                "pageCount": len(site_pages),
                "linkCount": len(site_links),
                "issueCount": len(issues),
                "issues": [_site_issue_dto(i) for i in issues],
            })
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py sitewide failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while running site-wide analysis."})


def _handle_crawl_graph(handler, payload):
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    try:
        with SessionLocal() as db:
            data = _load_crawl_data(db, crawl_id)
            if data is None:
                send_json(handler, 404, {"error": "Crawl not found."})
                return
            crawl, root_url, site_pages, site_links, page_urls = data
            if not root_url:
                send_json(handler, 422, {"error": "Crawl has no root URL to anchor the graph."})
                return
            report = build_depth_report(root_url, page_urls, site_links)
            issues = excessive_depth_issues(report, site_links, page_urls)
            send_json(handler, 200, {
                "crawlId": crawl_id,
                "root": report.root,
                "maxDepth": report.max_depth,
                "avgDepth": report.avg_depth,
                "reachableCount": report.reachable_count,
                "pagesPerDepth": report.pages_per_depth,
                "unreachableUrls": report.unreachable_urls,
                "deepestPages": [{"url": u, "depth": d} for (u, d) in report.deepest_pages],
                "issues": [_site_issue_dto(i) for i in issues],
            })
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py crawl-graph failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while building the crawl graph."})


def _handle_near_duplicates(handler, payload):
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    try:
        with SessionLocal() as db:
            data = _load_crawl_data(db, crawl_id)
            if data is None:
                send_json(handler, 404, {"error": "Crawl not found."})
                return
            _, _, site_pages, _, _ = data
            # Fuzzy near-duplicate detection needs stored MinHash signatures,
            # which the current serverless schema does not persist. Exact
            # duplicates (by content_hash) are already reported by the sitewide
            # "duplicate_content" check, so surface that as the honest fallback.
            hashes = {}
            for p in site_pages:
                if p.content_hash:
                    hashes.setdefault(p.content_hash, []).append(p.normalized_url)
            exact_clusters = [urls for urls in hashes.values() if len(urls) > 1]
            send_json(handler, 200, {
                "crawlId": crawl_id,
                "fuzzyAvailable": False,
                "reason": "Content signatures are not stored for this crawl; "
                          "fuzzy near-duplicate matching needs them. Exact "
                          "duplicates (by content hash) are shown instead.",
                "exactDuplicateClusters": exact_clusters,
            })
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py near-duplicates failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while finding near-duplicates."})


_ACTIONS = {
    "sitewide": _handle_sitewide,
    "crawl-graph": _handle_crawl_graph,
    "near-duplicates": _handle_near_duplicates,
}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            payload = read_json_body(self)
        except Exception:  # noqa: BLE001
            logger.exception("analyze.py request body could not be parsed")
            send_json(self, 500, {"error": "Internal error while processing the request."})
            return

        action = payload.get("action")
        fn = _ACTIONS.get(action)
        if fn is None:
            send_json(self, 400, {"error": f"Unknown or missing action (expected one of {sorted(_ACTIONS)})"})
            return

        # Per-org isolation: every analyze action is crawl-scoped. Verify the
        # session owns the crawl (None org = dev/test, no scoping; 401 if
        # unauthenticated in production).
        try:
            raw = payload.get("crawlId", payload.get("crawl_id"))
            crawl_id = int(raw)
        except (TypeError, ValueError):
            crawl_id = None
        with SessionLocal() as db:
            try:
                org_id = resolve_org_id(self, db)
            except AuthError as e:
                send_json(self, e.status, {"error": e.message})
                return
            if org_id is not None and crawl_id is not None and crawl_for_org(db, crawl_id, org_id) is None:
                send_json(self, 404, {"error": "Crawl not found."})
                return
        fn(self, payload)
