"""
image_auditor.py
Advanced image SEO analysis for SEO Technical Audit Dashboard.
"""

import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from modules.auditor import safe_request


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENERIC_ALT_PATTERNS = {
    "image", "img", "photo", "pic", "picture", "banner", "logo", "icon",
    "thumbnail", "placeholder", "image1", "img1", "photo1", "screenshot",
    "untitled",
}

# A generic auto-generated filename stem. The token must be followed by a digit,
# a separator, or end-of-stem so ordinary words that merely START with these
# letters are not flagged: "photography-guide" / "imagery-hero" / "imagine" are
# descriptive filenames, not "photo"/"image"/"img" boilerplate. Prior pattern
# used `\d*` (zero-or-more) with no boundary, so `^photo` matched "photography".
BAD_NAMING_RE = re.compile(
    r"^(img|image|dsc|screenshot|untitled|photo)(\d+|[-_ ]|$)|IMG_\d+",
    re.IGNORECASE,
)

EXTENSION_TO_FORMAT = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WebP",
    "svg": "SVG",
    "gif": "GIF",
    "avif": "AVIF",
}

SIZE_LABELS = [
    (100 * 1024, "< 100KB"),
    (200 * 1024, "100–200KB"),
    (500 * 1024, "200–500KB"),
]


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _get_extension(url_or_path):
    """Return lowercased file extension without leading dot, or 'unknown'."""
    path = urlparse(url_or_path).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower().split("?")[0]
        return ext if ext else "unknown"
    return "unknown"


def _file_size_label(size_bytes):
    if size_bytes is None:
        return "Unknown"
    for threshold, label in SIZE_LABELS:
        if size_bytes < threshold:
            return label
    return "> 500KB"


def _is_keyword_stuffed(alt_text):
    """Keyword stuffing is REPEATING keywords to game ranking, not merely long
    or descriptive alt text. The prior heuristic flagged any alt over 100 chars,
    which mislabeled long *descriptive* alt (e.g. "Team collaborating in a modern
    office during a corporate leadership training workshop") as stuffed - a false
    positive seen across the site. Detect real repetition instead: a content word
    repeated 3+ times, or low lexical diversity on a multi-word alt.
    """
    words = re.findall(r"[a-z0-9]+", alt_text.lower())
    if len(words) < 5:
        return False
    counts = Counter(words)
    # A meaningful content word (len > 3, i.e. not "the"/"and"/"of") repeated 3+ times.
    if any(count >= 3 and len(word) > 3 for word, count in counts.items()):
        return True
    # Very low unique-word ratio on a longer alt = padded with repeats.
    if len(words) >= 8 and len(set(words)) / len(words) < 0.5:
        return True
    return False


def _alt_status(alt_attr):
    """
    Classify alt attribute.
    Returns: "missing" | "empty" | "generic" | "keyword_stuffed" | "ok"
    """
    if alt_attr is None:
        return "missing"
    stripped = alt_attr.strip()
    if stripped == "":
        return "empty"
    if _is_keyword_stuffed(stripped):
        return "keyword_stuffed"
    if stripped.lower() in GENERIC_ALT_PATTERNS:
        return "generic"
    return "ok"


def _naming_quality(filename):
    """Return "bad" if filename matches bad naming patterns, else "good"."""
    if not filename:
        return "bad"
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return "bad" if BAD_NAMING_RE.search(stem) else "good"


def _resolve_url(src, base_url):
    """Resolve src to absolute URL using base_url."""
    if not src:
        return ""
    return urljoin(base_url, src) if base_url else src


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def _fetch_size(url, referer=None):
    """
    Fetch image file size and reachability.
    Returns (url, size_bytes_or_None, status_info) where status_info is
    {"reachable": bool, "status_code": int|None, "error": str|None}.

    Strategy:
    1. HEAD: fast, no body download.
    2. Range GET bytes=0-1: extracts total from Content-Range header.
    3. Streaming GET: reads only response headers, no body download.
    """
    if not REQUESTS_AVAILABLE:
        return url, None, {"reachable": None, "status_code": None, "error": "requests unavailable"}
    hdrs = dict(_BROWSER_HEADERS)
    if referer:
        hdrs["Referer"] = referer

    def _cl(h):
        v = h.get("Content-Length") or h.get("x-content-length") or h.get("x-uncompressed-content-length")
        return int(v) if v and str(v).isdigit() and int(v) > 1 else None

    def _get(method, *args, **kwargs):
        """Try with TLS verification first; fall back if SSLError."""
        import requests as _r
        try:
            return method(*args, **kwargs, verify=True)
        except _r.exceptions.SSLError:
            return method(*args, **kwargs, verify=False)

    def _safe_fetch(method, target_url, *, max_redirects=5, **kwargs):
        """SSRF-safe fetch with a TLS-verify fallback: shares
        `auditor.safe_request`'s per-hop redirect re-validation, wrapping
        `method` so each attempt still gets the verify=True-then-False retry
        image size-checking needs (self-signed certs are common on the sites
        this audits)."""
        return safe_request(
            lambda u, **kw: _get(method, u, **kw), target_url, max_redirects=max_redirects, **kwargs
        )

    try:
        # ── 1. HEAD ───────────────────────────────────────────────────────
        r = _safe_fetch(requests.head, url, timeout=10, headers=hdrs)
        if r.status_code < 400:
            sz = _cl(r.headers)
            if sz:
                return url, sz, {"reachable": True, "status_code": r.status_code, "error": None}

        # ── 2. Range GET ──────────────────────────────────────────────────
        r2 = _safe_fetch(requests.get, url, timeout=10,
                         headers={**hdrs, "Range": "bytes=0-1"}, stream=True)
        r2.close()
        if r2.status_code in (200, 206):
            status2 = {"reachable": True, "status_code": r2.status_code, "error": None}
            cr = r2.headers.get("Content-Range", "")
            if "/" in cr:
                total = cr.split("/")[-1].strip()
                if total.isdigit() and int(total) > 1:
                    return url, int(total), status2
            sz = _cl(r2.headers)
            if sz:
                return url, sz, status2

        # ── 3. Streaming GET (headers only, no body) ──────────────────────
        r3 = _safe_fetch(requests.get, url, timeout=10, headers=hdrs, stream=True)
        r3.close()
        # Use the LAST attempted status code for reachability: a 200 here
        # means the image is fine even if size couldn't be determined; a 4xx/5xx
        # across every attempt means the image genuinely doesn't load.
        final_code = r3.status_code
        reachable = final_code < 400
        if reachable:
            sz = _cl(r3.headers)
            if sz:
                return url, sz, {"reachable": True, "status_code": final_code, "error": None}
        return url, None, {
            "reachable": reachable,
            "status_code": final_code,
            "error": None if reachable else f"HTTP {final_code}",
        }
    except requests.exceptions.Timeout:
        return url, None, {"reachable": False, "status_code": None, "error": "Timeout"}
    except requests.exceptions.SSLError:
        return url, None, {"reachable": False, "status_code": None, "error": "SSL Error"}
    except requests.exceptions.ConnectionError:
        return url, None, {"reachable": False, "status_code": None, "error": "Connection Error"}
    except Exception as e:
        return url, None, {"reachable": False, "status_code": None, "error": str(e)[:60]}


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def _extract_image_data(soup, base_url):
    """
    Extract metadata for every <img> and <source> tag in the document.
    Returns list of image dicts.
    """
    images = []

    for tag in soup.find_all(["img", "source"]):
        # A <source> tag is only image-related inside <picture>: inside
        # <audio>/<video> it points to audio/video media, not an image.
        if tag.name == "source" and tag.find_parent("picture") is None:
            continue

        # Determine source URL
        src = tag.get("src") or tag.get("data-src") or tag.get("data-lazy-src") or ""
        if tag.name == "source":
            src = tag.get("srcset", "").split(",")[0].split()[0] if tag.get("srcset") else src

        url = _resolve_url(src, base_url)
        path = urlparse(url).path
        name = path.rsplit("/", 1)[-1] if "/" in path else path

        ext = _get_extension(url)
        format_label = EXTENSION_TO_FORMAT.get(ext, "Unknown")

        alt_attr = tag.get("alt")  # None if missing, "" if empty
        a_status = _alt_status(alt_attr)

        has_lazy = tag.get("loading", "").lower() == "lazy"

        raw_width = tag.get("width")
        raw_height = tag.get("height")
        try:
            width = int(raw_width) if raw_width else None
        except (ValueError, TypeError):
            width = None
        try:
            height = int(raw_height) if raw_height else None
        except (ValueError, TypeError):
            height = None
        has_dimensions = width is not None and height is not None

        has_srcset = bool(tag.get("srcset"))

        # Check if inside <picture>
        is_in_picture = tag.find_parent("picture") is not None

        nq = _naming_quality(name)

        per_image_issues = []
        # alt / lazy-loading / width-height only apply to the rendered <img>. A
        # <picture>'s <source> elements never carry alt, loading, or width/height
        # (those belong on the fallback <img>), so running these checks on a
        # <source> produced phantom "Missing alt / dimensions / lazy loading"
        # issues for every correctly-authored responsive <picture>.
        if tag.name == "img":
            if a_status == "missing":
                per_image_issues.append("Missing alt text")
            elif a_status == "empty":
                per_image_issues.append("Empty alt text")
            elif a_status == "generic":
                per_image_issues.append("Generic alt text")
            elif a_status == "keyword_stuffed":
                per_image_issues.append("Keyword-stuffed alt text")
            if not has_lazy:
                per_image_issues.append("Missing lazy loading")
            if not has_dimensions:
                per_image_issues.append("Missing width/height dimensions")
        if nq == "bad":
            per_image_issues.append("Poor filename convention")
        if ext in ("jpg", "jpeg", "png"):
            per_image_issues.append("Could be converted to WebP/AVIF")

        images.append({
            "url": url,
            "tag_name": tag.name,  # "img" or "source" (see _compute_summary)
            "name": name,
            "extension": ext,
            "format_label": format_label,
            "alt_text": alt_attr,
            "alt_status": a_status,
            "has_lazy": has_lazy,
            "width": width,
            "height": height,
            "has_dimensions": has_dimensions,
            "has_srcset": has_srcset,
            "is_in_picture": is_in_picture,
            "naming_quality": nq,
            "file_size_bytes": None,
            "file_size_label": "N/A",
            "status_code": None,
            "is_broken": None,
            "fetch_error": None,
            "is_lcp_candidate": False,
            "issues": per_image_issues,
        })

    return images


def _mark_lcp_candidate(images):
    """
    Mark the image most likely to be the page's LCP element.
    SVGs are excluded: browsers never report an SVG as the LCP element.
    Prefers the largest raster image by pixel area; falls back to the
    first non-SVG HTTP image.
    """
    if not images:
        return

    def _mark(img):
        img["is_lcp_candidate"] = True
        # An LCP / hero / above-the-fold image SHOULD NOT be lazy-loaded, so
        # missing lazy loading is not an issue for it - drop that flag so it is
        # not counted or reported.
        if "Missing lazy loading" in (img.get("issues") or []):
            img["issues"].remove("Missing lazy loading")

    raster = [img for img in images if img.get("format_label") not in ("SVG",)
              and img.get("url", "").startswith("http")]
    if not raster:
        return
    with_dims = [
        (img, (img.get("width") or 0) * (img.get("height") or 0))
        for img in raster if img.get("has_dimensions")
    ]
    if with_dims:
        best_img, best_area = max(with_dims, key=lambda x: x[1])
        if best_area > 0:
            _mark(best_img)
            return
    _mark(raster[0])


def _populate_sizes(images, max_size_checks, base_url=""):
    """
    Make HEAD requests for up to max_size_checks unique image URLs and
    populate file_size_bytes / file_size_label / is_broken / status_code.
    """
    if not REQUESTS_AVAILABLE:
        return

    from functools import partial
    seen = {}
    to_check = []
    for img in images:
        url = img["url"]
        if url and url.startswith("http") and url not in seen and len(to_check) < max_size_checks:
            to_check.append(url)
            seen[url] = None

    _fetch = partial(_fetch_size, referer=base_url) if base_url else _fetch_size
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(_fetch, to_check))

    status_map = {url: (size, status) for url, size, status in results}

    for img in images:
        url = img["url"]
        if url in status_map:
            size_bytes, status = status_map[url]
            img["file_size_bytes"] = size_bytes
            img["file_size_label"] = _file_size_label(size_bytes)
            code = status.get("status_code")
            img["status_code"] = code
            # Only a genuinely dead resource is "broken": 404/410 or a hard 5xx.
            # 401/403 (hotlink protection / WAF), 429 (rate limit), 503
            # (transient), and timeout/SSL/connection failures (status_code None)
            # mean "could not verify from a bot" - not that the image is missing
            # in a real browser. Marking those broken flagged perfectly visible
            # CDN/hotlink-protected images as "fails to load".
            img["is_broken"] = code in (404, 410) or (
                code is not None and 500 <= code < 600 and code != 503
            )
            img["fetch_error"] = status.get("error")

            if img["is_broken"] and "Broken image (does not load)" not in img["issues"]:
                img["issues"].append("Broken image (does not load)")
            # Update large image issue flag
            if size_bytes is not None and size_bytes > 300 * 1024:
                if "Large file size (> 300KB)" not in img["issues"]:
                    img["issues"].append("Large file size (> 300KB)")


def _compute_summary(images, check_sizes):
    """Compute aggregate summary counts."""
    total = len(images)
    # alt / lazy / dimensions are <img>-only attributes; a <picture>'s <source>
    # never carries them, so counting <source> here inflated every count and
    # emitted "Missing alt/dimensions/lazy on N image(s)" false positives.
    imgs = [i for i in images if i.get("tag_name", "img") == "img"]
    missing_alt = sum(1 for i in imgs if i["alt_status"] == "missing")
    empty_alt = sum(1 for i in imgs if i["alt_status"] == "empty")
    generic_alt = sum(1 for i in imgs if i["alt_status"] == "generic")
    keyword_stuffed_alt = sum(1 for i in imgs if i["alt_status"] == "keyword_stuffed")
    # LCP / hero / above-the-fold candidates are excluded: those images should
    # NOT be lazy-loaded, so "missing lazy loading" is not a defect for them.
    no_lazy = sum(1 for i in imgs if not i["has_lazy"] and not i.get("is_lcp_candidate"))
    no_dimensions = sum(1 for i in imgs if not i["has_dimensions"])
    non_webp = sum(1 for i in images if i["extension"] in ("jpg", "jpeg", "png"))
    bad_naming = sum(1 for i in images if i["naming_quality"] == "bad")

    # Duplicate alt detection (same non-empty alt on 2+ images)
    alt_counter = defaultdict(int)
    for img in images:
        if img["alt_text"] and img["alt_text"].strip():
            alt_counter[img["alt_text"].strip().lower()] += 1
    duplicate_alt = sum(1 for count in alt_counter.values() if count > 1)

    large_images = 0
    broken_images = 0
    if check_sizes:
        large_images = sum(
            1 for i in images
            if i["file_size_bytes"] is not None and i["file_size_bytes"] > 300 * 1024
        )
        broken_images = sum(1 for i in images if i.get("is_broken") is True)

    format_breakdown = defaultdict(int)
    for img in images:
        format_breakdown[img["format_label"]] += 1

    return {
        "total": total,
        "missing_alt": missing_alt,
        "empty_alt": empty_alt,
        "generic_alt": generic_alt,
        "keyword_stuffed_alt": keyword_stuffed_alt,
        "duplicate_alt": duplicate_alt,
        "no_lazy": no_lazy,
        "no_dimensions": no_dimensions,
        "non_webp_jpg_png": non_webp,
        "bad_naming": bad_naming,
        "large_images": large_images,
        "broken_images": broken_images,
        "format_breakdown": dict(format_breakdown),
    }


def _build_issues(summary, check_sizes, images=None):
    """Build list of SEO issue dicts from summary counts.

    `images` is the per-image list (each has `url` + a `issues` label list); we
    use it to attach `affected` - the exact image src(s) behind each issue - so
    the UI can point the user at the specific offending images, not just a count.
    """
    issues = []
    n = summary
    imgs = images or []

    def _aff(label, detail=None):
        """The exact images whose per-image `issues` include `label`."""
        out = [
            {"value": im.get("url", ""), "detail": detail or label}
            for im in imgs
            if label in (im.get("issues") or [])
        ]
        return out[:50]

    if n["missing_alt"] > 0:
        issues.append({
            "issue": f"Missing alt text on {n['missing_alt']} image(s)",
            "category": "Image SEO",
            "severity": "High",
            "recommendation": "Add descriptive alt text to all images for accessibility and SEO.",
            "impact_score": 7,
            "effort": "Medium",
            "affected": _aff("Missing alt text", "no alt attribute"),
        })

    if n["empty_alt"] > 0:
        # alt="" is correct for decorative images (WCAG); advisory, not a
        # confirmed problem. See the matching note in modules/auditor.py.
        issues.append({
            "issue": f"Empty alt text on {n['empty_alt']} image(s) (verify decorative)",
            "category": "Image SEO",
            "severity": "Low",
            "recommendation": "Empty alt='' is correct for decorative images. Add a description only for images that convey meaning.",
            "impact_score": 2,
            "effort": "Low",
            "affected": _aff("Empty alt text", 'alt=""'),
        })

    if n["generic_alt"] > 0:
        issues.append({
            "issue": f"Generic alt text on {n['generic_alt']} image(s)",
            "category": "Image SEO",
            "severity": "Low",
            "recommendation": "Replace generic alt text with descriptive, keyword-relevant descriptions.",
            "impact_score": 3,
            "effort": "Medium",
            "affected": _aff("Generic alt text"),
        })

    if n["keyword_stuffed_alt"] > 0:
        issues.append({
            "issue": f"Keyword-stuffed alt text on {n['keyword_stuffed_alt']} image(s)",
            "category": "Image SEO",
            "severity": "Warning",
            "recommendation": "Keep alt text concise and natural; avoid over-optimisation.",
            "impact_score": 5,
            "effort": "Low",
            "affected": _aff("Keyword-stuffed alt text"),
        })

    if n["duplicate_alt"] > 0:
        # No single per-image label marks a duplicate; find the images that
        # share a non-empty alt value so the UI can point at the exact ones.
        alt_counts = Counter(
            (im.get("alt_text") or "").strip()
            for im in imgs
            if (im.get("alt_text") or "").strip()
        )
        dup_aff = [
            {"value": im.get("url", ""), "detail": f'alt="{(im.get("alt_text") or "").strip()}"'}
            for im in imgs
            if (im.get("alt_text") or "").strip()
            and alt_counts[(im.get("alt_text") or "").strip()] > 1
        ][:50]
        issues.append({
            "issue": f"Duplicate alt text on {n['duplicate_alt']} image(s)",
            "category": "Image SEO",
            "severity": "Warning",
            "recommendation": "Use unique alt text for each image to provide distinct context.",
            "impact_score": 4,
            "effort": "Medium",
            "affected": dup_aff,
        })

    if n["no_lazy"] > 0:
        issues.append({
            "issue": f"{n['no_lazy']} image(s) missing lazy loading",
            "category": "Performance",
            "severity": "Notice",
            "recommendation": (
                "Add loading='lazy' to below-the-fold images to improve page load speed. "
                "Do NOT lazy-load above-the-fold or LCP/hero images - lazy-loading the "
                "largest visible image delays LCP and hurts Core Web Vitals. The likely "
                "LCP candidate has already been excluded from this count."
            ),
            "impact_score": 4,
            "effort": "Low",
            "affected": _aff("Missing lazy loading"),
        })

    if n["no_dimensions"] > 0:
        issues.append({
            "issue": f"{n['no_dimensions']} image(s) missing width/height dimensions",
            "category": "Performance",
            "severity": "Medium",
            "recommendation": (
                "Specify width and height attributes to prevent layout shifts (CLS). "
                "A CSS aspect-ratio (or width/height in CSS) also reserves the space and "
                "satisfies this - the image does not need HTML width/height attributes if "
                "the layout already reserves its dimensions."
            ),
            "impact_score": 6,
            "effort": "Low",
            "affected": _aff("Missing width/height dimensions"),
        })

    if n["non_webp_jpg_png"] > 0:
        issues.append({
            "issue": f"{n['non_webp_jpg_png']} image(s) could be converted to WebP",
            "category": "Performance",
            "severity": "Low",
            "recommendation": "Convert JPEG/PNG images to WebP or AVIF for better compression.",
            "impact_score": 4,
            "effort": "Medium",
            "affected": _aff("Could be converted to WebP/AVIF"),
        })

    if n["bad_naming"] > 0:
        issues.append({
            "issue": f"{n['bad_naming']} image(s) have poor filename conventions",
            "category": "Image SEO",
            "severity": "Low",
            "recommendation": "Use descriptive, hyphen-separated filenames instead of generic names like img001.jpg.",
            "impact_score": 3,
            "effort": "Medium",
            "affected": _aff("Poor filename convention"),
        })

    if check_sizes and n.get("broken_images", 0) > 0:
        issues.append({
            "issue": f"{n['broken_images']} image(s) fail to load",
            "category": "Image SEO",
            "severity": "Critical",
            "recommendation": "Fix or remove broken image references: check the URL is correct and the file still exists on the server.",
            "impact_score": 9,
            "effort": "Low",
            "affected": _aff("Broken image (does not load)"),
        })

    if check_sizes and n["large_images"] > 0:
        issues.append({
            "issue": f"{n['large_images']} image(s) are larger than 300KB",
            "category": "Performance",
            "severity": "Warning",
            "recommendation": "Compress images or switch to WebP/AVIF to keep file sizes under 300KB.",
            "impact_score": 8,
            "effort": "Medium",
            "affected": _aff("Large file size (> 300KB)"),
        })

    return issues


def _format_opportunity(images):
    """Return list of images where extension is jpg/png (WebP/AVIF upgrade candidates)."""
    return [
        img for img in images if img["extension"] in ("jpg", "jpeg", "png")
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_images_advanced(soup, base_url="", check_sizes=False, max_size_checks=30):
    """
    Advanced image SEO analysis.

    Parameters
    ----------
    soup : BeautifulSoup
        Parsed HTML document.
    base_url : str
        Base URL for resolving relative image paths.
    check_sizes : bool
        If True, perform HEAD requests to fetch file sizes.
    max_size_checks : int
        Maximum number of unique URLs to check for file size.

    Returns
    -------
    dict
        Comprehensive image analysis results.
    """
    images = _extract_image_data(soup, base_url)
    _mark_lcp_candidate(images)
    # LCP image must NOT be lazy-loaded: remove that per-image issue for it
    for img in images:
        if img.get("is_lcp_candidate"):
            img["issues"] = [i for i in img.get("issues", []) if i != "Missing lazy loading"]

    if check_sizes and images:
        _populate_sizes(images, max_size_checks, base_url=base_url)

    summary = _compute_summary(images, check_sizes)
    issues = _build_issues(summary, check_sizes, images)
    format_opportunity = _format_opportunity(images)

    return {
        "images": images,
        "summary": summary,
        "format_breakdown": summary["format_breakdown"],
        "format_opportunity": format_opportunity,
        "issues": issues,
        # Flat access for page_image_seo() KPI strip and overview table
        "total": summary["total"],
        "missing_alt": summary["missing_alt"],
        "empty_alt": summary["empty_alt"],
        "generic_alt": summary["generic_alt"],
        "keyword_stuffed_alt": summary["keyword_stuffed_alt"],
        "duplicate_alt": summary["duplicate_alt"],
        "no_lazy": summary["no_lazy"],
        "no_dimensions": summary["no_dimensions"],
        "non_webp_jpg_png": summary["non_webp_jpg_png"],
        "bad_naming": summary["bad_naming"],
        "large_images": summary["large_images"],
        "broken_images": summary["broken_images"],
    }
