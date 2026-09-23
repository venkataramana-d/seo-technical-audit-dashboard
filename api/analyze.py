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

import json
import logging
import os
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from modules._http import read_json_body, send_json  # noqa: E402
from modules.crawl_graph import build_depth_report, excessive_depth_issues  # noqa: E402
from modules.google_integrations import (  # noqa: E402
    GoogleIntegrationError,
    fetch_ga4,
    fetch_gsc,
    join_ga4_to_pages,
    join_gsc_to_pages,
)
from modules.link_score import build_link_score_report  # noqa: E402
from modules.sitewide import SiteLink, SitePage, run_sitewide_audit  # noqa: E402
from worker.api_key_service import get_api_key, get_or_create_default_org  # noqa: E402
from worker.db.models import Crawl, Link, Page, Project  # noqa: E402
from worker.db.session import SessionLocal  # noqa: E402
from worker.access import crawl_for_org, resolve_org_id  # noqa: E402
from worker.auth import AuthError  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 28


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


def _handle_link_score(handler, payload):
    """Internal PageRank / Link Score (M3 T3.1) over the crawl's internal link
    graph: which pages the site's own linking promotes, plus inlink/outlink
    counts. Highest-scoring page is normalized to 100."""
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    try:
        with SessionLocal() as db:
            data = _load_crawl_data(db, crawl_id)
            if data is None:
                send_json(handler, 404, {"error": "Crawl not found."})
                return
            _, root_url, _, site_links, page_urls = data
            report = build_link_score_report(page_urls, site_links, root=root_url)
            send_json(handler, 200, {
                "crawlId": crawl_id,
                "rootUrl": root_url,
                "pageCount": len(page_urls),
                "topPages": [
                    {"url": u, "score": s, "inlinks": report.inlinks.get(u, 0),
                     "outlinks": report.outlinks.get(u, 0)}
                    for (u, s) in report.top_pages
                ],
                "lowestPages": [
                    {"url": u, "score": s, "inlinks": report.inlinks.get(u, 0),
                     "outlinks": report.outlinks.get(u, 0)}
                    for (u, s) in report.lowest_pages
                ],
            })
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py link-score failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while computing link scores."})


def _handle_link_graph(handler, payload):
    """Node-link graph for the crawl visualization (M5 T5.3): the top pages by
    internal Link Score plus the internal edges among them. Bounded to keep the
    payload and client render cheap - the highest-authority pages are the ones
    worth seeing structurally."""
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    NODE_CAP = 60
    try:
        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": "Crawl not found."})
                return
            rows = db.execute(
                select(Page.id, Page.normalized_url, Page.link_score, Page.depth,
                       Page.inlinks, Page.outlinks)
                .where(Page.crawl_id == crawl_id, Page.link_score.isnot(None))
                .order_by(Page.link_score.desc())
                .limit(NODE_CAP)
            ).all()
            if not rows:
                send_json(handler, 200, {"crawlId": crawl_id, "nodes": [], "edges": [],
                                         "truncated": False})
                return
            id_by_url = {r.normalized_url: i for i, r in enumerate(rows)}
            node_ids = [r.id for r in rows]
            nodes = [
                {"url": r.normalized_url, "score": r.link_score, "depth": r.depth,
                 "inlinks": r.inlinks, "outlinks": r.outlinks}
                for r in rows
            ]
            # Internal edges whose BOTH ends are in the node set.
            link_rows = db.execute(
                select(Page.normalized_url, Link.target_url)
                .join(Page, Link.page_id == Page.id)
                .where(Page.crawl_id == crawl_id, Link.page_id.in_(node_ids),
                       Link.link_type == "internal")
            ).all()
            edges = []
            seen = set()
            for src_url, tgt_url in link_rows:
                i, j = id_by_url.get(src_url), id_by_url.get(tgt_url)
                if i is not None and j is not None and i != j and (i, j) not in seen:
                    seen.add((i, j))
                    edges.append([i, j])
            total_pages = db.execute(
                select(func.count()).select_from(Page).where(Page.crawl_id == crawl_id)
            ).scalar_one()
            send_json(handler, 200, {
                "crawlId": crawl_id,
                "nodes": nodes,
                "edges": edges,
                "truncated": total_pages > len(nodes),
                "totalPages": total_pages,
            })
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py link-graph failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while building the link graph."})


def _handle_near_duplicates(handler, payload):
    crawl_id = _parse_crawl_id(handler, payload)
    if crawl_id is None:
        return
    try:
        from modules.near_duplicate import near_duplicate_from_signatures

        with SessionLocal() as db:
            crawl = db.get(Crawl, crawl_id)
            if crawl is None:
                send_json(handler, 404, {"error": "Crawl not found."})
                return
            rows = db.execute(
                select(Page.normalized_url, Page.content_hash, Page.content_signature_json)
                .where(Page.crawl_id == crawl_id)
            ).all()

            # Exact duplicates (content_hash) - always available.
            hashes: dict[str, list[str]] = {}
            for url, chash, _sig in rows:
                if chash:
                    hashes.setdefault(chash, []).append(url)
            exact_clusters = [urls for urls in hashes.values() if len(urls) > 1]

            # Fuzzy near-duplicates from stored MinHash signatures (M3 Tier B).
            signatures = {url: sig for url, _c, sig in rows if sig and sig.get("minhash")}
            fuzzy_available = len(signatures) >= 2
            near_dupe_issues = (
                near_duplicate_from_signatures(signatures) if fuzzy_available else []
            )

            send_json(handler, 200, {
                "crawlId": crawl_id,
                "fuzzyAvailable": fuzzy_available,
                "reason": None if fuzzy_available else (
                    "No stored content signatures for this crawl (crawled before "
                    "signatures were captured); re-run the crawl to enable fuzzy "
                    "matching. Exact duplicates by content hash are shown."),
                "exactDuplicateClusters": exact_clusters,
                "nearDuplicateClusters": [i.affected_urls for i in near_dupe_issues],
                "issues": [_site_issue_dto(i) for i in near_dupe_issues],
            })
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py near-duplicates failed for crawl %s", crawl_id)
        send_json(handler, 500, {"error": "Internal error while finding near-duplicates."})


# --------------------------------------------------------------------------- #
# Google integrations (GSC / GA4) - merged in from the former api/insights.py.
# The stored vault VALUE for each provider is a JSON-string wrapper (never the
# bare Google key), set from Settings -> the GSC/GA4 connect forms:
#   gsc: {"service_account": {<full Google SA JSON>}, "site_url": "https://example.com/"}
#   ga4: {"service_account": {<full Google SA JSON>}, "property_id": "123456789"}
# Auth + crawl ownership are already enforced by do_POST for every action, so
# these handlers only add the vault/provider logic. The secret is never returned.
# --------------------------------------------------------------------------- #
def _read_vault_wrapper(provider: str) -> dict | None:
    """Load and JSON-parse the vault wrapper for 'gsc'/'ga4', or None if the
    provider isn't configured. Raises ValueError if a value exists but isn't the
    expected JSON wrapper (surfaced to the caller as a 400)."""
    with SessionLocal() as db:
        org = get_or_create_default_org(db)
        raw = get_api_key(db, org.id, provider)
    if not raw:
        return None
    try:
        wrapper = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"The saved {provider} credential is not valid JSON - re-connect it "
            "in Settings by pasting the full service-account JSON."
        ) from e
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("service_account"), dict):
        raise ValueError(
            f"The saved {provider} credential is missing its service_account - "
            "re-connect it in Settings."
        )
    return wrapper


def _date_range(payload) -> tuple[str, str]:
    """Resolve the report window from the payload, defaulting to the last 28
    days. Accepts ISO 'YYYY-MM-DD' startDate/endDate."""
    end = (payload.get("endDate") or "").strip()
    start = (payload.get("startDate") or "").strip()
    if not end:
        end = date.today().isoformat()
    if not start:
        # Derive from end when possible, else today - lookback.
        try:
            end_d = date.fromisoformat(end)
        except ValueError:
            end_d = date.today()
            end = end_d.isoformat()
        start = (end_d - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
    return start, end


def _page_urls_for_crawl(crawl_id) -> list[str]:
    """Optional/guarded: the crawl's page URLs, for joining metrics onto pages.
    Any failure (bad id, DB unreachable, empty crawl) yields [] so the raw
    metrics still return."""
    try:
        cid = int(crawl_id)
    except (TypeError, ValueError):
        return []
    try:
        with SessionLocal() as db:
            rows = db.execute(select(Page.url).where(Page.crawl_id == cid)).scalars().all()
        return [u for u in rows if u]
    except Exception:  # noqa: BLE001 - joining is a best-effort enhancement
        logger.warning("analyze.py could not load page URLs for crawl %s", crawl_id, exc_info=True)
        return []


def _site_origin(site_url: str, page_urls: list[str]) -> str:
    """Origin to prefix GA4 pagePaths with. Prefer the configured GSC site_url's
    origin; else infer from the first page URL."""
    for candidate in (site_url, page_urls[0] if page_urls else ""):
        if not candidate:
            continue
        parts = urlsplit(candidate)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return ""


def _not_configured(provider_label: str) -> dict:
    return {
        "configured": False,
        "message": f"Connect Google {provider_label} in Settings to see this data.",
    }


def _handle_insights_status(handler, payload):
    """Report which providers are configured - booleans only, never the secret."""
    try:
        with SessionLocal() as db:
            org = get_or_create_default_org(db)
            gsc_set = bool(get_api_key(db, org.id, "gsc"))
            ga4_set = bool(get_api_key(db, org.id, "ga4"))
        send_json(handler, 200, {"gsc": gsc_set, "ga4": ga4_set})
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py (insights-status) request failed")
        send_json(handler, 500, {"error": "Internal error while reading integration status."})


def _handle_gsc(handler, payload):
    try:
        try:
            wrapper = _read_vault_wrapper("gsc")
        except ValueError as e:
            send_json(handler, 400, {"error": str(e)})
            return
        if wrapper is None:
            send_json(handler, 200, _not_configured("Search Console"))
            return

        site_url = (wrapper.get("site_url") or "").strip()
        if not site_url:
            send_json(handler, 400, {
                "error": "The saved GSC credential has no site_url - re-connect it in Settings.",
            })
            return

        start, end = _date_range(payload)
        row_limit = int(payload.get("rowLimit", 1000) or 1000)
        rows = fetch_gsc(
            wrapper["service_account"], site_url,
            start_date=start, end_date=end, dimensions=("page",), row_limit=row_limit,
        )

        result = {
            "configured": True,
            "siteUrl": site_url,
            "startDate": start,
            "endDate": end,
            "rows": rows,
        }
        crawl_id = payload.get("crawlId")
        if crawl_id is not None:
            page_urls = _page_urls_for_crawl(crawl_id)
            if page_urls:
                result["byPage"] = join_gsc_to_pages(rows, page_urls)
        send_json(handler, 200, result)
    except GoogleIntegrationError as e:
        send_json(handler, 400, {"configured": True, "error": str(e)})
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py (gsc) request failed")
        send_json(handler, 500, {"error": "Internal error while fetching Search Console data."})


def _handle_ga4(handler, payload):
    try:
        try:
            wrapper = _read_vault_wrapper("ga4")
        except ValueError as e:
            send_json(handler, 400, {"error": str(e)})
            return
        if wrapper is None:
            send_json(handler, 200, _not_configured("Analytics 4"))
            return

        property_id = str(wrapper.get("property_id") or "").strip()
        if not property_id:
            send_json(handler, 400, {
                "error": "The saved GA4 credential has no property_id - re-connect it in Settings.",
            })
            return

        start, end = _date_range(payload)
        rows = fetch_ga4(
            wrapper["service_account"], property_id,
            start_date=start, end_date=end,
            dimensions=("pagePath",), metrics=("sessions", "screenPageViews"),
        )

        result = {
            "configured": True,
            "propertyId": property_id,
            "startDate": start,
            "endDate": end,
            "rows": rows,
        }
        crawl_id = payload.get("crawlId")
        if crawl_id is not None:
            page_urls = _page_urls_for_crawl(crawl_id)
            if page_urls:
                # GA4 gives paths; combine with the site origin to match full URLs.
                gsc_wrapper = None
                try:
                    gsc_wrapper = _read_vault_wrapper("gsc")
                except ValueError:
                    gsc_wrapper = None
                gsc_site = (gsc_wrapper.get("site_url") if gsc_wrapper else "") or ""
                origin = _site_origin(gsc_site, page_urls)
                result["byPage"] = join_ga4_to_pages(rows, page_urls, origin)
        send_json(handler, 200, result)
    except GoogleIntegrationError as e:
        send_json(handler, 400, {"configured": True, "error": str(e)})
    except Exception:  # noqa: BLE001
        logger.exception("analyze.py (ga4) request failed")
        send_json(handler, 500, {"error": "Internal error while fetching Analytics data."})


_ACTIONS = {
    "sitewide": _handle_sitewide,
    "crawl-graph": _handle_crawl_graph,
    "link-score": _handle_link_score,
    "link-graph": _handle_link_graph,
    "near-duplicates": _handle_near_duplicates,
    "insights-status": _handle_insights_status,
    "gsc": _handle_gsc,
    "ga4": _handle_ga4,
}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            payload = read_json_body(self)
        except ValueError:  # malformed JSON (JSONDecodeError subclasses ValueError)
            send_json(self, 400, {"error": "Request body must be valid JSON."})
            return
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
