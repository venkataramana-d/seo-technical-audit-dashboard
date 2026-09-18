import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import bulk_url_cap, read_json_body, require_str, send_json, validate_pattern, validate_url_or_400  # noqa: E402
from worker.auth import AuthError, require_authenticated  # noqa: E402
from modules.auditor import audit_url  # noqa: E402
from modules.crawler import CrawlConfig, crawl_site  # noqa: E402
from modules.pagespeed import fetch_pagespeed  # noqa: E402
from modules.sitemap_extractor import (  # noqa: E402
    DEFAULT_URL_CAP,
    SitemapError,
    discover_sitemap_url,
    extract_sitemap_urls,
)
from modules.technical_checks import analyze_domain_health  # noqa: E402
from worker.api_key_service import get_default_org_vaulted_key  # noqa: E402

logger = logging.getLogger(__name__)

# Discovery-only cap, shares modules/_http.py::bulk_url_cap with the
# sitemap/CSV modes (200 in production, see that module for why). Unlike a
# sitemap fetch (one XML download), BFS crawl discovery does a real HTTP GET
# per page just to extract links, all within this one synchronous
# request/invocation (not chunked the way per-URL audits are) — a crawl
# anywhere near this cap risks exceeding Vercel's maxDuration window.
DEFAULT_MAX_PAGES = 50
MAX_MAX_PAGES = bulk_url_cap()


def _handle_audit(handler, payload):
    try:
        url = require_str(handler, payload, "url")
        if url is None:
            return
        if not validate_url_or_400(handler, url):
            return

        audit_type = payload.get("auditType", "auto")
        check_links = bool(payload.get("checkLinks", True))
        validate_links = bool(payload.get("validateLinks", False))
        fetch_pagespeed_flag = bool(payload.get("fetchPagespeed", False))
        # Precedence: a key pasted for this one request > a saved vault
        # entry (Phase 5) > the server-wide env var.
        psi_api_key = (
            payload.get("psiApiKey")
            or get_default_org_vaulted_key("psi")
            or os.environ.get("PSI_API_KEY")
        )
        # Optional: domain-level site-health computed once by the client
        # (via the "site-health" action) and reused, so a same-domain crawl
        # skips re-running WHOIS/DNS/SSL/robots/etc. per page.
        prefetched_domain_health = payload.get("prefetchedDomainHealth")
        if not isinstance(prefetched_domain_health, dict):
            prefetched_domain_health = None

        result = audit_url(
            url,
            audit_type=audit_type,
            check_links=check_links,
            validate_links=validate_links,
            fetch_pagespeed=fetch_pagespeed_flag,
            psi_api_key=psi_api_key,
            prefetched_domain_health=prefetched_domain_health,
        )
        result.pop("_soup_text", None)
        send_json(handler, 200, result)
    except Exception:  # noqa: BLE001
        logger.exception("audit-pipeline.py (audit) request failed")
        send_json(handler, 500, {"error": "Internal error while running the audit."})


def _handle_sitemap(handler, payload):
    try:
        raw = require_str(handler, payload, "sitemapUrl", "url", field_name="sitemapUrl")
        if raw is None:
            return

        # If the caller passed a bare domain / page URL, guess its sitemap.
        sitemap_url = raw if raw.rstrip("/").lower().endswith(".xml") or "sitemap" in raw.lower() else discover_sitemap_url(raw)

        if not validate_url_or_400(handler, sitemap_url):
            return

        limit = payload.get("limit", DEFAULT_URL_CAP)
        include_pattern = payload.get("includePattern") or None
        exclude_pattern = payload.get("excludePattern") or None

        for pat in (include_pattern, exclude_pattern):
            if pat:
                err = validate_pattern(pat)
                if err:
                    send_json(handler, 400, {"error": err})
                    return

        result = extract_sitemap_urls(
            sitemap_url,
            limit=limit,
            include_pattern=include_pattern,
            exclude_pattern=exclude_pattern,
        )
        send_json(handler, 200, result)
    except SitemapError as e:
        send_json(handler, 502, {"error": f"Sitemap error: {e}"})
    except Exception:  # noqa: BLE001
        logger.exception("audit-pipeline.py (sitemap) request failed")
        send_json(handler, 500, {"error": "Internal error while resolving the sitemap."})


def _handle_crawl(handler, payload):
    try:
        seed_url = require_str(handler, payload, "seedUrl", "url", field_name="seedUrl")
        if seed_url is None:
            return
        if not validate_url_or_400(handler, seed_url):
            return

        max_pages = max(1, min(int(payload.get("maxPages") or DEFAULT_MAX_PAGES), MAX_MAX_PAGES))

        for pat in (payload.get("includePattern"), payload.get("excludePattern")):
            if pat:
                err = validate_pattern(pat)
                if err:
                    send_json(handler, 400, {"error": err})
                    return

        try:
            config = CrawlConfig(
                seed_url=seed_url,
                seed_source=payload.get("seedSource", "homepage"),
                include_patterns=[payload["includePattern"]] if payload.get("includePattern") else [],
                exclude_patterns=[payload["excludePattern"]] if payload.get("excludePattern") else [],
                max_depth=int(payload.get("maxDepth") or 3),
                max_pages=max_pages,
                include_subdomains=bool(payload.get("includeSubdomains", False)),
                user_agent=payload.get("userAgent", "default"),
                robots_mode=payload.get("robotsMode", "respect"),
                max_workers=4,
                # Discovery only: the browser fans out per-page audits itself
                # via lib/crawl/orchestrator.ts, so this stays fast and safely
                # under the Vercel maxDuration cap even at MAX_MAX_PAGES.
                run_full_audit=False,
                # Hard wall-clock budget under this function's 90s maxDuration
                # (vercel.json) so a large/slow site can't overrun the timeout;
                # partial results come back flagged timed_out.
                max_seconds=80.0,
            )
        except ValueError as e:
            send_json(handler, 400, {"error": str(e)})
            return

        result = crawl_site(config)
        if result.get("error"):
            send_json(handler, 502, {"error": result["error"]})
            return

        urls = [p["url"] for p in result["pages"]]
        send_json(handler, 200, {
            "seed_url": seed_url,
            "urls": urls,
            "total_found": len(urls),
            "capped": result["stats"]["pages_crawled"] >= max_pages,
            "timed_out": result["stats"].get("timed_out", False),
            "skipped_robots": len(result.get("skipped_robots", [])),
            "skipped_scope": len(result.get("skipped_scope", [])),
            "errors": len(result.get("errors", [])),
            "depth_reached": result["stats"]["depth_reached"],
            "duration_seconds": result["stats"]["duration_seconds"],
        })
    except Exception:  # noqa: BLE001
        logger.exception("audit-pipeline.py (crawl) request failed")
        send_json(handler, 500, {"error": "Internal error while crawling."})


def _handle_site_health(handler, payload):
    """Return only the domain-level site-health checks for a URL's domain
    (robots, sitemap, WHOIS, SSL, HTTPS enforcement, DNS, www-redirect,
    HTTP/2). The client fetches this once per domain and passes it into the
    per-URL "audit" action so a same-domain crawl doesn't re-run these for
    every page (Phase 2, PROJECT_LOG)."""
    try:
        url = require_str(handler, payload, "url")
        if url is None:
            return
        if not validate_url_or_400(handler, url):
            return

        send_json(handler, 200, {"url": url, "domain_health": analyze_domain_health(url)})
    except Exception:  # noqa: BLE001
        logger.exception("audit-pipeline.py (site-health) request failed")
        send_json(handler, 500, {"error": "Internal error while checking site health."})


def _handle_pagespeed(handler, payload):
    try:
        url = require_str(handler, payload, "url")
        if url is None:
            return

        strategy = payload.get("strategy", "mobile")
        api_key = payload.get("apiKey") or get_default_org_vaulted_key("psi") or os.environ.get("PSI_API_KEY")

        result = fetch_pagespeed(url, strategy=strategy, api_key=api_key)
        send_json(handler, 200, result)
    except Exception:  # noqa: BLE001
        logger.exception("audit-pipeline.py (pagespeed) request failed")
        send_json(handler, 500, {"error": "Internal error while fetching PageSpeed data."})


# Consolidates what used to be 5 separate api/*.py files (audit, sitemap,
# crawl, site-health, pagespeed) into one Vercel serverless function.
# Each was its own isolated Python function, and Vercel's Python builder
# reinstalls + recompiles the ENTIRE requirements.txt independently per
# function — with 10 api/*.py files that added ~14s x 10 to every build.
# Consolidating the 9 non-export functions down to 2 (this one + api/ai.py)
# cuts that to ~14s x 3, saving roughly 100s per deploy. Dispatch is by an
# "action" field in the JSON body rather than the URL path, so callers POST
# to /api/audit-pipeline with {"action": "audit", ...}.
_ACTIONS = {
    "audit": _handle_audit,
    "sitemap": _handle_sitemap,
    "crawl": _handle_crawl,
    "site-health": _handle_site_health,
    "pagespeed": _handle_pagespeed,
}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # All actions perform server-side fetches and/or spend vaulted API keys,
        # so require a signed-in user (prevents anonymous quota abuse / using the
        # crawler as an SSRF proxy) — audit finding #4.
        try:
            require_authenticated(self)
        except AuthError as e:
            send_json(self, e.status, {"error": e.message})
            return
        try:
            payload = read_json_body(self)
        except (ValueError, json.JSONDecodeError):
            send_json(self, 400, {"error": "Request body must be valid JSON."})
            return
        except Exception:  # noqa: BLE001
            logger.exception("audit-pipeline.py request body could not be parsed")
            send_json(self, 500, {"error": "Internal error while processing the request."})
            return

        action = payload.get("action")
        fn = _ACTIONS.get(action)
        if fn is None:
            send_json(self, 400, {"error": f"Unknown or missing action (expected one of {sorted(_ACTIONS)})"})
            return
        fn(self, payload)
