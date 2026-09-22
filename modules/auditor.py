"""Core URL audit engine: fetches pages and runs all SEO checks."""

import ipaddress
import re
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse, urljoin

import urllib3
import requests
from bs4 import BeautifulSoup

# Suppress only SSL/TLS InsecureRequestWarning from urllib3 (verify=False fallback)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── SSRF protection ────────────────────────────────────────────────────────────
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


class BlockedURLError(requests.RequestException):
    """A URL (or one of its redirect targets) failed SSRF validation.

    Subclasses requests.RequestException so existing `except requests.RequestException`
    handlers treat a blocked URL as a clean fetch failure rather than crashing.
    """


def _ip_is_blocked(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def validate_audit_url(url: str) -> tuple[bool, str]:
    """Block SSRF attempts: private/reserved IPs, loopback, link-local, and
    hostnames that *resolve* to any of those (e.g. a public-looking domain
    whose DNS points at 169.254.169.254 or an internal host).

    Note: this is not fully DNS-rebinding-proof (that would require pinning the
    resolved IP through the socket connection); resolution failures degrade
    gracefully to "allow" so a transient DNS hiccup or an offline unit-test
    environment doesn't reject a legitimate URL. Combined with per-hop redirect
    re-validation in `safe_get`, it blocks the realistic metadata/internal-host
    attacks.
    """
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, "Only http:// and https:// URLs are supported."
        host = (p.hostname or "").lower()
        if not host:
            return False, "URL has no hostname."
        if host in _BLOCKED_HOSTS:
            return False, "Loopback/local addresses are not allowed."
        # Literal IP: check directly, no DNS needed.
        try:
            ipaddress.ip_address(host)
            if _ip_is_blocked(host):
                return False, "Private or reserved IP addresses are not allowed."
            return True, ""
        except ValueError:
            pass  # it's a hostname, resolve it below
        # Hostname: reject if any resolved address is private/reserved.
        try:
            infos = socket.getaddrinfo(host, None)
        except (socket.gaierror, OSError, UnicodeError):
            return True, ""  # can't resolve; the fetch itself will fail/connect publicly
        for info in infos:
            if _ip_is_blocked(info[4][0]):
                return False, "Hostname resolves to a private or reserved IP address."
        return True, ""
    except Exception:
        return False, "Invalid URL format."


def safe_request(method, url: str, *, max_redirects: int = 10, **kwargs):
    """SSRF-safe fetch: follows redirects manually, re-validating the initial
    URL and every hop with `validate_audit_url` before it's requested, so a
    public URL that 301s to an internal/metadata host is blocked mid-chain
    instead of being fetched. `method` is any `requests`-shaped callable
    (`requests.get`, `requests.head`, or a `requests.Session`'s bound
    `.get`/`.head`), so callers that need connection reuse (a `Session`) or a
    HEAD-first strategy (image/link size- and reachability-checks) can share
    this instead of each hand-rolling their own redirect loop. Returns the
    final `requests.Response` with `.history` populated.

    Raises `BlockedURLError` if any hop fails validation, and
    `requests.TooManyRedirects` past `max_redirects`.
    """
    kwargs.setdefault("headers", HEADERS)
    kwargs.setdefault("timeout", TIMEOUT)
    kwargs["allow_redirects"] = False
    current = url
    history = []
    for _ in range(max_redirects + 1):
        ok, msg = validate_audit_url(current)
        if not ok:
            raise BlockedURLError(msg)
        resp = method(current, **kwargs)
        if resp.is_redirect and resp.headers.get("Location"):
            history.append(resp)
            current = urljoin(current, resp.headers["Location"])
            continue
        resp.history = history
        return resp
    raise requests.TooManyRedirects(f"Exceeded {max_redirects} redirects")


def safe_get(url: str, *, max_redirects: int = 10, **kwargs):
    """SSRF-safe `requests.get`; see `safe_request`."""
    return safe_request(requests.get, url, max_redirects=max_redirects, **kwargs)


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

TIMEOUT = 20
MAX_TITLE_LEN   = 60
MIN_TITLE_LEN   = 30
MAX_DESC_LEN    = 160
MIN_DESC_LEN    = 150
THIN_THRESHOLD  = 300
SLOW_THRESHOLD  = 3.0   # seconds


def _issue(issue, category, severity, recommendation, impact_score=5, effort="Medium", affected=None):
    """Build one issue. `affected` is the list of exact offending elements (the
    "WHERE"): each item is {"value": <the element, e.g. an image src / link
    target / heading text>, "detail": <what's wrong with it, optional>}. This is
    what lets the UI point the user straight at the specific image/link/element,
    not just say "N pages have X"."""
    return {
        "issue": issue,
        "category": category,
        "severity": severity,
        "recommendation": recommendation,
        "impact_score": impact_score,
        "effort": effort,
        "affected": affected or [],
    }


# Defaults used to backfill issues that some modules build inline WITHOUT going
# through `_issue()` (e.g. blog_auditor), so they arrive missing impact_score /
# effort. Without this, worstIssue()/get_top_issues_by_impact rank a
# High-severity issue with a missing impact_score below a Low with impact 2, and
# the fix-effort chips undercount. Keyed by severity.
_DEFAULT_IMPACT_BY_SEVERITY = {"Critical": 9, "High": 7, "Medium": 5, "Warning": 4, "Low": 2}
_DEFAULT_EFFORT_BY_SEVERITY = {
    "Critical": "High", "High": "High", "Medium": "Medium", "Warning": "Medium", "Low": "Low",
}


def _normalize_issues(issues):
    """Guarantee every issue dict has a numeric `impact_score` and an `effort`,
    derived from severity when absent/None, so no downstream sort or fix-effort
    chip hits a missing value. Mutates + returns the list."""
    for i in issues:
        sev = i.get("severity", "Low")
        if not isinstance(i.get("impact_score"), (int, float)):
            i["impact_score"] = _DEFAULT_IMPACT_BY_SEVERITY.get(sev, 3)
        if not i.get("effort"):
            i["effort"] = _DEFAULT_EFFORT_BY_SEVERITY.get(sev, "Medium")
    return issues


def fetch_page(url):
    """Fetch URL with proper encoding detection and error handling.

    Uses safe_get so redirect targets are SSRF-validated per hop (a public URL
    that redirects to an internal/metadata host is blocked mid-chain).
    """
    try:
        resp = safe_get(url, verify=True)
        # Use detected encoding, fall back to apparent then utf-8
        if resp.encoding and resp.encoding.lower() not in ("utf-8", "utf8"):
            try:
                text = resp.content.decode(resp.encoding, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = resp.content.decode("utf-8", errors="replace")
        else:
            text = resp.content.decode("utf-8", errors="replace")

        soup = BeautifulSoup(text, "lxml")
        return {
            "success": True,
            "status_code": resp.status_code,
            "final_url": resp.url,
            "redirect_count": len(resp.history),
            "redirect_history": [r.url for r in resp.history],
            "content_type": resp.headers.get("Content-Type", ""),
            "soup": soup,
            "html": text,
            "response_time": resp.elapsed.total_seconds(),
            "http_headers": dict(resp.headers),
            "page_size_bytes": len(resp.content),
        }
    except requests.exceptions.SSLError:
        try:
            resp = safe_get(url, verify=False)
            text = resp.content.decode("utf-8", errors="replace")
            soup = BeautifulSoup(text, "lxml")
            return {
                "success": True,
                "status_code": resp.status_code,
                "final_url": resp.url,
                "redirect_count": len(resp.history),
                "redirect_history": [r.url for r in resp.history],
                "content_type": resp.headers.get("Content-Type", ""),
                "soup": soup,
                "html": text,
                "response_time": resp.elapsed.total_seconds(),
                "ssl_warning": True,
                "http_headers": dict(resp.headers),
                "page_size_bytes": len(resp.content),
            }
        except Exception as e:
            return {"success": False, "error": f"SSL Error: {e}", "status_code": 0}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timed out (20s). Check the URL is accessible.", "status_code": 0}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Connection failed. Verify the URL and network access.", "status_code": 0}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {type(e).__name__}: {e}", "status_code": 0}


def analyze_metadata(soup, url):
    issues = []

    # Title
    title_tag = soup.find("title")
    title = title_tag.get_text().strip() if title_tag else ""
    title_len = len(title)

    if not title:
        issues.append(_issue("Missing Meta Title", "Metadata", "Critical",
            "Add a unique, descriptive meta title (30–60 chars) containing your primary keyword.",
            impact_score=10, effort="Low"))
    elif title_len < MIN_TITLE_LEN:
        issues.append(_issue(f"Meta Title Too Short ({title_len} chars)", "Metadata", "Notice",
            f"Expand the meta title to at least {MIN_TITLE_LEN} characters for better SERP visibility.",
            impact_score=6, effort="Low",
            affected=[{"value": title, "detail": f"{title_len} chars"}]))
    elif title_len > MAX_TITLE_LEN:
        issues.append(_issue(f"Meta Title Too Long ({title_len} chars)", "Metadata", "Notice",
            f"Shorten meta title to under {MAX_TITLE_LEN} characters to avoid SERP truncation (pixel-width proxy).",
            impact_score=6, effort="Low",
            affected=[{"value": title, "detail": f"{title_len} chars"}]))

    # Description
    desc_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    description = desc_tag.get("content", "").strip() if desc_tag else ""
    desc_len = len(description)

    if not description:
        issues.append(_issue("Missing Meta Description", "Metadata", "Warning",
            "Add a compelling meta description (150–160 chars) with a clear call to action.",
            impact_score=9, effort="Low"))
    elif desc_len > MAX_DESC_LEN:
        issues.append(_issue(f"Meta Description Too Long ({desc_len} chars)", "Metadata", "Warning",
            f"Shorten description to under {MAX_DESC_LEN} characters.",
            impact_score=5, effort="Low",
            affected=[{"value": description, "detail": f"{desc_len} chars"}]))

    # OG tags
    og_title = soup.find("meta", property="og:title")
    og_desc  = soup.find("meta", property="og:description")
    og_image = soup.find("meta", property="og:image")

    missing_og = []
    if not og_title:  missing_og.append("og:title")
    if not og_desc:   missing_og.append("og:description")
    if not og_image:  missing_og.append("og:image")
    if missing_og:
        issues.append(_issue(f"Missing Open Graph Tags: {', '.join(missing_og)}", "Metadata", "Notice",
            "Add all og: meta tags to control how this page appears when shared on social media.",
            impact_score=4, effort="Low",
            affected=[{"value": tag, "detail": "missing"} for tag in missing_og[:50]]))

    return {
        "title": title, "title_length": title_len, "has_title": bool(title),
        "description": description, "description_length": desc_len,
        "has_description": bool(description),
        "has_og_tags": bool(og_title and og_desc),
        "has_og_image": bool(og_image),
        "issues": issues,
    }


def analyze_headings(soup):
    issues = []
    h1_tags = soup.find_all("h1")
    h2_tags = soup.find_all("h2")
    h3_tags = soup.find_all("h3")
    h4_tags = soup.find_all("h4")
    h1_count = len(h1_tags)
    h1_texts = [h.get_text().strip() for h in h1_tags]

    if h1_count == 0:
        issues.append(_issue("Missing H1 Tag", "Headings", "Critical",
            "Add exactly one H1 tag containing the primary keyword.",
            impact_score=9, effort="Low"))
    elif h1_count > 1:
        issues.append(_issue(f"Multiple H1 Tags ({h1_count})", "Headings", "High",
            "Keep only one H1 per page. Move additional headings to H2 or H3.",
            impact_score=7, effort="Low",
            affected=[{"value": t, "detail": "H1"} for t in h1_texts[:50]]))

    if len(h2_tags) == 0 and h1_count > 0:
        issues.append(_issue("No H2 Tags Found", "Headings", "Warning",
            "Add H2 tags to structure your content and improve readability.",
            impact_score=4, effort="Low"))

    # Heading hierarchy check: detect skipped levels
    all_headings = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        level = int(tag.name[1])
        all_headings.append(level)

    skipped = False
    skipped_seq = ""
    for i in range(1, len(all_headings)):
        if all_headings[i] > all_headings[i - 1] + 1:
            skipped = True
            skipped_seq = f"H{all_headings[i - 1]} → H{all_headings[i]}"
            break
    if skipped:
        issues.append(_issue("Skipped Heading Levels (e.g. H1→H3)", "Headings", "Low",
            "Use sequential heading levels (H1→H2→H3) without skipping levels for proper document structure.",
            impact_score=3, effort="Low",
            affected=[{"value": skipped_seq, "detail": "skipped level"}]))

    return {
        "h1_count": h1_count, "h2_count": len(h2_tags),
        "h3_count": len(h3_tags), "h4_count": len(h4_tags),
        "h1_texts": h1_texts, "issues": issues,
    }


def analyze_canonical(soup, url):
    issues = []
    canonical_tags = soup.find_all("link", rel="canonical")
    canonical_url  = ""
    is_self_ref    = False

    if len(canonical_tags) == 0:
        issues.append(_issue("Missing Canonical Tag", "Canonical", "Notice",
            "Add a canonical tag to prevent duplicate content issues.",
            impact_score=5, effort="Low"))
    elif len(canonical_tags) > 1:
        issues.append(_issue(f"Multiple Canonical Tags ({len(canonical_tags)})", "Canonical", "Critical",
            "Remove duplicate canonical tags: only one should exist per page.",
            impact_score=8, effort="Low",
            affected=[{"value": t.get("href", "").strip(), "detail": "canonical"}
                      for t in canonical_tags[:50]]))
    else:
        href = canonical_tags[0].get("href", "").strip()
        # Resolve relative canonical to absolute
        if href and not href.startswith("http"):
            href = urljoin(url, href)
        canonical_url = href

        if canonical_url:
            p_url = urlparse(url)
            p_can = urlparse(canonical_url)
            url_norm = f"{p_url.netloc}{p_url.path}".rstrip("/").lower()
            can_norm = f"{p_can.netloc}{p_can.path}".rstrip("/").lower()
            is_self_ref = url_norm == can_norm

            if not is_self_ref:
                issues.append(_issue("Canonical Points to Different URL", "Canonical", "Notice",
                    f"Verify this is intentional (cross-URL canonicals are often correct, e.g. ?page=2 → base); "
                    f"only a problem if the canonical target is a redirect, 4xx/5xx, or noindex page. "
                    f"Points to: {canonical_url[:80]}",
                    impact_score=6, effort="Low",
                    affected=[{"value": canonical_url, "detail": f"expected self-reference: {url}"}]))

    return {
        "canonical_url": canonical_url,
        "canonical_count": len(canonical_tags),
        "is_self_referencing": is_self_ref,
        "issues": issues,
    }


def analyze_indexability(soup, http_headers=None):
    issues = []
    robots_meta = soup.find("meta", attrs={"name": re.compile(r"robots", re.I)})
    robots_content = ""
    is_indexable = True

    if robots_meta:
        robots_content = robots_meta.get("content", "").lower()
        tokens = [t.strip() for t in robots_content.replace(",", " ").split()]
        if "noindex" in tokens:
            is_indexable = False
            issues.append(_issue("Page Set to Noindex", "Indexability", "Critical",
                "Verify this is intentional — noindex is correct for thank-you/cart/search/filter pages. "
                "Remove 'noindex' from meta robots if this page should appear in search results.",
                impact_score=10, effort="Low",
                affected=[{"value": robots_content, "detail": "meta robots"}]))
        if "nofollow" in tokens:
            issues.append(_issue("Meta Robots: Nofollow Active", "Indexability", "Warning",
                "Review whether nofollow on meta robots is intentional: it prevents link equity flow.",
                impact_score=5, effort="Low",
                affected=[{"value": robots_content, "detail": "meta robots"}]))

    # X-Robots-Tag response header: noindex is already flagged by
    # modules/advanced_checks.py::analyze_http_headers; only add the
    # nofollow case here (not covered there) to avoid double-counting.
    headers = {k.lower(): v for k, v in (http_headers or {}).items()}
    x_robots_tag = headers.get("x-robots-tag", "").lower()
    if x_robots_tag:
        tokens = [t.strip() for t in x_robots_tag.replace(",", " ").split()]
        if "noindex" in tokens:
            is_indexable = False
        if "nofollow" in tokens:
            issues.append(_issue("X-Robots-Tag Header: Nofollow Active", "Indexability", "Warning",
                "Review whether nofollow on the X-Robots-Tag header is intentional.",
                impact_score=5, effort="Low",
                affected=[{"value": x_robots_tag, "detail": "X-Robots-Tag header"}]))

    return {
        "robots_meta": robots_content, "x_robots_tag": x_robots_tag,
        "is_indexable": is_indexable, "issues": issues,
    }


def analyze_url_structure(url, response_time=0.0, final_url=None):
    """`url` drives the structural checks (length/case/query params) since
    those are properties of the URL as discovered/linked and worth fixing at
    the source regardless of where it redirects. The HTTPS check instead uses
    `final_url` (defaults to `url` if not given) -- otherwise a URL that's
    simply *linked* as http:// but correctly redirects to a fully-HTTPS site
    gets a Critical "migrate to HTTPS" finding that's actively wrong."""
    issues = []
    parsed = urlparse(url)
    path   = parsed.path
    slug   = path.rstrip("/").split("/")[-1] if path else ""
    url_len = len(url)

    if url_len > 115:
        issues.append(_issue(f"URL Too Long ({url_len} chars)", "URL Structure", "Notice",
            "Keep URLs under 115 characters for better crawlability and usability.",
            impact_score=4, effort="Medium",
            affected=[{"value": url, "detail": f"{url_len} chars"}]))

    if re.search(r"[A-Z]", path):
        issues.append(_issue("URL Contains Uppercase Letters", "URL Structure", "Notice",
            "Use lowercase-only URLs to avoid duplicate content issues.",
            impact_score=3, effort="Low",
            affected=[{"value": url, "detail": "uppercase in path"}]))

    if urlparse(final_url or url).scheme != "https":
        issues.append(_issue("Not Using HTTPS", "URL Structure", "Critical",
            "Migrate to HTTPS. Google uses HTTPS as a ranking signal and it's required for trust.",
            impact_score=9, effort="High",
            affected=[{"value": final_url or url, "detail": "not https"}]))

    return {
        "length": url_len, "slug": slug, "path": path,
        "is_https": parsed.scheme == "https", "issues": issues,
    }


def analyze_content(soup, html: str = "", base_url: str = ""):
    """Analyse content quality. Parses from raw HTML when available to avoid re-serialising soup."""
    issues = []
    # Parse a fresh tree from raw HTML so we can decompose tags without mutating the shared soup
    soup_copy = BeautifulSoup(html or str(soup), "lxml")
    for tag in soup_copy(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    text = soup_copy.get_text(separator=" ")
    # Count all tokens of 2+ non-whitespace chars: supports multilingual content
    words = [w for w in text.split() if len(w) >= 2]
    word_count   = len(words)
    reading_time = round(word_count / 200, 1)

    html_len   = len(str(soup))
    text_len   = len(text.strip())
    content_ratio = round((text_len / html_len * 100) if html_len > 0 else 0, 1)

    if word_count < THIN_THRESHOLD:
        issues.append(_issue(f"Thin Content ({word_count} words)", "Content", "Warning",
            f"Expand content to at least {THIN_THRESHOLD} words. Thin content rarely ranks well.",
            impact_score=8, effort="High",
            affected=[{"value": f"{word_count} words", "detail": f"below {THIN_THRESHOLD}"}]))
    elif word_count < 600:
        issues.append(_issue(f"Below Recommended Word Count ({word_count} words)", "Content", "Notice",
            "Aim for 600+ words to cover the topic comprehensively and outrank competitors.",
            impact_score=5, effort="High",
            affected=[{"value": f"{word_count} words", "detail": "below 600"}]))

    # Extract beginning and ending paragraphs for content preview
    para_tags = [
        p for p in soup_copy.find_all("p")
        if len(p.get_text(strip=True)) > 60   # skip very short/nav paragraphs
    ]
    paras = [p.get_text(separator=" ", strip=True) for p in para_tags]
    intro_paras       = paras[:3]   # first 3 meaningful paragraphs
    conclusion_paras  = paras[-3:] if len(paras) > 3 else []

    intro_tags       = para_tags[:3]
    conclusion_tags  = para_tags[-3:] if len(para_tags) > 3 else []
    from modules.link_auditor import linkify_paragraph_html
    intro_html      = [linkify_paragraph_html(p, base_url) for p in intro_tags]
    conclusion_html = [linkify_paragraph_html(p, base_url) for p in conclusion_tags]

    return {
        "word_count": word_count, "reading_time": reading_time,
        "content_ratio": content_ratio, "is_thin": word_count < THIN_THRESHOLD,
        # Normalized visible-text extraction (script/style/nav/footer/header/
        # aside already stripped above) - exposed so a caller can hash it for
        # sitewide exact-duplicate-content detection (worker/site_audit.py)
        # without re-parsing the page.
        "text": text.strip(),
        "intro_paragraphs": intro_paras,
        "conclusion_paragraphs": conclusion_paras,
        "intro_paragraphs_html": intro_html,
        "conclusion_paragraphs_html": conclusion_html,
        "total_paragraphs": len(paras),
        "issues": issues,
    }


def analyze_images(soup):
    issues = []
    images = soup.find_all("img")
    total  = len(images)
    missing_alt, empty_alt, poor_alt = [], [], []

    GENERIC_ALT = re.compile(r"^(image|img|photo|pic|picture|banner|logo)\d*\.?(jpg|png|gif|webp|svg)?$", re.I)

    for img in images:
        src = img.get("src", "")
        alt = img.get("alt")
        if alt is None:
            missing_alt.append(src)
        elif alt.strip() == "":
            empty_alt.append(src)
        elif GENERIC_ALT.match(alt.strip()):
            poor_alt.append((src, alt.strip()))

    if missing_alt:
        issues.append(_issue(f"{len(missing_alt)} Image(s) Missing Alt Attribute", "Images", "High",
            "Add descriptive alt text to every image for accessibility and image SEO.",
            impact_score=7, effort="Low",
            affected=[{"value": src, "detail": "no alt attribute"} for src in missing_alt[:50]]))
    if empty_alt:
        # alt="" is the CORRECT, WCAG-recommended markup for purely decorative
        # images, so this is advisory (Low), not a confirmed problem: only act on
        # it for images that actually convey meaning.
        issues.append(_issue(f"{len(empty_alt)} Image(s) with Empty Alt Text (verify decorative)", "Images", "Low",
            "Empty alt='' is correct for decorative images. Add a description only for images that convey meaning.",
            impact_score=2, effort="Low",
            affected=[{"value": src, "detail": 'alt=""'} for src in empty_alt[:50]]))
    if poor_alt:
        issues.append(_issue(f"{len(poor_alt)} Image(s) with Generic Alt Text", "Images", "Low",
            "Replace generic alt text like 'image.jpg' with descriptive phrases that include keywords.",
            impact_score=3, effort="Low",
            affected=[{"value": src, "detail": f'alt="{alt}"'} for src, alt in poor_alt[:50]]))

    return {
        "total_images": total,
        "missing_alt_count": len(missing_alt),
        "empty_alt_count":   len(empty_alt),
        "poor_alt_count":    len(poor_alt),
        "missing_alt_urls":  missing_alt[:10],
        "issues": issues,
    }


def analyze_redirect_chain(redirect_history):
    """Analyse redirect chain for multiple hops."""
    issues = []
    if len(redirect_history) > 1:
        issues.append(_issue(
            f"Redirect Chain Detected ({len(redirect_history)} hops)",
            "Redirects", "Warning",
            "Fix redirect chains: each hop wastes crawl budget and dilutes link equity. Link directly to the final URL.",
            impact_score=6, effort="Medium",
            affected=[{"value": hop, "detail": f"hop {n}"}
                      for n, hop in enumerate(redirect_history[:50], start=1)]))
    return {
        "chain_length": len(redirect_history),
        "chain": redirect_history,
        "issues": issues,
    }


def detect_page_type(url, soup):
    """Classify a page as course / blog / general so the per-page-type auditors
    (course_auditor, blog_auditor) only run where they apply. Getting this wrong
    fires page-type-specific checks ("Missing Course Overview / CTA", "Missing
    Course Schema") on pages that aren't that type - a false positive.
    """
    url_lower = url.lower()
    path = urlparse(url_lower).path.strip("/")

    # The homepage / site root is never an individual course or blog POST. A
    # training company's homepage is full of course vocabulary (enroll,
    # certification, instructor), which used to mis-classify it as "course" and
    # run per-course-page checks on it. Root is always "general".
    if path == "":
        return "general"

    # Classification is URL-pattern-only. The former content-signal fallback
    # (counting words like "curriculum"/"enroll") was measurably UNreliable on
    # real data: on edstellar, a genuine course page (/course/…-training) had
    # only 2 course signals while a non-course service page (/coaching-solutions)
    # had 3 - so the fallback classified backwards, firing per-course-page checks
    # ("Missing Course Overview / CTA / Schema") on service pages. Real course
    # and blog pages have clear URL patterns (edstellar: 1,697 /course/… pages,
    # /blog/… posts), which the checks below catch reliably. When a site has no
    # such pattern, defaulting to "general" (no page-type-specific checks) is the
    # correct, false-positive-free behavior. `soup` is kept in the signature for
    # callers/back-compat.
    if any(x in url_lower for x in ["/course/", "/courses/", "/training/", "/program/", "/workshop/", "/bootcamp/"]):
        return "course"
    if any(x in url_lower for x in ["/blog/", "/blogs/", "/article/", "/post/", "/news/", "/insight/"]):
        return "blog"
    return "general"


def audit_url(url, audit_type="auto", check_links=True, validate_links=False,
              fetch_pagespeed=False, psi_api_key=None, prefetched=None,
              prefetched_domain_health=None):
    # SSRF protection: block private/internal URLs before making any outbound request
    ok, ssrf_msg = validate_audit_url(url)
    if not ok:
        return {
            "url": url, "audit_timestamp": datetime.now().isoformat(),
            "status_code": 0, "audit_type": audit_type,
            "fetch_error": ssrf_msg, "response_time": 0.0,
            "seo_score": 0.0, "score_breakdown": {}, "all_issues": [],
        }

    result = {
        "url": url,
        "audit_timestamp": datetime.now().isoformat(),
        "status_code": 0,
        "audit_type": audit_type,
        "fetch_error": None,
        "response_time": 0.0,
        "redirect_count": 0,
        "redirect_chain": [],
        "final_url": url,
        "metadata": {},
        "headings": {},
        "canonical": {},
        "indexability": {},
        "url_structure": {},
        "content": {},
        "images": {},
        "advanced": {},
        "site_health": {},
        "redirect_analysis": {},
        "internal_links": {},
        "external_links": {},
        "course_audit": {},
        "blog_audit": {},
        "http_headers": {},
        "technical_seo": {},
        "seo_score": 0,
        "score_breakdown": {},
        "all_issues": [],
        "technical_audit_checklist": {},
    }

    fetch = prefetched if prefetched is not None else fetch_page(url)
    if not fetch["success"]:
        result["fetch_error"] = fetch.get("error", "Unknown error")
        result["status_code"] = fetch.get("status_code", 0)
        result["all_issues"] = [_issue(
            f"Page Fetch Failed: {result['fetch_error']}",
            "Accessibility", "Critical",
            "Ensure the URL is publicly accessible and returns a valid HTTP response.",
            impact_score=10, effort="High")]
        from modules.scoring import calculate_seo_score
        sr = calculate_seo_score(result)
        result["seo_score"] = sr["score"]
        result["score_breakdown"] = sr["breakdown"]
        return result

    result["status_code"]    = fetch["status_code"]
    result["response_time"]  = fetch.get("response_time", 0.0)
    result["redirect_count"] = fetch.get("redirect_count", 0)
    result["redirect_chain"] = fetch.get("redirect_history", [])
    result["final_url"]      = fetch.get("final_url", url)
    result["ssl_warning"]    = fetch.get("ssl_warning", False)

    soup = fetch["soup"]
    # Store plain text for keyword density analysis (strip script/style/nav)
    try:
        _txt_soup = soup.__class__(str(soup), "lxml")
        for _tag in _txt_soup(["script", "style", "nav", "footer", "header"]):
            _tag.decompose()
        result["_soup_text"] = _txt_soup.get_text(" ", strip=True)[:50000]
    except Exception:
        result["_soup_text"] = ""

    result["metadata"]     = analyze_metadata(soup, url)
    result["headings"]     = analyze_headings(soup)   # kept for scoring compatibility
    result["canonical"]    = analyze_canonical(soup, url)
    result["indexability"] = analyze_indexability(soup, http_headers=fetch.get("http_headers", {}))
    result["url_structure"] = analyze_url_structure(url, result["response_time"], final_url=result["final_url"])
    result["content"]      = analyze_content(soup, html=fetch.get("html", ""), base_url=url)
    result["images"]       = analyze_images(soup)     # kept for scoring compatibility
    result["redirect_analysis"] = analyze_redirect_chain(result["redirect_chain"])

    # ── Deep-analysis modules (new dedicated pages) ───────────────────────
    from modules.heading_auditor import analyze_heading_structure
    result["heading_detail"] = analyze_heading_structure(
        soup, title=result["metadata"].get("title", "")
    )

    from modules.image_auditor import analyze_images_advanced
    result["image_detail"] = analyze_images_advanced(
        soup, base_url=url, check_sizes=True, max_size_checks=40
    )

    # Capture HTTP headers and page size from the fetch result
    http_headers = fetch.get("http_headers", {})
    page_size_bytes = fetch.get("page_size_bytes", 0)
    result["http_headers"] = http_headers

    # Advanced checks (mobile, schema, social, hreflang, Twitter, headers, technical)
    from modules.advanced_checks import analyze_advanced
    result["advanced"] = analyze_advanced(
        soup, url,
        http_headers=http_headers,
        page_size_bytes=page_size_bytes,
        response_time=result["response_time"],
    )

    # Expose technical_seo sub-dict at top level for easy access
    result["technical_seo"] = result["advanced"].get("technical_seo", {})

    # ── Optional PageSpeed Insights API call ─────────────────────────────
    pagespeed_data = None
    if fetch_pagespeed:
        from modules.pagespeed import fetch_pagespeed as _fetch_psi
        pagespeed_data = _fetch_psi(url, strategy="mobile", api_key=psi_api_key)
    result["pagespeed"] = pagespeed_data or {}

    from modules.mobile_auditor import analyze_mobile
    result["mobile_audit"] = analyze_mobile(
        soup,
        base_url=url,
        technical_seo=result["technical_seo"],
        advanced_data=result["advanced"],
        pagespeed=pagespeed_data,
    )

    # Site-health checks: domain age, SSL, DNS/SPF/DMARC/MX, robots.txt,
    # sitemap.xml, readability, content freshness, canonical loops, www
    # redirect consistency, HTTP/2: run concurrently, independent of the page fetch above.
    #
    # Uses final_url (the actual destination after following redirects), not
    # the originally-requested `url` -- a page queued as e.g. http://example.com
    # that correctly redirects to https://example.com was otherwise flagged
    # with a Critical "Page Not Served Over HTTPS" against its own working
    # HTTPS setup, since check_ssl()/check_https_enforcement() just test
    # whether the URL they're given starts with "https".
    from modules.technical_checks import analyze_site_health
    result["site_health"] = analyze_site_health(
        result["final_url"], soup=soup, http_headers=http_headers, page_text=result.get("_soup_text", ""),
        prefetched_domain_health=prefetched_domain_health,
    )

    if audit_type == "auto":
        result["audit_type"] = detect_page_type(url, soup)

    if check_links:
        from modules.link_auditor import audit_links
        link_res = audit_links(soup, url, validate=validate_links)
        result["internal_links"] = link_res["internal"]
        result["external_links"] = link_res["external"]
        result["special_links"] = link_res.get("special", {})
        result["special_link_counts"] = link_res.get("special_counts", {})

    if result["audit_type"] == "course":
        from modules.course_auditor import audit_course_page
        result["course_audit"] = audit_course_page(soup, url)
    elif result["audit_type"] == "blog":
        from modules.blog_auditor import audit_blog_page
        result["blog_audit"] = audit_blog_page(soup, url)

    all_issues = []
    # "headings" and "images" are intentionally omitted: heading_detail and
    # image_detail cover the same checks more thoroughly (they're also the
    # versions modules/scoring.py scores against), and including the legacy
    # blocks too would double-count heading and alt-text issues.
    for key in ["metadata", "canonical", "indexability", "url_structure",
                "content", "heading_detail", "image_detail",
                "advanced", "redirect_analysis", "mobile_audit", "site_health",
                "internal_links", "external_links", "course_audit", "blog_audit"]:
        all_issues.extend(result.get(key, {}).get("issues", []))
    result["all_issues"] = _normalize_issues(all_issues)

    from modules.scoring import calculate_seo_score
    sr = calculate_seo_score(result)
    result["seo_score"] = sr["score"]
    result["score_breakdown"] = sr["breakdown"]

    from modules.technical_audit_checklist import build_technical_audit_checklist
    result["technical_audit_checklist"] = build_technical_audit_checklist(result)

    return result


def audit_urls_bulk(urls, audit_type="auto", check_links=True, validate_links=False,
                    max_workers=8, progress_callback=None,
                    fetch_pagespeed=False, psi_api_key=None):
    results = []
    completed = 0
    lock = threading.Lock()
    total = len(urls)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                audit_url, url, audit_type, check_links, validate_links,
                fetch_pagespeed, psi_api_key
            ): url
            for url in urls
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                results.append(future.result())
            except Exception as e:
                results.append({
                    "url": url, "fetch_error": str(e), "status_code": 0,
                    "audit_type": "general", "seo_score": 0, "advanced": {},
                    "http_headers": {}, "technical_seo": {},
                    "all_issues": [_issue(str(e), "Error", "Critical",
                        "Check URL validity and network accessibility.",
                        impact_score=10, effort="High")],
                })
            with lock:
                completed += 1
                done = completed
            if progress_callback:
                progress_callback(done, total)

    return results
