"""Internal and external link discovery, classification, and validation."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from modules.auditor import BlockedURLError, safe_request, validate_audit_url

# Browser-like headers: avoids 403/999 bot blocks on LinkedIn, McKinsey, etc.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
TIMEOUT = 8

# Reuse TCP connections for all link validation requests in a session
_session = requests.Session()
_session.headers.update(HEADERS)

# Sites that always block HEAD/GET with non-standard codes: don't count as "broken"
KNOWN_BLOCKER_DOMAINS = {
    "linkedin.com", "www.linkedin.com",
    "twitter.com", "x.com", "www.twitter.com",
    "facebook.com", "www.facebook.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com",
}

WEAK_ANCHORS = {
    "click here", "here", "read more", "learn more", "link",
    "this", "more", "see more", "see here", "visit", "go here",
    "continue", "website", "url", "page", "article",
    "post", "check out", "check this", "find out", "more info",
    "click", "view", "details", "info",
    # NOTE: "source", "download", and "example" were removed - on a citation
    # ("Source"), a file link ("Download"), or a demo link ("Example") they are
    # contextually descriptive, not generic filler, and flagging them produced
    # weak-anchor false positives.
}

# Domain type categories for external link classification
DOMAIN_CATEGORIES = {
    "social":    {"facebook.com","twitter.com","x.com","linkedin.com","instagram.com",
                  "youtube.com","tiktok.com","pinterest.com","reddit.com","snapchat.com"},
    "news":      {"bbc.com","cnn.com","nytimes.com","theguardian.com","reuters.com",
                  "apnews.com","bloomberg.com","forbes.com","wsj.com","techcrunch.com",
                  "businessinsider.com","entrepreneur.com"},
    "academic":  {"scholar.google.com","researchgate.net","academia.edu","jstor.org",
                  "pubmed.ncbi.nlm.nih.gov","springer.com","ieee.org","ssrn.com"},
    "government":{"gov","mil","europa.eu"},
    "reference": {"wikipedia.org","wikimedia.org","britannica.com","investopedia.com",
                  "merriam-webster.com"},
    "tech":      {"github.com","stackoverflow.com","developer.mozilla.org","docs.python.org",
                  "aws.amazon.com","cloud.google.com","docs.microsoft.com","npmjs.com"},
}


def get_base_domain(url):
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def get_full_domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def categorize_domain(domain):
    """Return a category label for an external domain."""
    # `lstrip("www.")` strips any leading run of the CHARACTERS w/./., so it
    # corrupted domains like "worldbank.org" -> "orldbank.org"; strip the literal
    # "www." prefix instead.
    d = domain.lower()
    if d.startswith("www."):
        d = d[4:]
    for cat, domains in DOMAIN_CATEGORIES.items():
        if d in domains:
            return cat.title()
        # TLD-based check for gov/mil
        if cat == "government":
            for tld in domains:
                if d.endswith("." + tld) or d == tld:
                    return "Government"
    return "Other"


def status_label(code):
    """Human-readable HTTP status label (Ahrefs-style)."""
    if code is None:
        return "Not Checked"
    if code == 0:
        return "Error"
    if code == 200:
        return "OK"
    if code in (301, 302, 303, 307, 308):
        return f"{code} Redirect"
    if code == 403:
        return "403 Forbidden"
    if code == 404:
        return "404 Not Found"
    if code == 410:
        return "410 Gone"
    if code == 429:
        return "429 Rate Limited"
    if code == 500:
        return "500 Server Error"
    if code == 503:
        return "503 Unavailable"
    if code == 999:
        return "999 Blocked"
    if 200 <= code < 300:
        return f"{code} OK"
    if 300 <= code < 400:
        return f"{code} Redirect"
    if 400 <= code < 500:
        return f"{code} Client Error"
    if 500 <= code < 600:
        return f"{code} Server Error"
    return str(code)


def link_health(code, domain=""):
    """
    Classify link health:
      ok       : 2xx
      redirect : 3xx
      blocked  : 401/403 (auth / WAF / bot-challenge), 408/429 (rate-limit),
                 451, 503 (unavailable/maintenance), 999 - the server is alive
                 but refused or throttled our bot request; NOT a dead link
      broken   : 404, 410, and 500/502/504 - a genuinely dead or erroring resource
      unknown  : None (not validated), 0 (timeout/connection/SSL - could not verify)

    Prior versions bucketed 403 (unless on a tiny hard-coded social-domain
    allowlist), 429, 503, and every connection failure as "broken", so a link to
    any Cloudflare/WAF-protected site, a rate-limited API host, or a slow server
    that timed out during the 12-worker burst was reported as a broken link on a
    page that has no broken links. "blocked"/"unknown" are excluded from the
    broken count so those false positives no longer fire.
    """
    if code is None or code == 0:
        return "unknown"
    if code in (401, 403, 408, 429, 451, 503, 999):
        return "blocked"
    if 200 <= code < 300:
        return "ok"
    if 300 <= code < 400:
        return "redirect"
    if code in (404, 410) or 500 <= code < 600:
        return "broken"
    if 400 <= code < 500:
        # Other 4xx (400/405/406/…): an access/protocol refusal, not a confirmed
        # dead resource. Treat as blocked so it doesn't inflate the broken count.
        return "blocked"
    return "unknown"


def _resolve_href(href, base_url):
    """Resolve an <a href> to an absolute URL. Handles the scheme-relative
    `//host/path` form explicitly, since `urljoin` alone doesn't special-case
    it the way this needs (was duplicated identically in parse_link_tag and
    linkify_paragraph_html before being pulled out here)."""
    if href.startswith("//"):
        scheme = urlparse(base_url).scheme or "https"
        return scheme + ":" + href
    if href.startswith(("http://", "https://")):
        return href
    return urljoin(base_url, href)


def classify_link(href, base_url):
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    if href.startswith("//"):
        scheme = urlparse(base_url).scheme or "https"
        href = scheme + ":" + href
    if not href.startswith("http"):
        return "internal"
    base_domain = get_base_domain(base_url)
    link_domain = get_base_domain(href)
    if link_domain == base_domain or link_domain.endswith("." + base_domain):
        return "internal"
    return "external"


# Extensions used to tag a page/internal/external link with a more specific
# link_category than just "page": purely extension-based, no content sniffing.
_DOC_EXTENSIONS = {
    "pdf": "pdf",
    "doc": "download", "docx": "download",
    "xls": "download", "xlsx": "download",
    "ppt": "download", "pptx": "download",
    "zip": "download", "rar": "download", "7z": "download",
    "csv": "download", "txt": "download",
    "mp3": "download", "mp4": "download", "mov": "download", "avi": "download",
}
_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "avif"}


def classify_link_category(url):
    """Extension-based link category for internal/external links: pdf, download, image, or page."""
    path = urlparse(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if ext == "pdf":
        return "pdf"
    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in _DOC_EXTENSIONS:
        return "download"
    return "page"


_LOCATION_SELECTORS = (
    ("nav", "nav"),
    ("header", "header"),
    ("footer", "footer"),
    ("aside", "sidebar"),
)


def classify_link_location(tag):
    """Walk up the DOM to classify where a link sits: nav / header / footer / sidebar /
    breadcrumb / body. Heuristic, based on tag names and common class/id naming, not a
    layout-rendering analysis."""
    node = tag
    depth = 0
    while node is not None and depth < 12:
        name = (getattr(node, "name", "") or "").lower()
        classes = " ".join(node.get("class", [])).lower() if hasattr(node, "get") else ""
        node_id = (node.get("id", "") or "").lower() if hasattr(node, "get") else ""
        haystack = f"{classes} {node_id}"

        if "breadcrumb" in haystack:
            return "breadcrumb"
        for tag_name, label in _LOCATION_SELECTORS:
            if name == tag_name:
                return label
        if "sidebar" in haystack:
            return "sidebar"
        if "footer" in haystack:
            return "footer"
        if "nav" in haystack or "menu" in haystack:
            return "nav"

        node = node.parent
        depth += 1
    return "body"


def parse_special_link_tag(tag, base_url, kind):
    """Parse mailto: / tel: / #anchor / javascript: links: no HTTP validation applies."""
    href = tag.get("href", "").strip()
    anchor = tag.get_text(strip=True) or "[No Text]"
    return {
        "href": href,
        "anchor_text": anchor[:150],
        "kind": kind,
        "location": classify_link_location(tag),
    }


def parse_link_tag(tag, base_url):
    href = tag.get("href", "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None

    full_url = _resolve_href(href, base_url)

    rel_attr = tag.get("rel", [])
    if isinstance(rel_attr, str):
        rel_attr = rel_attr.split()
    rel = [r.lower() for r in rel_attr]

    target     = tag.get("target", "").lower()
    title_attr = tag.get("title", "").strip()
    anchor_raw = tag.get_text(strip=True)
    has_img    = bool(tag.find("img"))
    if not anchor_raw and has_img:
        img = tag.find("img")
        anchor_raw = img.get("alt", "").strip() if img else ""
        anchor_type = "image" if anchor_raw else "image-no-alt"
    else:
        anchor_type = "text" if anchor_raw else "empty"

    anchor = anchor_raw or ("[Image]" if has_img else "[No Text]")

    is_nofollow  = "nofollow"  in rel
    is_sponsored = "sponsored" in rel
    is_ugc       = "ugc"       in rel
    # A link is "dofollow" only when it carries NO ranking-suppressing qualifier.
    # ugc (user-generated content) is such a qualifier - Google treats it as a
    # hint not to pass full ranking credit - so it must be excluded here too;
    # otherwise ugc links inflate the dofollow count and get miscategorised.
    is_dofollow  = not (is_nofollow or is_sponsored or is_ugc)

    return {
        "url":              full_url,
        "href":             href,
        "anchor_text":      anchor[:150],
        "anchor_type":      anchor_type,      # text / image / image-no-alt / empty
        "link_category":    classify_link_category(full_url),  # page / pdf / download / image
        "location":         classify_link_location(tag),        # nav / header / footer / sidebar / breadcrumb / body
        "title_attr":       title_attr,
        "rel":              " ".join(rel) if rel else "dofollow",
        "rel_list":         rel,
        "target":           target,
        "is_nofollow":      is_nofollow,
        "is_sponsored":     is_sponsored,
        "is_ugc":           is_ugc,
        "is_dofollow":      is_dofollow,
        "opens_new_tab":    target == "_blank",
        "has_noopener":     "noopener"   in rel,
        "has_noreferrer":   "noreferrer" in rel,
        "missing_target":   target == "",
        "is_weak_anchor":   anchor.lower().strip() in WEAK_ANCHORS,
        "status_code":      None,
        "status_label":     "Not Checked",
        "health":           "unknown",
        "is_broken":        None,
        "is_redirect":      None,
        "final_url":        None,
        "redirect_path":    None,
        "response_time_ms": None,
        "content_type":     None,
    }


def validate_url(url):
    """HTTP-check a URL. Returns status, health, label."""
    domain = get_full_domain(url)
    base   = get_base_domain(url)

    # SSRF guard: links come from untrusted third-party page HTML. Refuse to
    # HTTP-check any URL pointing at a private/reserved/internal host (directly
    # or via DNS), so a crafted <a href="http://169.254.169.254/..."> can't be
    # used to probe internal services.
    ok, _ssrf_msg = validate_audit_url(url)
    if not ok:
        return {
            "url": url,
            "status_code": 0,
            "status_label": "Blocked (internal address)",
            "health": "blocked",
            "is_broken": False,
            "is_redirect": False,
            "final_url": url,
            "redirect_count": 0,
            "note": "Skipped: refuses to fetch private/internal addresses",
        }

    if base in KNOWN_BLOCKER_DOMAINS:
        return {
            "url": url,
            "status_code": 999,
            "status_label": "999 Blocked",
            "health": "blocked",
            "is_broken": False,
            "is_redirect": False,
            "final_url": url,
            "redirect_count": 0,
            "note": "Skipped: site blocks automated requests",
        }

    ssl_error = False

    def _get_with_ssl_fallback(method, u, **kwargs):
        nonlocal ssl_error
        try:
            return method(u, **kwargs, verify=True)
        except requests.exceptions.SSLError:
            ssl_error = True
            return method(u, **kwargs, verify=False)

    try:
        # SSRF guard, part 2: the check above only validates the URL as
        # given. `allow_redirects=True` used to let `requests` follow
        # redirects unguarded from there, so a link that passed the initial
        # check but 301'd to an internal/metadata host would be fetched
        # anyway. `safe_request` re-validates every hop before requesting it.
        try:
            resp = safe_request(
                lambda u, **kw: _get_with_ssl_fallback(_session.head, u, **kw),
                url, timeout=TIMEOUT,
            )
        except BlockedURLError:
            return {
                "url": url,
                "status_code": 0,
                "status_label": "Blocked (internal address)",
                "health": "blocked",
                "is_broken": False,
                "is_redirect": False,
                "final_url": url,
                "redirect_count": 0,
                "note": "Skipped: redirected to a private/internal address",
            }
        code = resp.status_code

        if code in (405, 501):
            try:
                resp = safe_request(
                    lambda u, **kw: _get_with_ssl_fallback(_session.get, u, **kw),
                    url, timeout=TIMEOUT, stream=True,
                )
            except BlockedURLError:
                return {
                    "url": url,
                    "status_code": 0,
                    "status_label": "Blocked (internal address)",
                    "health": "blocked",
                    "is_broken": False,
                    "is_redirect": False,
                    "final_url": url,
                    "redirect_count": 0,
                    "note": "Skipped: redirected to a private/internal address",
                }
            resp.close()
            code = resp.status_code

        h     = link_health(code, url)
        label = status_label(code)
        redirect_path = [r.url for r in resp.history] + [resp.url] if resp.history else []
        response_time_ms = round(sum((r.elapsed.total_seconds() for r in resp.history), resp.elapsed.total_seconds()) * 1000)

        return {
            "url":              url,
            "status_code":      code,
            "status_label":     label,
            "health":           h,
            "is_broken":        h == "broken",
            "is_redirect":      h == "redirect",
            "final_url":        resp.url,
            "redirect_count":   len(resp.history),
            "redirect_path":    redirect_path,
            "response_time_ms": response_time_ms,
            "content_type":     resp.headers.get("Content-Type", "").split(";")[0].strip(),
            "ssl_error":        ssl_error,
        }

    # Timeout / SSL / connection failures are transient or environmental (a slow
    # server, a cold CDN, a cert quirk, a blip during the 12-worker burst) - they
    # mean "could not verify", not "dead link". Marking them broken produced
    # broken-link false positives on pages whose links are actually fine, so they
    # are now "unknown" (is_broken False) and excluded from the broken count.
    except requests.exceptions.Timeout:
        return {"url": url, "status_code": 0, "status_label": "Timeout",
                "health": "unknown", "is_broken": False, "is_redirect": False,
                "response_time_ms": TIMEOUT * 1000}
    except requests.exceptions.SSLError:
        return {"url": url, "status_code": 0, "status_label": "SSL Error",
                "health": "unknown", "is_broken": False, "is_redirect": False}
    except requests.exceptions.ConnectionError:
        return {"url": url, "status_code": 0, "status_label": "Connection Error",
                "health": "unknown", "is_broken": False, "is_redirect": False}
    except Exception as e:
        return {"url": url, "status_code": 0, "status_label": f"Error: {str(e)[:40]}",
                "health": "unknown", "is_broken": False, "is_redirect": False}


def validate_urls_bulk(urls, max_workers=12):
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(validate_url, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception:
                results[url] = {"url": url, "status_code": 0, "status_label": "Error",
                                "health": "broken", "is_broken": True}
    return results


_SPECIAL_PREFIXES = (
    ("mailto:", "mailto"),
    ("tel:", "tel"),
    ("javascript:", "javascript"),
)


def audit_links(soup, base_url, validate=False):
    internal, external = [], []
    special = {"mailto": [], "tel": [], "anchor": [], "javascript": []}

    for tag in soup.find_all("a", href=True):
        href = (tag.get("href") or "").strip()

        matched_special = False
        for prefix, kind in _SPECIAL_PREFIXES:
            if href.startswith(prefix):
                special[kind].append(parse_special_link_tag(tag, base_url, kind))
                matched_special = True
                break
        if matched_special:
            continue
        if href.startswith("#") and len(href) > 1:
            special["anchor"].append(parse_special_link_tag(tag, base_url, "anchor"))
            continue

        link_data = parse_link_tag(tag, base_url)
        if not link_data:
            continue
        kind = classify_link(link_data["href"], base_url)
        if kind == "internal":
            internal.append(link_data)
        elif kind == "external":
            external.append(link_data)

    if validate:
        all_urls = list({l["url"] for l in internal + external})
        validation = validate_urls_bulk(all_urls)
        for link in internal + external:
            v = validation.get(link["url"], {})
            link["status_code"]      = v.get("status_code")
            link["status_label"]     = v.get("status_label", "Not Checked")
            link["health"]           = v.get("health", "unknown")
            link["is_broken"]        = v.get("is_broken")
            link["is_redirect"]      = v.get("is_redirect")
            link["final_url"]        = v.get("final_url")
            link["redirect_path"]    = v.get("redirect_path")
            link["response_time_ms"] = v.get("response_time_ms")
            link["content_type"]     = v.get("content_type")

    return {
        "internal": _summarize_internal(internal),
        "external": _summarize_external(external),
        "special": {k: v[:200] for k, v in special.items()},
        "special_counts": {k: len(v) for k, v in special.items()},
    }


# ── Body content link highlighting ────────────────────────────────────────

INTERNAL_LINK_COLOR = "#1D4ED8"   # blue, matches internal-link brand color elsewhere
EXTERNAL_LINK_COLOR = "#7C3AED"   # purple, matches external-link brand color elsewhere


def linkify_paragraph_html(p_tag, base_url, max_chars=400):
    """
    Render a BeautifulSoup <p> tag as safe inline HTML for content previews,
    keeping only <a> links (color-coded internal/external) and escaping all
    other text. Every other tag is unwrapped to plain text. Output is
    truncated to max_chars without ever leaving a tag unclosed.
    """
    import html as _html

    frag = BeautifulSoup(str(p_tag), "lxml")
    p_el = frag.find("p") or frag

    for tag in p_el.find_all(True):
        if tag.name != "a":
            tag.unwrap()

    budget = max_chars
    parts, truncated = [], False
    for node in list(p_el.contents):
        if budget <= 0:
            truncated = True
            break
        if getattr(node, "name", None) == "a":
            href = (node.get("href") or "").strip()
            text = node.get_text(" ", strip=True)
            is_linkable = href and text and not href.lower().startswith(
                ("#", "mailto:", "tel:", "javascript:", "data:")
            )
            if not is_linkable:
                clip = text[:budget]
                if len(text) > budget:
                    truncated = True
                budget -= len(clip)
                if clip:
                    parts.append(_html.escape(clip))
                continue

            full_url = _resolve_href(href, base_url)

            clip = text[:budget]
            if len(text) > budget:
                truncated = True
            budget -= len(clip)

            kind  = classify_link(full_url, base_url) or "external"
            color = INTERNAL_LINK_COLOR if kind == "internal" else EXTERNAL_LINK_COLOR
            icon  = "🔵" if kind == "internal" else "🟣"
            safe_url = _html.escape(full_url)
            parts.append(
                f"<a href='{safe_url}' target='_blank' rel='noopener noreferrer' "
                f"style='color:{color};font-weight:700;text-decoration:underline;"
                f"text-underline-offset:2px' "
                f"title='{icon} {kind.title()} link → {safe_url}'>{_html.escape(clip)}</a>"
            )
        else:
            text = str(node)
            clip = text[:budget]
            if len(text) > budget:
                truncated = True
            budget -= len(clip)
            parts.append(_html.escape(clip))

    result = "".join(parts)
    if truncated:
        result += "…"
    return result


# ── Anchor text analysis ──────────────────────────────────────────────────

def analyze_anchor_text(links):
    """
    Returns a detailed anchor text report across a list of link dicts.
    Works for both internal and external link sets.
    """
    total = len(links)
    if total == 0:
        return {"total": 0, "distribution": [], "issues": [], "opportunities": []}

    anchors = [l.get("anchor_text","").strip() for l in links]
    counter = Counter(a.lower() for a in anchors if a)

    distribution = [
        {
            "anchor": text,
            "count": cnt,
            "pct": round(cnt / total * 100, 1),
            "is_weak": text in WEAK_ANCHORS,
        }
        for text, cnt in counter.most_common(50)
    ]

    exact_match_threshold = 0.30  # >30% same anchor = over-optimised
    issues       = []
    opportunities= []

    top_anchor, top_cnt = (counter.most_common(1)[0] if counter else ("", 0))
    if top_cnt / total > exact_match_threshold:
        issues.append({
            "type": "over_optimized",
            "anchor": top_anchor,
            "pct": round(top_cnt / total * 100, 1),
            "message": f'"{top_anchor}" used on {round(top_cnt/total*100,1)}% of links: may look spammy to Google.',
            "recommendation": "Vary your anchor text with natural phrases, branded terms, and partial-match keywords.",
        })

    weak_links  = [l for l in links if l.get("is_weak_anchor")]
    img_no_alt  = [l for l in links if l.get("anchor_type") == "image-no-alt"]
    empty_links = [l for l in links if l.get("anchor_type") == "empty"]

    if weak_links:
        opportunities.append({
            "type": "weak_anchor",
            "count": len(weak_links),
            "message": f"{len(weak_links)} link(s) use generic anchor text ('click here', 'read more', etc.).",
            "recommendation": "Replace with descriptive, keyword-rich anchor text that signals the target page topic.",
            "links": [l.get("url","") for l in weak_links[:10]],
        })
    if img_no_alt:
        opportunities.append({
            "type": "image_no_alt",
            "count": len(img_no_alt),
            "message": f"{len(img_no_alt)} image link(s) have no alt text: search engines cannot read the anchor.",
            "recommendation": "Add descriptive alt attributes to all linked images.",
            "links": [l.get("url","") for l in img_no_alt[:10]],
        })
    if empty_links:
        opportunities.append({
            "type": "empty_anchor",
            "count": len(empty_links),
            "message": f"{len(empty_links)} link(s) have completely empty anchor text.",
            "recommendation": "Add meaningful anchor text or remove the empty link tag.",
            "links": [l.get("url","") for l in empty_links[:10]],
        })

    unique_anchors = len(counter)
    if unique_anchors > 0:
        diversity = round(unique_anchors / total * 100, 1)
        opportunities.append({
            "type": "diversity",
            "count": unique_anchors,
            "message": f"Anchor text diversity: {unique_anchors} unique phrases across {total} links ({diversity}% unique).",
            "recommendation": (
                "Good diversity: keep varying anchor text."
                if diversity > 60 else
                "Low diversity: try using more unique, contextually relevant phrases per link."
            ),
            "links": [],
        })

    return {
        "total":        total,
        "unique":       unique_anchors,
        "weak_count":   len(weak_links),
        "image_no_alt": len(img_no_alt),
        "empty_count":  len(empty_links),
        "distribution": distribution,
        "issues":       issues,
        "opportunities":opportunities,
    }


# ── Internal link opportunity detection ───────────────────────────────────

def get_internal_link_opportunities(results_list):
    """
    Analyse audit results to find internal linking gaps and suggestions.
    Returns a list of opportunity dicts.
    """
    opportunities = []

    # Pages with few or no internal links pointing TO them
    page_urls = {r.get("url","") for r in results_list}

    # Count inbound internal links per page
    inbound = {url: 0 for url in page_urls}
    for r in results_list:
        for lk in r.get("internal_links", {}).get("links", []):
            target = lk.get("url","")
            if target in inbound:
                inbound[target] += 1

    orphan_pages = [url for url, cnt in inbound.items() if cnt == 0]
    low_link_pages = [url for url, cnt in inbound.items() if 0 < cnt < 3]

    if orphan_pages:
        opportunities.append({
            "type": "orphan_pages",
            "severity": "High",
            "count": len(orphan_pages),
            "title": "Orphan Pages: No Internal Links Pointing To Them",
            "message": f"{len(orphan_pages)} page(s) have zero internal inbound links from other audited pages.",
            "recommendation": "Link to these pages from relevant content to help search engines discover and crawl them.",
            "pages": orphan_pages[:10],
        })

    if low_link_pages:
        opportunities.append({
            "type": "low_inbound",
            "severity": "Medium",
            "count": len(low_link_pages),
            "title": "Under-Linked Pages: Fewer Than 3 Internal Inbound Links",
            "message": f"{len(low_link_pages)} page(s) have fewer than 3 internal links pointing to them.",
            "recommendation": "Increase internal links to these pages to distribute link equity and improve crawl priority.",
            "pages": low_link_pages[:10],
        })

    # Pages with very few outbound internal links
    for r in results_list:
        il = r.get("internal_links", {})
        total_out = il.get("total_links", 0)
        word_count = r.get("content", {}).get("word_count", 0)
        if word_count > 500 and total_out < 3:
            opportunities.append({
                "type": "low_outbound",
                "severity": "Medium",
                "count": 1,
                "title": "Content-Rich Page With Few Outgoing Internal Links",
                "message": f"{r.get('url','')} has {word_count:,} words but only {total_out} internal link(s).",
                "recommendation": "Add relevant internal links to distribute link equity and guide readers to related content.",
                "pages": [r.get("url","")],
            })

    # Broken internal links by target page
    broken_targets = {}
    for r in results_list:
        for lk in r.get("internal_links", {}).get("links", []):
            if lk.get("is_broken"):
                t = lk.get("url","")
                if t not in broken_targets:
                    broken_targets[t] = []
                broken_targets[t].append(r.get("url",""))

    if broken_targets:
        for target, sources in list(broken_targets.items())[:10]:
            opportunities.append({
                "type": "broken_target",
                "severity": "Critical",
                "count": len(sources),
                "title": "Broken Internal Link Target",
                "message": f"'{target}' is broken, linked from {len(sources)} page(s).",
                "recommendation": "Fix or redirect the broken target URL, or update all links pointing to it.",
                "pages": sources[:5],
            })

    # Weak anchor text opportunities per page
    for r in results_list:
        weak = r.get("internal_links", {}).get("weak_anchor_count", 0)
        if weak > 0:
            opportunities.append({
                "type": "weak_anchors_page",
                "severity": "Low",
                "count": weak,
                "title": f"Weak Internal Anchor Text ({weak} links)",
                "message": f"{r.get('url','')} has {weak} internal link(s) with generic anchor text.",
                "recommendation": "Use descriptive, keyword-rich anchor text on internal links to signal relevance to search engines.",
                "pages": [r.get("url","")],
            })

    return opportunities


# ── Summarise helpers ─────────────────────────────────────────────────────

def _summarize_internal(links):
    issues = []
    unique    = list({l["url"] for l in links})
    dofollow  = sum(1 for l in links if l["is_dofollow"])
    nofollow  = sum(1 for l in links if l["is_nofollow"])
    broken    = sum(1 for l in links if l.get("is_broken") is True)
    redirect  = sum(1 for l in links if l.get("is_redirect") is True)
    new_tab   = sum(1 for l in links if l["opens_new_tab"])
    same_tab  = len(links) - new_tab
    miss_no   = sum(1 for l in links if l["opens_new_tab"] and not l["has_noopener"])
    weak_a    = sum(1 for l in links if l.get("is_weak_anchor"))
    img_links = sum(1 for l in links if "image" in l.get("anchor_type",""))
    empty_a   = sum(1 for l in links if l.get("anchor_type") == "empty")

    if len(links) == 0:
        issues.append({
            "issue": "No Internal Links Found", "category": "Internal Links",
            "severity": "Warning", "impact_score": 7, "effort": "Medium",
            "recommendation": "Add internal links to improve crawlability and distribute link equity.",
        })
    elif len(links) < 3:
        issues.append({
            "issue": f"Very Few Internal Links ({len(links)})", "category": "Internal Links",
            "severity": "Warning", "impact_score": 5, "effort": "Medium",
            "recommendation": "Add more internal links to connect related content and improve navigation.",
        })
    if broken > 0:
        issues.append({
            "issue": f"Broken Internal Links ({broken})", "category": "Internal Links",
            "severity": "Critical", "impact_score": 9, "effort": "Low",
            "recommendation": "Fix or remove all broken internal links immediately: they harm user experience and crawlability.",
            "affected": [
                {"value": l["url"], "detail": f"HTTP {l.get('status_code') or '?'} · anchor: {l.get('anchor_text', '') or '[no text]'}"}
                for l in links if l.get("is_broken") is True
            ][:50],
        })
    if redirect > 0:
        issues.append({
            "issue": f"Redirecting Internal Links ({redirect})", "category": "Internal Links",
            "severity": "Warning", "impact_score": 5, "effort": "Low",
            "recommendation": "Update internal links to point directly to final destination URLs.",
            "affected": [
                {"value": l["url"], "detail": f"redirects · anchor: {l.get('anchor_text', '') or '[no text]'}"}
                for l in links if l.get("is_redirect") is True
            ][:50],
        })
    if miss_no > 0:
        issues.append({
            "issue": f"Internal Links Opening in New Tab Without rel='noopener' ({miss_no})", "category": "Internal Links",
            # Notice (0 score penalty): advisory only. Modern browsers imply
            # `noopener` for target="_blank" automatically (since ~2021), so this
            # is neither a security concern in practice nor an SEO ranking factor.
            "severity": "Notice", "impact_score": 0, "effort": "Low",
            "recommendation": "Advisory only: browsers have implied noopener for target=\"_blank\" since 2021, so no action is required. This is not an SEO issue. If you want explicit hardening for very old browsers, you may add rel='noopener noreferrer'.",
        })
    if weak_a > 0:
        issues.append({
            "issue": f"Weak Anchor Text on {weak_a} Internal Link(s)", "category": "Internal Links",
            "severity": "Low", "impact_score": 4, "effort": "Low",
            "recommendation": "Replace generic anchor text ('click here', 'read more') with descriptive keyword-rich phrases.",
            "affected": [
                {"value": l.get("anchor_text", "") or "[no text]", "detail": f"→ {l['url']}"}
                for l in links if l.get("is_weak_anchor")
            ][:50],
        })

    return {
        "total_links":            len(links),
        "unique_links":           len(unique),
        "dofollow_count":         dofollow,
        "nofollow_count":         nofollow,
        "broken_count":           broken,
        "redirect_count":         redirect,
        "new_tab_count":          new_tab,
        "same_tab_count":         same_tab,
        "missing_noopener_count": miss_no,
        "weak_anchor_count":      weak_a,
        "image_link_count":       img_links,
        "empty_anchor_count":     empty_a,
        "links":                  links[:200],
        "issues":                 issues,
    }


def _summarize_external(links):
    issues = []
    unique_domains  = list({get_base_domain(l["url"]) for l in links})
    dofollow        = sum(1 for l in links if l["is_dofollow"])
    nofollow        = sum(1 for l in links if l["is_nofollow"])
    sponsored       = sum(1 for l in links if l["is_sponsored"])
    ugc             = sum(1 for l in links if l["is_ugc"])
    broken          = sum(1 for l in links if l.get("is_broken") is True)
    blocked         = sum(1 for l in links if l.get("health") == "blocked")
    redirect        = sum(1 for l in links if l.get("is_redirect") is True)
    new_tab         = sum(1 for l in links if l["opens_new_tab"])
    same_tab        = len(links) - new_tab
    miss_noop       = sum(1 for l in links if l["opens_new_tab"] and not l["has_noopener"])
    miss_noref      = sum(1 for l in links if l["opens_new_tab"] and not l["has_noreferrer"])
    weak_a          = sum(1 for l in links if l.get("is_weak_anchor"))
    img_links       = sum(1 for l in links if "image" in l.get("anchor_type",""))
    no_security     = sum(1 for l in links if l["opens_new_tab"] and not (l["has_noopener"] and l["has_noreferrer"]))

    if broken > 0:
        issues.append({
            "issue": f"Broken External Links ({broken})", "category": "External Links",
            "severity": "High", "impact_score": 8, "effort": "Low",
            "recommendation": "Replace or remove all broken external links: they harm user experience and trust signals.",
            "affected": [
                {"value": l["url"], "detail": f"HTTP {l.get('status_code') or '?'} · anchor: {l.get('anchor_text', '') or '[no text]'}"}
                for l in links if l.get("is_broken") is True
            ][:50],
        })
    if miss_noop > 0:
        issues.append({
            "issue": f"External Links Missing rel='noopener' ({miss_noop})", "category": "External Links",
            # Notice (0 score penalty): advisory only. Modern browsers imply
            # noopener for target="_blank" since ~2021, so this is neither a
            # practical security concern nor an SEO ranking factor.
            "severity": "Notice", "impact_score": 0, "effort": "Low",
            "recommendation": "Advisory only: browsers have implied noopener for target=\"_blank\" since 2021, so no action is required. This is not an SEO issue. If you want explicit hardening for very old browsers, you may add rel='noopener noreferrer'.",
        })
    # Paid/affiliate links that are follow (no rel qualifier) should carry
    # rel="sponsored" under Google's link-spam policy. Conservative keyword match
    # on the URL to avoid false positives.
    # High-precision affiliate/ad markers only. Deliberately conservative: broad
    # tokens like "aff", "ref=", "utm_", "tag=", "partner", "/go/" match ordinary
    # outbound links (utm-tagged shares, "staff"/"traffic", internal /go/ routers)
    # and would reintroduce the false positives Phase 1 is removing. We flag only
    # unambiguous affiliate networks / explicit affiliate tokens.
    _AFFILIATE_MARKERS = (
        "affiliate", "aff_id", "affid=", "/aff/", "clickbank.net",
        "amzn.to", "shareasale.com", "cj.com", "anrdoezrs.net",
        "impact.com", "awin1.com", "linksynergy.com", "rakuten",
        "prf.hn", "sjv.io", "redirectingat.com",
    )
    affiliate_missing = [
        l for l in links
        if l["is_dofollow"]
        and not l.get("is_sponsored") and not l.get("is_ugc")
        and any(m in (l.get("url") or "").lower() for m in _AFFILIATE_MARKERS)
    ]
    if affiliate_missing:
        n = len(affiliate_missing)
        issues.append({
            "issue": f"Paid/Affiliate Links Missing rel='sponsored' ({n})", "category": "External Links",
            "severity": "Warning", "impact_score": 5, "effort": "Low",
            "recommendation": "Google's link-spam policy requires rel=\"sponsored\" (or rel=\"nofollow\") on paid, affiliate, and sponsored links. These follow links point to affiliate/ad/tracking destinations without a rel qualifier: add rel=\"sponsored\" to stay compliant and avoid manual actions.",
            "affected": [
                {"value": l["url"], "detail": "follow link, no rel='sponsored'"}
                for l in affiliate_missing
            ][:50],
        })
    if weak_a > 0:
        issues.append({
            "issue": f"Weak Anchor Text on {weak_a} External Link(s)", "category": "External Links",
            "severity": "Low", "impact_score": 3, "effort": "Low",
            "recommendation": "Use descriptive anchor text for external links rather than generic phrases.",
            "affected": [
                {"value": l.get("anchor_text", "") or "[no text]", "detail": f"→ {l['url']}"}
                for l in links if l.get("is_weak_anchor")
            ][:50],
        })

    return {
        "total_links":              len(links),
        "unique_domains":           len(unique_domains),
        "domains":                  unique_domains[:30],
        "dofollow_count":           dofollow,
        "nofollow_count":           nofollow,
        "sponsored_count":          sponsored,
        "ugc_count":                ugc,
        "broken_count":             broken,
        "blocked_count":            blocked,
        "redirect_count":           redirect,
        "new_tab_count":            new_tab,
        "same_tab_count":           same_tab,
        "missing_noopener_count":   miss_noop,
        "missing_noreferrer_count": miss_noref,
        "no_security_count":        no_security,
        "weak_anchor_count":        weak_a,
        "image_link_count":         img_links,
        "links":                    links[:200],
        "issues":                   issues,
    }
