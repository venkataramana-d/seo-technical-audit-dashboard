"""Site-health & technical SEO checks not covered by modules/advanced_checks.py:
domain age (WHOIS), SSL certificate, DNS health (SPF/DMARC/MX), robots.txt,
sitemap.xml, readability, content freshness, canonical-loop detection,
www/non-www redirect consistency, and HTTP/2 support.

Ported from the standalone Streamlit SEO audit tool's tools/phase1.py, adapted
to this project's {issue, category, severity, recommendation, impact_score,
effort} issue schema and its existing SSRF-safe fetch layer.
"""

import logging
import socket
import ssl
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from modules.auditor import HEADERS, TIMEOUT, _issue, safe_get, validate_audit_url

logger = logging.getLogger(__name__)

_DNS_TTL = 10 * 60
_dns_cache: dict[str, tuple] = {}
_dns_cache_lock = threading.Lock()




def _public_hostname(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.lower()


def _cached_dns(domain: str, kind: str) -> list:
    """Cached DNS lookup. kind: 'txt' | 'dmarc' | 'mx'."""
    import time as _time

    key = f"{domain}|{kind}"
    with _dns_cache_lock:
        cached = _dns_cache.get(key)
    if cached and (_time.time() - cached[1]) < _DNS_TTL:
        val = cached[0]
        if isinstance(val, Exception):
            raise val
        return val

    import dns.resolver as _dns

    try:
        if kind == "txt":
            val = [r.to_text().strip('"') for r in _dns.resolve(domain, "TXT")]
        elif kind == "dmarc":
            val = [r.to_text().strip('"') for r in _dns.resolve(f"_dmarc.{domain}", "TXT")]
        elif kind == "mx":
            val = [str(r.exchange) for r in _dns.resolve(domain, "MX")]
        else:
            raise ValueError(f"Unknown DNS lookup kind: {kind}")
    except Exception as exc:
        with _dns_cache_lock:
            _dns_cache[key] = (exc, _time.time())
        raise
    with _dns_cache_lock:
        _dns_cache[key] = (val, _time.time())
    return val


# ════════════════════════════════════════════════════════════════════════════
# robots.txt
# ════════════════════════════════════════════════════════════════════════════

def check_robots_txt(url: str) -> dict:
    issues = []
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = base + "/robots.txt"

    try:
        r = safe_get(robots_url, headers=HEADERS, timeout=TIMEOUT, verify=True)
    except (requests.RequestException, OSError) as exc:
        logger.warning("check_robots_txt fetch failed for %s: %s", robots_url, exc)
        return {"robots_url": robots_url, "exists": False, "allowed": None, "issues": issues}

    if r.status_code == 404:
        return {
            "robots_url": robots_url, "exists": False, "allowed": True,
            "googlebot_allowed": True, "crawl_delay": None, "issues": issues,
        }
    if r.status_code != 200:
        issues.append(_issue(f"robots.txt Returned HTTP {r.status_code}", "Site Health", "Warning",
            "Ensure robots.txt returns a 200 status so crawlers can read your crawl rules.",
            impact_score=4, effort="Low",
            affected=[{"value": robots_url, "detail": f"Returned HTTP {r.status_code}"}]))
        return {"robots_url": robots_url, "exists": False, "allowed": None, "issues": issues}

    rp = RobotFileParser()
    rp.set_url(robots_url)
    rp.parse(r.text.splitlines())
    allowed = rp.can_fetch("*", url)
    googlebot_allowed = rp.can_fetch("Googlebot", url)
    crawl_delay = rp.crawl_delay("*")

    if not allowed or not googlebot_allowed:
        issues.append(_issue("Page Blocked by robots.txt", "Site Health", "Critical",
            "Update robots.txt Disallow rules if this page should be crawled and indexed.",
            impact_score=10, effort="Low",
            affected=[{"value": robots_url, "detail": f"Disallows: {url}"}]))

    return {
        "robots_url": robots_url, "exists": True, "allowed": allowed,
        "googlebot_allowed": googlebot_allowed, "crawl_delay": crawl_delay, "issues": issues,
    }


# ════════════════════════════════════════════════════════════════════════════
# sitemap.xml
# ════════════════════════════════════════════════════════════════════════════

def check_sitemap(url: str) -> dict:
    issues = []
    parsed = urlparse(url)
    root_base = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = f"{root_base}/sitemap.xml"

    # Probe the two conventional locations. Yoast and many other CMSs serve the
    # sitemap only at /sitemap_index.xml, so probing /sitemap.xml alone reported
    # "Sitemap.xml Not Found" on sites that DO have a valid sitemap. (A robots.txt
    # `Sitemap:` directive can declare an arbitrary path too; a full resolver
    # lives in modules/sitemap_extractor.py - this lightweight health check just
    # covers the two standard paths.)
    r = None
    fetch_failed = False
    for candidate in (sitemap_url, f"{root_base}/sitemap_index.xml"):
        try:
            resp = safe_get(candidate, headers=HEADERS, timeout=TIMEOUT, verify=True)
        except (requests.RequestException, OSError) as exc:
            logger.warning("check_sitemap fetch failed for %s: %s", candidate, exc)
            fetch_failed = True
            continue
        if resp.status_code == 200:
            r, sitemap_url = resp, candidate
            break

    if r is None:
        # Only flag "Not Found" when a location was actually reached and returned
        # non-200. A transient fetch failure (timeout/SSL) is inconclusive and,
        # like check_robots_txt's exception path, degrades with no scored issue
        # rather than asserting the sitemap is missing.
        if not fetch_failed:
            issues.append(_issue("Sitemap Not Found", "Site Health", "Warning",
                "Add a sitemap.xml (or sitemap_index.xml) at the site root and submit it in Search Console to aid discovery.",
                impact_score=4, effort="Low",
                affected=[
                    {"value": sitemap_url, "detail": "No sitemap at this location"},
                    {"value": f"{root_base}/sitemap_index.xml", "detail": "No sitemap at this location"},
                ]))
        return {"sitemap_url": sitemap_url, "exists": False, "url_count": 0, "issues": issues}

    urls = []
    malformed = False
    try:
        root = ET.fromstring(r.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [loc.text.strip() for loc in root.findall(".//sm:loc", ns) if loc.text]
    except ET.ParseError:
        try:
            from lxml import etree as lxml_et
            p = lxml_et.XMLParser(recover=True, resolve_entities=False, no_network=True)
            root_lxml = lxml_et.fromstring(r.content, parser=p)
            urls = [loc.text.strip() for loc in
                    root_lxml.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc") if loc.text]
            malformed = True
        except Exception as exc:
            logger.warning("check_sitemap lxml recovery failed for %s: %s", sitemap_url, exc)
            malformed = True

    if malformed:
        issues.append(_issue("Sitemap XML Is Malformed", "Site Health", "Warning",
            "Fix XML syntax errors in sitemap.xml so search engines can parse it reliably.",
            impact_score=5, effort="Medium",
            affected=[{"value": sitemap_url, "detail": "XML parse error (recovered via lenient parser)"}]))

    duplicates = len(urls) - len(set(urls))
    if duplicates:
        seen = set()
        dup_values = []
        for u in urls:
            if u in seen and u not in dup_values:
                dup_values.append(u)
            seen.add(u)
        issues.append(_issue(f"{duplicates} Duplicate URLs in Sitemap", "Site Health", "Low",
            "Remove duplicate <loc> entries from the sitemap.",
            impact_score=2, effort="Low",
            affected=[{"value": u, "detail": "Appears more than once in sitemap"}
                      for u in dup_values[:50]]))

    if len(urls) > 50000:
        issues.append(_issue("Sitemap Exceeds 50,000 URL Limit", "Site Health", "High",
            "Split the sitemap into multiple files referenced by a sitemap index.",
            impact_score=6, effort="Medium",
            affected=[{"value": sitemap_url, "detail": f"Contains {len(urls):,} URLs (limit 50,000)"}]))

    return {
        "sitemap_url": sitemap_url, "exists": True, "url_count": len(urls),
        "duplicate_count": duplicates, "issues": issues,
    }


# ════════════════════════════════════════════════════════════════════════════
# Domain age (WHOIS)
# ════════════════════════════════════════════════════════════════════════════

def check_domain_age(url: str) -> dict:
    try:
        import whois as _whois
    except ImportError:
        return {"available": False, "issues": []}

    domain = _public_hostname(url)
    try:
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            w = ex.submit(_whois.whois, domain).result(timeout=10)
        finally:
            ex.shutdown(wait=False)
        created = w.get("creation_date")
        if isinstance(created, list):
            created = created[0]
        if not created:
            return {"available": True, "age_years": None, "issues": []}

        now = datetime.now(timezone.utc) if getattr(created, "tzinfo", None) else datetime.now()
        age_days = (now - created).days
        age_years = round(age_days / 365, 1)

        issues = []
        if age_years < 0.5:
            created_str = created.strftime("%Y-%m-%d") if hasattr(created, "strftime") else str(created)
            issues.append(_issue(f"Very New Domain ({age_years} years old)", "Site Health", "Low",
                "New domains take time to build trust with search engines: this is informational, not a fix.",
                impact_score=2, effort="Low",
                affected=[{"value": domain, "detail": f"Created {created_str} ({age_years} years / {age_days} days old)"}]))

        return {
            "available": True, "age_years": age_years, "age_days": age_days,
            "created": created.strftime("%Y-%m-%d") if hasattr(created, "strftime") else str(created),
            "registrar": str(w.get("registrar") or ""), "issues": issues,
        }
    except Exception as exc:
        logger.warning("check_domain_age failed for %s: %s", domain, exc)
        return {"available": False, "issues": []}


# ════════════════════════════════════════════════════════════════════════════
# SSL certificate
# ════════════════════════════════════════════════════════════════════════════

def check_ssl(url: str) -> dict:
    issues = []
    if not url.startswith("https"):
        issues.append(_issue("Page Not Served Over HTTPS", "Site Health", "Critical",
            "Migrate to HTTPS and obtain a valid SSL certificate.",
            impact_score=10, effort="High",
            affected=[{"value": url, "detail": "Served over plain HTTP"}]))
        return {"valid": False, "issues": issues}

    domain = urlparse(url).netloc.split(":")[0]
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.create_connection((domain, 443), timeout=8),
                              server_hostname=domain) as s:
            cert = s.getpeercert() or {}
        expiry_raw = str(cert.get("notAfter", ""))
        expiry = datetime.strptime(expiry_raw, "%b %d %H:%M:%S %Y %Z") if expiry_raw else None
        days_left = (expiry - datetime.now(timezone.utc).replace(tzinfo=None)).days if expiry else None

        expiry_str = expiry.strftime("%Y-%m-%d") if expiry else expiry_raw
        if days_left is not None and days_left < 14:
            issues.append(_issue(f"SSL Certificate Expires in {days_left} Days", "Site Health", "Critical",
                "Renew the SSL certificate immediately to avoid a browser security warning.",
                impact_score=10, effort="Low",
                affected=[{"value": domain, "detail": f"Certificate expires {expiry_str} ({days_left} days left)"}]))
        elif days_left is not None and days_left < 30:
            issues.append(_issue(f"SSL Certificate Expires in {days_left} Days", "Site Health", "Warning",
                "Renew the SSL certificate soon.",
                impact_score=5, effort="Low",
                affected=[{"value": domain, "detail": f"Certificate expires {expiry_str} ({days_left} days left)"}]))

        return {
            "valid": True, "expiry": expiry.strftime("%Y-%m-%d") if expiry else None,
            "days_left": days_left, "issues": issues,
        }
    except ssl.SSLCertVerificationError as exc:
        issues.append(_issue("SSL Certificate Invalid", "Site Health", "Critical",
            f"Fix the SSL certificate: verification failed: {exc}",
            impact_score=10, effort="High",
            affected=[{"value": domain, "detail": f"Verification failed: {exc}"}]))
        return {"valid": False, "issues": issues}
    except (OSError, ValueError) as exc:
        logger.warning("check_ssl failed for %s: %s", domain, exc)
        return {"valid": None, "issues": issues}


# ════════════════════════════════════════════════════════════════════════════
# HTTPS enforcement: does the http:// origin redirect to https://?
# ════════════════════════════════════════════════════════════════════════════

def check_https_enforcement(url: str) -> dict:
    """Verify the http:// version of this host redirects to https://.

    Distinct from check_ssl (which validates the certificate on the URL as
    requested): this confirms visitors landing on the insecure origin are
    forced onto HTTPS rather than being served content over plain HTTP.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        # Already flagged elsewhere (check_ssl / analyze_url_structure): avoid double-counting.
        return {"enforced": None, "issues": []}

    http_url = f"http://{parsed.netloc}{parsed.path or '/'}"
    try:
        r = safe_get(http_url, headers=HEADERS, timeout=8, verify=True)
    except (requests.RequestException, OSError) as exc:
        logger.warning("check_https_enforcement fetch failed for %s: %s", http_url, exc)
        return {"enforced": None, "issues": []}

    final_scheme = urlparse(r.url).scheme
    if final_scheme == "https":
        return {"enforced": True, "issues": []}

    return {
        "enforced": False,
        "issues": [_issue(
            "HTTP Does Not Redirect to HTTPS", "Site Health", "Critical",
            f"Add a server-level redirect so http://{parsed.netloc} forces HTTPS: "
            "visitors and crawlers can currently reach an insecure version of this site.",
            impact_score=9, effort="Low",
            affected=[{"value": http_url, "detail": f"Resolves to {final_scheme}://{urlparse(r.url).netloc} (not HTTPS)"}])],
    }


# ════════════════════════════════════════════════════════════════════════════
# DNS health: SPF / DMARC / MX
# ════════════════════════════════════════════════════════════════════════════

def check_dns_health(url: str) -> dict:
    try:
        import dns.resolver  # noqa: F401 (availability check only)
    except ImportError:
        return {"available": False, "issues": []}

    domain = _public_hostname(url)
    issues = []

    def _lookup(kind):
        try:
            return _cached_dns(domain, kind)
        except Exception as exc:
            logger.warning("DNS %s lookup failed for %s: %s", kind, domain, exc)
            return []

    with ThreadPoolExecutor(max_workers=3) as ex:
        spf_f = ex.submit(_lookup, "txt")
        dmarc_f = ex.submit(_lookup, "dmarc")
        mx_f = ex.submit(_lookup, "mx")
        spf_records = [r for r in spf_f.result() if r.startswith("v=spf1")]
        dmarc_records = [r for r in dmarc_f.result() if r.startswith("v=DMARC1")]
        mx = mx_f.result()

    # SPF / DMARC / MX are email-deliverability DNS records, not search-ranking
    # signals: a missing SPF record does not hurt how a page ranks. We therefore
    # collect the records for informational display (surfaced as "info" checklist
    # items) but intentionally emit NO scored issues here, so email posture never
    # penalizes a site's SEO score. `issues` stays empty by design.

    return {
        "available": True,
        "spf": spf_records[0][:120] if spf_records else None,
        "dmarc": dmarc_records[0][:120] if dmarc_records else None,
        "mx": mx[:5],
        "informational": True,
        "issues": issues,
    }


# ════════════════════════════════════════════════════════════════════════════
# Readability (Flesch-Kincaid via textstat): reuses already-parsed page text
# ════════════════════════════════════════════════════════════════════════════

def check_readability(text: str) -> dict:
    try:
        import textstat
    except ImportError:
        return {"available": False, "issues": []}

    if len(text.split()) < 50:
        return {"available": True, "issues": []}

    issues = []
    fk_grade = round(textstat.textstat.flesch_kincaid_grade(text), 1)
    ease = round(textstat.textstat.flesch_reading_ease(text), 1)

    # A Flesch-Kincaid grade above ~19 is not real reading difficulty - the
    # scale tops out around 18 (post-graduate). A higher number means the input
    # is NOT prose: nav labels, button text, and marketing fragments without
    # sentence punctuation inflate the average-sentence-length term. Treat that
    # as a measurement artifact and don't flag it (this removed the implausible
    # "Grade 22" false positive on nav-heavy/landing pages). Genuinely hard prose
    # still falls in the plausible 14-19 band and is flagged.
    if fk_grade > 19:
        return {"available": True, "fk_grade": fk_grade, "reading_ease": ease, "issues": []}

    if fk_grade > 14:
        issues.append(_issue(f"Difficult Readability (Grade {fk_grade})", "Content", "Warning",
            "Simplify sentence structure and vocabulary to lower the reading grade level.",
            impact_score=4, effort="Medium",
            affected=[{"value": f"Flesch-Kincaid grade {fk_grade}", "detail": f"Reading ease {ease}"}]))
    elif fk_grade > 10:
        issues.append(_issue(f"Above-Average Reading Difficulty (Grade {fk_grade})", "Content", "Low",
            "Consider shorter sentences to make content accessible to a broader audience.",
            impact_score=2, effort="Medium",
            affected=[{"value": f"Flesch-Kincaid grade {fk_grade}", "detail": f"Reading ease {ease}"}]))

    return {"available": True, "fk_grade": fk_grade, "reading_ease": ease, "issues": issues}


# ════════════════════════════════════════════════════════════════════════════
# Content freshness: reuses already-fetched headers + soup
# ════════════════════════════════════════════════════════════════════════════

def check_content_freshness(http_headers: dict, soup) -> dict:
    h = {k.lower(): v for k, v in (http_headers or {}).items()}
    last_modified = h.get("last-modified")

    visible_date = None
    if soup:
        for selector, attr in [
            ("meta[property='article:modified_time']", "content"),
            ("meta[property='article:published_time']", "content"),
            ("time[datetime]", "datetime"),
            ("meta[name='date']", "content"),
        ]:
            el = soup.select_one(selector)
            if el and el.get(attr):
                visible_date = el.get(attr)
                break

    raw_date = visible_date or last_modified
    if not raw_date:
        # The majority of healthy pages expose neither an article:modified_time
        # meta nor a Last-Modified header (normal for dynamic/CDN-served pages).
        # Absence of an OPTIONAL freshness signal is not a defect, so degrade
        # gracefully (available: False, no scored issue) like every other
        # optional check here - emitting a scored "No Content-Freshness Signals"
        # issue flagged nearly every page.
        return {"available": False, "issues": []}

    parsed_dt = None
    if visible_date:
        try:
            parsed_dt = datetime.fromisoformat(visible_date.replace("Z", "+00:00"))
        except ValueError:
            parsed_dt = None
    if parsed_dt is None and last_modified:
        try:
            parsed_dt = parsedate_to_datetime(last_modified)
        except (TypeError, ValueError):
            parsed_dt = None

    if parsed_dt is None:
        return {"available": True, "raw_date": raw_date, "issues": []}

    now = datetime.now(timezone.utc)
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
    age_days = (now - parsed_dt).days

    issues = []
    if age_days > 730:
        issues.append(_issue(f"Content Is ~{age_days // 30} Months Old", "Content", "Warning",
            "Refresh this content: search engines favour recently updated pages for time-sensitive queries.",
            impact_score=4, effort="Medium",
            affected=[{"value": str(raw_date), "detail": f"Last modified ~{age_days} days ago"}]))

    return {"available": True, "raw_date": raw_date, "age_days": age_days, "issues": issues}


# ════════════════════════════════════════════════════════════════════════════
# Canonical loop / chain detection
# ════════════════════════════════════════════════════════════════════════════

def check_canonical_loop(url: str, soup) -> dict:
    MAX_HOPS = 5
    visited = [url]
    verified_hops = 0  # canonical targets we actually fetched + re-parsed
    current_url, current_soup = url, soup

    for hop in range(MAX_HOPS):
        tag = current_soup.find("link", rel="canonical") if current_soup else None
        if not tag or not tag.get("href"):
            return {"chain": visited, "issues": []}

        canon_url = tag["href"].strip()
        if not canon_url.startswith("http"):
            canon_url = urljoin(current_url, canon_url)

        if canon_url == current_url:
            return {"chain": visited, "issues": []}  # self-referencing, fine

        if canon_url in visited:
            chain = " → ".join(visited + [canon_url])
            return {"chain": visited + [canon_url], "issues": [_issue(
                "Canonical Loop Detected", "Canonical", "Critical",
                f"Break the canonical loop, chain: {chain}",
                impact_score=8, effort="Medium",
                affected=[{"value": chain, "detail": f"Loops back to {canon_url}"}])]}

        visited.append(canon_url)
        try:
            ok, _ = validate_audit_url(canon_url)
            if not ok:
                break
            r = safe_get(canon_url, headers=HEADERS, timeout=TIMEOUT, verify=True)
            from bs4 import BeautifulSoup
            current_soup = BeautifulSoup(r.content, "lxml")
            current_url = canon_url
            verified_hops += 1
        except (requests.RequestException, OSError):
            break

    # Only warn about a real multi-hop chain, i.e. one where we CONFIRMED the
    # canonical target has its own differing canonical (verified_hops >= 1). A
    # single cross-URL canonical (A -> B) whose target B we couldn't fetch
    # (SSRF-blocked, timeout) is not a chain - the prior code emitted a bogus
    # "Canonical Chain (2 Hops)" for that normal, common case.
    if len(visited) > 1 and verified_hops >= 1:
        chain = " → ".join(visited)
        return {"chain": visited, "issues": [_issue(
            f"Canonical Chain ({len(visited)} Hops)", "Canonical", "Warning",
            f"Point the canonical tag directly at the final URL to avoid a redirect chain: {chain}",
            impact_score=5, effort="Medium",
            affected=[{"value": chain, "detail": f"{len(visited)} canonical hops"}])]}

    return {"chain": visited, "issues": []}


# ════════════════════════════════════════════════════════════════════════════
# www / non-www redirect consistency
# ════════════════════════════════════════════════════════════════════════════

def check_www_redirect(url: str) -> dict:
    parsed = urlparse(url)
    domain = parsed.netloc
    has_www = domain.startswith("www.")

    # The www/non-www consolidation check only makes sense for an apex host
    # (example.com) or its www. variant (www.example.com). For a deeper subdomain
    # like blog.example.com the "alt" would be www.blog.example.com, which almost
    # never exists, so the check reported a bogus "www.blog.example.com Does Not
    # Resolve" warning on a host that resolves perfectly. Skip those.
    host_no_www = domain[4:] if has_www else domain
    if not has_www and host_no_www.count(".") != 1:
        # Non-www AND not a plain 2-label apex (e.g. blog.example.com,
        # example.co.uk) - can't reliably derive the www counterpart, so skip
        # rather than probe a host that likely doesn't exist.
        return {"consolidated": True, "issues": []}

    alt_domain = domain[4:] if has_www else f"www.{domain}"
    alt_url = f"{parsed.scheme}://{alt_domain}/"

    try:
        r = safe_get(alt_url, headers=HEADERS, timeout=8, verify=True)
    except (requests.RequestException, OSError):
        # A timeout, SSL error, or connection blip does NOT prove the variant
        # doesn't resolve (the domain may resolve fine but be slow / have a cert
        # quirk). Only a real DNS-resolution failure would, and we can't cleanly
        # distinguish it here, so degrade gracefully with no scored issue rather
        # than assert a claim ("Does Not Resolve") that may be false.
        return {"consolidated": True, "issues": []}

    final_netloc = urlparse(r.url).netloc
    if final_netloc == domain:
        return {"consolidated": True, "issues": []}
    if final_netloc == alt_domain:
        return {"consolidated": False, "issues": [_issue(
            f"{alt_url} Resolves Independently", "Site Health", "Warning",
            "Redirect the www/non-www variant to a single canonical host to avoid duplicate-content risk.",
            impact_score=5, effort="Medium",
            affected=[{"value": alt_domain, "detail": f"Serves content independently of {domain} instead of redirecting to it"}])]}
    return {"consolidated": True, "issues": []}


# ════════════════════════════════════════════════════════════════════════════
# HTTP/2 support
# ════════════════════════════════════════════════════════════════════════════

def check_http2(url: str) -> dict:
    try:
        import httpx
    except ImportError:
        return {"available": False, "issues": []}

    try:
        ok, _ = validate_audit_url(url)
        if not ok:
            return {"available": False, "issues": []}
        with httpx.Client(http2=True, verify=True, timeout=10, follow_redirects=True) as client:
            resp = client.get(url, headers=HEADERS)
            version = resp.http_version
        is_modern = version in ("HTTP/2", "HTTP/3")
        issues = []
        if not is_modern:
            issues.append(_issue(f"HTTP/2 Not Detected ({version})", "Site Health", "Low",
                "Upgrade the server/CDN to support HTTP/2 or HTTP/3 for faster multiplexed page loads.",
                impact_score=3, effort="Medium",
                affected=[{"value": url, "detail": f"Negotiated {version}"}]))
        return {"available": True, "http_version": version, "issues": issues}
    except Exception as exc:
        logger.warning("check_http2 failed for %s: %s", url, exc)
        return {"available": False, "issues": []}


# ════════════════════════════════════════════════════════════════════════════
# Aggregate entry point: run every site-health check concurrently
# ════════════════════════════════════════════════════════════════════════════

# Which site-health checks are domain-level (identical for every page on a
# domain, so they can be computed once and reused across a same-domain crawl)
# vs. page-level (depend on the specific page's text/soup/headers).
_DOMAIN_LEVEL_CHECKS = (
    "robots", "sitemap", "domain_age", "ssl", "https_enforcement",
    "dns", "www_redirect", "http2",
)
_PAGE_LEVEL_CHECKS = ("readability", "content_freshness", "canonical_loop")


def analyze_domain_health(url: str) -> dict:
    """Run only the domain-level site-health checks (robots, sitemap, WHOIS,
    SSL, HTTPS enforcement, DNS, www-redirect, HTTP/2). These are identical for
    every page on a domain, so a crawl can compute them once per domain and
    reuse them (see api/site-health.py and the client-side cache) instead of
    re-fetching WHOIS/DNS/SSL etc. for every page (Phase 2, PROJECT_LOG)."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            "robots": ex.submit(check_robots_txt, url),
            "sitemap": ex.submit(check_sitemap, url),
            "domain_age": ex.submit(check_domain_age, url),
            "ssl": ex.submit(check_ssl, url),
            "https_enforcement": ex.submit(check_https_enforcement, url),
            "dns": ex.submit(check_dns_health, url),
            "www_redirect": ex.submit(check_www_redirect, url),
            "http2": ex.submit(check_http2, url),
        }
        results = {}
        for key, fut in futures.items():
            try:
                results[key] = fut.result(timeout=25)
            except Exception as exc:
                logger.warning("domain_health check %s failed for %s: %s", key, url, exc)
                results[key] = {"issues": []}
    return results


def _analyze_page_health(soup=None, http_headers=None, page_text: str = "", url: str = "") -> dict:
    """The page-level site-health checks (readability, content freshness,
    canonical loop) that must run per page even when domain health is reused."""
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {
            "readability": ex.submit(check_readability, page_text),
            "content_freshness": ex.submit(check_content_freshness, http_headers, soup),
            "canonical_loop": ex.submit(check_canonical_loop, url, soup),
        }
        results = {}
        for key, fut in futures.items():
            try:
                results[key] = fut.result(timeout=25)
            except Exception as exc:
                logger.warning("page_health check %s failed for %s: %s", key, url, exc)
                results[key] = {"issues": []}
    return results


def analyze_site_health(url: str, soup=None, http_headers=None, page_text: str = "",
                        prefetched_domain_health: dict | None = None) -> dict:
    """Run all site-health checks and merge into one result dict.

    If `prefetched_domain_health` is provided (the output of
    `analyze_domain_health` for this URL's domain), the expensive domain-level
    checks are reused instead of re-run, and only the page-level checks
    execute. Otherwise all checks run (the single-URL path).
    """
    domain = prefetched_domain_health if prefetched_domain_health is not None else analyze_domain_health(url)
    page = _analyze_page_health(soup=soup, http_headers=http_headers, page_text=page_text, url=url)
    results = {**domain, **page}

    all_issues = []
    for key in (*_DOMAIN_LEVEL_CHECKS, *_PAGE_LEVEL_CHECKS):
        all_issues.extend(results.get(key, {}).get("issues", []))

    results["issues"] = all_issues
    return results
