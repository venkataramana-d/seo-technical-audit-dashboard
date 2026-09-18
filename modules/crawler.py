"""Site-wide crawler: BFS link discovery + per-page audit.

Modeled on the crawl-configuration patterns shared by Semrush, Ahrefs Site
Audit, and Screaming Frog: seed selection (homepage / sitemap / URL list),
scope control (domain + include/exclude regex + depth/page caps), a
configurable bot identity, and a robots.txt mode (respect / ignore /
ignore-but-report).

Phase 1 scope: this crawl runs synchronously in-process, bounded by
max_pages/max_depth so it fits inside a single request. A background job
queue + persistence layer (for larger, resumable crawls) is a later phase.
See the "Scope notes" in README.md.
"""

import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from modules.auditor import HEADERS as DEFAULT_HEADERS, TIMEOUT, audit_url, fetch_page, safe_get, validate_audit_url
from modules.link_auditor import get_base_domain
from modules.renderer import render_page

# ── User-agent presets (mirrors Semrush/Screaming Frog's UA switcher) ─────────
USER_AGENTS = {
    "default": DEFAULT_HEADERS["User-Agent"],
    "googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "googlebot-mobile": (
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/W.X.Y.Z Mobile Safari/537.36 "
        "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    ),
    "bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
}

ROBOTS_MODES = ("respect", "ignore", "ignore_but_report")
SEED_SOURCES = ("homepage", "sitemap", "url_list")

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


@dataclass
class CrawlConfig:
    seed_url: str
    seed_source: str = "homepage"              # homepage | sitemap | url_list
    url_list: list = field(default_factory=list)          # used when seed_source == "url_list"
    include_patterns: list = field(default_factory=list)  # regex; a URL must match at least one if any are set
    exclude_patterns: list = field(default_factory=list)  # regex; a URL matching any of these is dropped
    max_depth: int = 3
    max_pages: int = 50
    include_subdomains: bool = False
    user_agent: str = "default"                 # key into USER_AGENTS
    robots_mode: str = "respect"                # respect | ignore | ignore_but_report
    crawl_delay: float = 0.0                     # min seconds between requests to the same host
    max_workers: int = 4
    run_full_audit: bool = True                  # False = discovery + status only, no per-page SEO checks
    render_js: bool = False                       # Phase 4: audit the Playwright-rendered DOM instead of raw HTML
    render_timeout_ms: int = 5000                 # per-page render timeout (Screaming Frog's own default)
    max_seconds: float = 0.0                       # wall-clock budget for the whole crawl (0 = unlimited); the
                                                   # Vercel "crawl" action sets this under its maxDuration so one
                                                   # invocation can't overrun the function timeout

    def __post_init__(self):
        if self.seed_source not in SEED_SOURCES:
            raise ValueError(f"seed_source must be one of {SEED_SOURCES}")
        if self.robots_mode not in ROBOTS_MODES:
            raise ValueError(f"robots_mode must be one of {ROBOTS_MODES}")
        if self.user_agent not in USER_AGENTS:
            raise ValueError(f"user_agent must be one of {tuple(USER_AGENTS)}")
        if self.max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        if self.max_pages < 1:
            raise ValueError("max_pages must be >= 1")


def normalize_url(url: str) -> str:
    """Strip fragments and normalize trailing slashes so the same page isn't
    queued twice under two different-looking URLs. Also used by
    worker/crawl_service.py to normalize internal Link.target_url the same
    way Page.url is normalized, so site_audit.py's broken-link matcher keeps
    working on link targets sourced from modules/link_auditor.py."""
    url, _fragment = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.netloc}{path}{query}"


def _in_scope(url: str, seed_domain: str, config: CrawlConfig) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    domain = parsed.netloc.lower()
    base = domain[4:] if domain.startswith("www.") else domain
    if config.include_subdomains:
        if not (base == seed_domain or base.endswith("." + seed_domain)):
            return False
    elif base != seed_domain:
        return False

    if config.exclude_patterns and any(re.search(p, url) for p in config.exclude_patterns):
        return False
    if config.include_patterns and not any(re.search(p, url) for p in config.include_patterns):
        return False
    return True


def _extract_internal_links(soup, base_url: str, seed_domain: str, config: CrawlConfig) -> set:
    links = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        full_url = normalize_url(urljoin(base_url, href))
        if _in_scope(full_url, seed_domain, config):
            links.add(full_url)
    return links


# ── Sitemap-based seeding ──────────────────────────────────────────────────────

def _fetch_sitemap_locs(sitemap_url: str):
    """Return (list of <loc> URLs, is_sitemap_index) for a sitemap URL, or ([], False) on failure.

    SSRF guard: sitemap URLs are attacker-controlled (they come from the
    target's own robots.txt `Sitemap:` directives and from nested
    <sitemapindex> entries), so validate each before fetching to stop a
    malicious sitemap from pointing the crawler at internal hosts.
    """
    ok, _ = validate_audit_url(sitemap_url)
    if not ok:
        return [], False
    try:
        r = safe_get(sitemap_url, headers=DEFAULT_HEADERS, timeout=TIMEOUT, verify=True)
        if r.status_code != 200:
            return [], False
        root_el = ET.fromstring(r.content)
    except Exception:
        return [], False
    locs = [loc.text.strip() for loc in root_el.findall(".//sm:loc", _SITEMAP_NS) if loc.text]
    is_index = root_el.tag.lower().endswith("sitemapindex")
    return locs, is_index


def discover_sitemap_urls(seed_url: str) -> list:
    """Find page URLs via robots.txt `Sitemap:` directives (falling back to
    /sitemap.xml), following one level of sitemap-index nesting."""
    parsed = urlparse(seed_url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    sitemap_urls = []
    try:
        r = safe_get(root + "/robots.txt", headers=DEFAULT_HEADERS, timeout=TIMEOUT, verify=True)
        if r.status_code == 200:
            sitemap_urls = [
                line.split(":", 1)[1].strip()
                for line in r.text.splitlines()
                if line.lower().startswith("sitemap:")
            ]
    except Exception:
        pass
    if not sitemap_urls:
        sitemap_urls = [root + "/sitemap.xml"]

    page_urls = []
    for sitemap_url in sitemap_urls[:5]:
        locs, is_index = _fetch_sitemap_locs(sitemap_url)
        if is_index:
            for sub_sitemap_url in locs[:20]:
                sub_locs, _ = _fetch_sitemap_locs(sub_sitemap_url)
                page_urls.extend(sub_locs)
        else:
            page_urls.extend(locs)
    return page_urls


def _get_seed_urls(config: CrawlConfig) -> list:
    if config.seed_source == "url_list":
        return list(config.url_list) or [config.seed_url]
    if config.seed_source == "sitemap":
        return discover_sitemap_urls(config.seed_url) or [config.seed_url]
    return [config.seed_url]


# ── robots.txt (per-domain, cached for the life of the crawl) ─────────────────

def _get_robots_parser(domain_root: str, headers: dict, cache: dict, lock: threading.Lock) -> RobotFileParser:
    with lock:
        cached = cache.get(domain_root)
    if cached is not None:
        return cached

    rp = RobotFileParser()
    rp.set_url(domain_root + "/robots.txt")
    try:
        r = safe_get(domain_root + "/robots.txt", headers=headers, timeout=TIMEOUT, verify=True)
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except Exception:
        rp.parse([])  # unreachable robots.txt is treated as allow-all

    with lock:
        cache[domain_root] = rp
    return rp


def _robots_allowed(url: str, user_agent: str, cache: dict, lock: threading.Lock, headers: dict):
    parsed = urlparse(url)
    domain_root = f"{parsed.scheme}://{parsed.netloc}"
    rp = _get_robots_parser(domain_root, headers, cache, lock)
    ua_token = "Googlebot" if "googlebot" in user_agent else "*"
    return rp.can_fetch(ua_token, url), rp.crawl_delay(ua_token)


def crawl_site(config: CrawlConfig, progress_callback=None, on_result=None) -> dict:
    """Breadth-first crawl of `config.seed_url`'s domain: discover pages depth by
    depth, run the existing single-page audit engine on each accepted page, and
    return the aggregate result. `progress_callback(pages_done, max_pages)` is
    invoked after each page finishes, if provided. `on_result(url, outcome)` is
    invoked once per processed URL (success, robots-skip, or fetch-error alike)
    with the same `outcome` dict `_process()` produced — the hook a caller uses
    to persist each page as it's crawled instead of waiting for the final
    in-memory return value (see worker/crawl_service.py)."""
    started = datetime.now()

    ok, ssrf_msg = validate_audit_url(config.seed_url)
    if not ok:
        return {
            "seed_url": config.seed_url, "error": ssrf_msg, "pages": [],
            "stats": {}, "started_at": started.isoformat(), "finished_at": started.isoformat(),
        }

    seed_domain = get_base_domain(config.seed_url)
    headers = {**DEFAULT_HEADERS, "User-Agent": USER_AGENTS[config.user_agent]}

    seeds = [normalize_url(u) for u in _get_seed_urls(config)]
    frontier, skipped_scope = [], []
    for u in seeds:
        (frontier if _in_scope(u, seed_domain, config) else skipped_scope).append(u)
    frontier = list(dict.fromkeys(frontier))  # de-dup, keep discovery order

    visited: set = set()
    pages: list = []
    skipped_robots: list = []
    errors: list = []
    robots_cache: dict = {}
    robots_lock = threading.Lock()
    last_request: dict = {}
    throttle_lock = threading.Lock()

    # Phase 4: a small, separate pool for JS rendering — never the main BFS
    # pool (config.max_workers, sized for cheap raw HTTP fetches). Capped at
    # 2 regardless of max_workers, per 01-CRAWLER-ENGINE.md's explicit
    # "2 render workers vs. 20 fetch workers" guidance, since each render is
    # a real Chromium launch (10-50x slower/heavier than a raw GET).
    render_pool = ThreadPoolExecutor(max_workers=2) if config.render_js else None

    def _throttle(url: str, robots_delay):
        host = urlparse(url).netloc
        delay = max(config.crawl_delay, robots_delay or 0)
        if delay <= 0:
            return
        with throttle_lock:
            wait = delay - (time.monotonic() - last_request.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            last_request[host] = time.monotonic()

    def _process(url: str) -> dict:
        allowed, robots_delay = True, None
        if config.robots_mode != "ignore":
            try:
                allowed, robots_delay = _robots_allowed(url, config.user_agent, robots_cache, robots_lock, headers)
            except Exception:
                allowed, robots_delay = True, None
        if not allowed and config.robots_mode == "respect":
            return {"skipped": "robots", "url": url}

        _throttle(url, robots_delay)

        fetch = fetch_page(url)
        if not fetch["success"]:
            return {"error": fetch.get("error", "fetch failed"), "url": url}

        links = _extract_internal_links(fetch["soup"], fetch.get("final_url", url), seed_domain, config)
        page_record = {
            "url": url,
            "final_url": fetch.get("final_url", url),
            "status_code": fetch["status_code"],
            "redirect_count": fetch.get("redirect_count", 0),
            "blocked_by_robots": not allowed,  # only meaningful when robots_mode == "ignore_but_report"
        }
        if config.run_full_audit:
            page_record["audit"] = audit_url(url, check_links=True, prefetched=fetch)

            if render_pool is not None:
                rendered = render_pool.submit(render_page, url, config.render_timeout_ms).result()
                if rendered.get("success"):
                    rendered_audit = audit_url(url, check_links=True, prefetched=rendered)
                    raw_words = (page_record["audit"].get("content") or {}).get("word_count", 0)
                    rendered_words = (rendered_audit.get("content") or {}).get("word_count", 0)
                    page_record["audit"] = rendered_audit
                    page_record["rendered"] = True
                    # A large raw-vs-rendered content gap means crawlers/tools
                    # that don't execute JS will miss real content — the same
                    # signal Screaming Frog's own rendered mode flags.
                    if rendered_words > raw_words * 1.5 and (rendered_words - raw_words) >= 50:
                        rendered_audit.setdefault("all_issues", []).append({
                            "issue": "Significant JS-dependent content",
                            "category": "Technical",
                            "severity": "Warning",
                            "recommendation": (
                                f"Rendered content has {rendered_words} words vs {raw_words} in the raw HTML. "
                                "Crawlers/tools that don't execute JavaScript will miss this content — consider "
                                "server-side rendering or static generation for anything SEO-critical."
                            ),
                            "impact_score": 6,
                            "effort": "High",
                        })
        return {"page": page_record, "links": links}

    timed_out = False
    _crawl_start = time.monotonic()
    try:
        depth = 0
        while frontier and len(visited) < config.max_pages and depth <= config.max_depth:
            # Wall-clock guard: stop cleanly before overrunning the caller's
            # budget (e.g. Vercel's function timeout) rather than being killed
            # mid-request. Returns whatever was crawled so far, flagged.
            if config.max_seconds and (time.monotonic() - _crawl_start) >= config.max_seconds:
                timed_out = True
                break
            remaining = config.max_pages - len(visited)
            batch = [u for u in frontier if u not in visited][:remaining]
            frontier = []
            if not batch:
                break

            with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
                future_map = {executor.submit(_process, u): u for u in batch}
                for future in as_completed(future_map):
                    url = future_map[future]
                    visited.add(url)
                    try:
                        outcome = future.result()
                    except Exception as exc:
                        outcome = {"error": str(exc), "url": url}
                        errors.append({"url": url, "error": str(exc)})
                        if on_result:
                            on_result(url, outcome)
                        continue

                    if outcome.get("skipped") == "robots":
                        skipped_robots.append(url)
                    elif "error" in outcome:
                        errors.append({"url": url, "error": outcome["error"]})
                    else:
                        pages.append(outcome["page"])
                        for link in outcome["links"]:
                            if link not in visited:
                                frontier.append(link)

                    if on_result:
                        on_result(url, outcome)
                    if progress_callback:
                        progress_callback(len(pages), config.max_pages)

            frontier = list(dict.fromkeys(frontier))
            depth += 1
    finally:
        if render_pool is not None:
            render_pool.shutdown(wait=True)

    finished = datetime.now()
    return {
        "seed_url": config.seed_url,
        "config": {
            "seed_source": config.seed_source,
            "max_depth": config.max_depth,
            "max_pages": config.max_pages,
            "include_subdomains": config.include_subdomains,
            "robots_mode": config.robots_mode,
            "user_agent": config.user_agent,
        },
        "pages": pages,
        "skipped_scope": skipped_scope,
        "skipped_robots": skipped_robots,
        "errors": errors,
        "stats": {
            "pages_crawled": len(pages),
            "pages_skipped_robots": len(skipped_robots),
            "pages_skipped_scope": len(skipped_scope),
            "errors": len(errors),
            "depth_reached": depth,
            "timed_out": timed_out,
            "duration_seconds": round((finished - started).total_seconds(), 2),
            "issues_by_severity": dict(Counter(
                issue["severity"]
                for page in pages
                for issue in page.get("audit", {}).get("all_issues", [])
            )),
        },
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
    }
