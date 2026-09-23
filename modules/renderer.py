"""Playwright-based rendering: a drop-in peer of `fetch_page()`
(modules/auditor.py), used when a crawl has `render_js` enabled
(01-CRAWLER-ENGINE.md §3). Returns the exact same dict shape so it plugs
into `audit_url(prefetched=...)` unchanged - the whole per-page audit
pipeline doesn't need to know whether its input came from `requests` or a
real browser.

SSRF: the initial URL is validated with `validate_audit_url()`, and a route
guard aborts any main-frame navigation (i.e. a redirect) to a blocked
private/loopback/link-local/metadata host - the browser equivalent of the
requests path's per-hop re-validation in `safe_request()`. Subresource requests
(images/css/js) are left to the browser; only navigations can exfiltrate an
internal page's HTML into the audit, which is the SSRF vector that matters here.

Each call is fully self-contained (its own browser launch/close) rather
than reusing one instance across a thread - Playwright's sync API ties a
browser to the OS thread that created it, and safely sharing one across
calls needs real lifecycle bookkeeping this phase's scope doesn't need.
Slower per page (~200-500ms relaunch overhead), but trivially safe to
reason about - rendering is already expected to be far slower than a raw
fetch (see modules/crawler.py's dedicated, small render-worker pool).
"""

from __future__ import annotations

import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from modules.auditor import validate_audit_url

DEFAULT_TIMEOUT_MS = 5000


def _nav_allowed(u: str) -> bool:
    """SSRF gate for a navigation URL. Only http(s) URLs reach a network host, so
    inert schemes (data:/about:/blob:) are always allowed; http(s) URLs must pass
    validate_audit_url (blocks private/loopback/link-local/metadata hosts)."""
    low = (u or "").strip().lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return True
    ok, _ = validate_audit_url(u)
    return ok


def render_page(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> dict:
    started = time.monotonic()
    # Block the initial URL up front (SSRF), same as the requests path.
    if not _nav_allowed(url):
        return {"success": False, "error": "Render blocked: URL failed SSRF validation.", "status_code": 0}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()

                # Re-validate every main-frame navigation (i.e. a redirect target)
                # and abort it if it resolves to a private/metadata host - the
                # browser analogue of safe_request()'s per-hop check. Subresources
                # are not validated (perf; they can't leak an internal page's HTML
                # into the audit result).
                def _guard(route):
                    req = route.request
                    if req.resource_type == "document" and not _nav_allowed(req.url):
                        route.abort()
                        return
                    route.continue_()

                page.route("**/*", _guard)
                # networkidle: wait for no network activity for 500ms, so
                # client-rendered content (fetch/XHR-driven) has a chance to
                # land before we read the DOM. A page that never goes idle
                # (e.g. persistent polling) times out - treated as a failure
                # below, same as fetch_page()'s own Timeout handling.
                response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                html = page.content()
                final_url = page.url
                status_code = response.status if response else 200
                headers = dict(response.headers) if response else {}
            finally:
                browser.close()

        soup = BeautifulSoup(html, "lxml")
        return {
            "success": True,
            "status_code": status_code,
            "final_url": final_url,
            # Playwright's main navigation response doesn't expose the full
            # redirect chain the way requests' resp.history does; raw
            # fetch_page() already covers redirect-chain detection for BFS/
            # site-audit purposes, so this isn't duplicated here.
            "redirect_count": 0,
            "redirect_history": [],
            "content_type": headers.get("content-type", ""),
            "soup": soup,
            "html": html,
            "response_time": round(time.monotonic() - started, 3),
            "http_headers": headers,
            "page_size_bytes": len(html.encode("utf-8")),
        }
    except Exception as e:  # noqa: BLE001 - any Playwright/browser failure -> fetch_page's own failure shape
        return {"success": False, "error": f"Render error: {type(e).__name__}: {e}", "status_code": 0}
