"""
pagespeed.py
Google PageSpeed Insights API v5 client.
Returns real Lighthouse scores and CWV values.
No API key required for anonymous usage (100 req/day per IP).
Provide an API key for higher quotas (25 000 req/day).

FIELD vs LAB data
-----------------
The PSI response carries two very different classes of Core Web Vitals:

* FIELD data (CrUX) lives under the top-level ``loadingExperience`` /
  ``originLoadingExperience`` keys. It is what *real users* experienced over
  the trailing 28 days, reported at the 75th percentile (p75). THIS is the
  ranking-relevant signal: Google ranks on field CWV, not lab scores.
* LAB data (Lighthouse) lives under ``lighthouseResult.audits``. It is a
  single synthetic run in a controlled environment. It is diagnostic only
  (great for finding *why* something is slow) and is NOT used for ranking.

Also note: FID (First Input Delay) is deprecated and was replaced by INP
(Interaction to Next Paint) as a Core Web Vital in March 2024. We surface INP
from both field and lab data and do not report FID.
"""

import requests

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
PSI_TIMEOUT  = 90   # PSI fetches and renders the target page: needs extra headroom
PSI_RETRIES  = 2    # retry on timeout before giving up


def _score_to_status(score):
    """Convert a 0–1 Lighthouse score to pass/warning/fail/info."""
    if score is None:
        return "info"
    if score >= 0.9:
        return "pass"
    if score >= 0.5:
        return "warning"
    return "fail"


def _extract_metric(audits, key):
    a = audits.get(key, {})
    return {
        "value":        a.get("displayValue", "N/A"),
        "numericValue": a.get("numericValue"),
        "score":        a.get("score"),
        "status":       _score_to_status(a.get("score")),
    }


# CrUX (field) "good" thresholds, measured at p75 (the value Google uses).
# Each entry: (good_max, poor_min). "good" if value <= good_max, "poor" if
# value > poor_min, otherwise "needs-improvement".
_FIELD_THRESHOLDS = {
    "lcp":  (2500.0, 4000.0),   # ms
    "inp":  (200.0,  500.0),    # ms  (replaced FID as a Core Web Vital, Mar 2024)
    "cls":  (0.10,   0.25),     # unitless (0-1 scale)
    "fcp":  (1800.0, 3000.0),   # ms
    "ttfb": (800.0,  1800.0),   # ms
}

# CrUX metric key -> our short name.
_FIELD_KEY_MAP = {
    "LARGEST_CONTENTFUL_PAINT_MS":     "lcp",
    "INTERACTION_TO_NEXT_PAINT":       "inp",
    "CUMULATIVE_LAYOUT_SHIFT_SCORE":   "cls",
    "FIRST_CONTENTFUL_PAINT_MS":       "fcp",
    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": "ttfb",
    "TIME_TO_FIRST_BYTE":              "ttfb",
}


def _normalise_cls(percentile):
    """
    CrUX returns CLS as an integer percentile scaled *100 in some responses
    (e.g. 5 meaning 0.05), while CLS is conceptually a 0-1 score. Normalise so
    the returned value is always on the 0-1 scale.

    A raw percentile > 1 is treated as the *100 integer form and divided by 100;
    a value already <= 1 is assumed to be the real score and left untouched.
    """
    if percentile is None:
        return None
    try:
        p = float(percentile)
    except (TypeError, ValueError):
        return None
    return p / 100.0 if p > 1 else p


def _rate_field(short_name, value):
    """Judge a field metric value against the p75 'good' thresholds."""
    thresholds = _FIELD_THRESHOLDS.get(short_name)
    if thresholds is None or value is None:
        return "info"
    good_max, poor_min = thresholds
    if value <= good_max:
        return "good"
    if value > poor_min:
        return "poor"
    return "needs-improvement"


def _parse_field_metrics(loading_experience):
    """
    Build the CrUX (field / real-user) metrics object from a PSI
    ``loadingExperience`` (or ``originLoadingExperience``) block.

    Returns a dict of {short_name: {percentile, category, status}} for every
    recognised metric present, or {} if nothing usable is found. Fully
    defensive: never raises on a malformed / partial block.
    """
    out = {}
    try:
        metrics = (loading_experience or {}).get("metrics", {}) or {}
    except AttributeError:
        return out

    for crux_key, short_name in _FIELD_KEY_MAP.items():
        try:
            m = metrics.get(crux_key)
            if not isinstance(m, dict):
                continue
            percentile = m.get("percentile")
            if short_name == "cls":
                percentile = _normalise_cls(percentile)
            # CrUX category: "FAST" / "AVERAGE" / "SLOW"
            # (i.e. good / needs-improvement / poor).
            category = m.get("category")
            out[short_name] = {
                "percentile": percentile,
                "category":   category,
                "status":     _rate_field(short_name, percentile),
            }
        except Exception:
            # A single malformed metric must not lose the others.
            continue
    return out


def fetch_pagespeed(url, strategy="mobile", api_key=None):
    """
    Call PageSpeed Insights API and return structured results.

    Parameters
    ----------
    url : str
        The page URL to analyze.
    strategy : str
        "mobile" or "desktop".
    api_key : str, optional
        Google API key. If None, auto-fetched from APIKeyManager (falls back to anonymous).

    Returns
    -------
    dict
        Keys: success, performance_score, accessibility_score, seo_score,
              best_practices_score, strategy, fcp, lcp, tbt, cls, si, ttfb, inp,
              opportunities, source.
        On failure: success=False, error=str.
    """
    # No internal key lookup here: callers (api/audit-pipeline.py, api/ai.py)
    # resolve the PSI key from env or the org vault and pass it in explicitly.
    # If api_key is None we simply use PSI's anonymous quota. (Previously this
    # imported a non-existent modules.api_key_manager whose ImportError was
    # silently swallowed, so the fallback never actually did anything.)
    params = {"url": url, "strategy": strategy, "category": ["performance", "accessibility", "seo", "best-practices"]}
    if api_key:
        params["key"] = api_key

    last_error = "Unknown error"
    for attempt in range(1, PSI_RETRIES + 1):
        try:
            resp = requests.get(PSI_ENDPOINT, params=params, timeout=PSI_TIMEOUT)
            if resp.status_code == 429:
                return {
                    "success": False,
                    "error_code": 429,
                    "error": (
                        "Rate limit reached for anonymous PSI requests. "
                        "Add a free Google API key to get 25,000 requests/day."
                    ),
                }
            if resp.status_code == 400:
                return {"success": False, "error_code": 400,
                        "error": "Invalid URL or request rejected by PageSpeed Insights."}
            resp.raise_for_status()
            data = resp.json()
            break  # success: exit retry loop
        except requests.exceptions.Timeout:
            last_error = f"PageSpeed Insights request timed out ({PSI_TIMEOUT}s) on attempt {attempt}/{PSI_RETRIES}."
            if attempt == PSI_RETRIES:
                return {"success": False, "error_code": 0,
                        "error": f"PageSpeed Insights timed out after {PSI_RETRIES} attempts ({PSI_TIMEOUT}s each). "
                                 f"Google's servers may be slow, try again in a moment."}
            continue  # retry
        except Exception as e:
            return {"success": False, "error_code": 0, "error": str(e)}
    else:
        return {"success": False, "error_code": 0, "error": last_error}

    lhr    = data.get("lighthouseResult", {})
    cats   = lhr.get("categories", {})
    audits = lhr.get("audits", {})

    def _cat_score(key):
        raw = (cats.get(key, {}) or {}).get("score")
        return round(raw * 100) if raw is not None else None

    # Core metrics
    fcp  = _extract_metric(audits, "first-contentful-paint")
    lcp  = _extract_metric(audits, "largest-contentful-paint")
    tbt  = _extract_metric(audits, "total-blocking-time")
    cls  = _extract_metric(audits, "cumulative-layout-shift")
    si   = _extract_metric(audits, "speed-index")
    ttfb = _extract_metric(audits, "server-response-time")
    inp  = _extract_metric(audits, "interaction-to-next-paint")
    if not inp.get("value") or inp["value"] == "N/A":
        inp = _extract_metric(audits, "experimental-interaction-to-next-paint")
    if not inp.get("value") or inp["value"] == "N/A":
        inp = {"value": "Not available", "numericValue": None, "score": None, "status": "info"}

    # ---- FIELD (CrUX / real-user) data --------------------------------------
    # This is the ranking-relevant signal. Prefer page-level data
    # (loadingExperience); fall back to origin-level (originLoadingExperience)
    # when the specific URL has too little traffic for its own CrUX record.
    # Common on low-traffic pages: no CrUX data at all -> field stays None and
    # the lab path above is completely unaffected.
    field = None
    field_overall = None
    field_scope = None
    try:
        page_le   = data.get("loadingExperience", {}) or {}
        origin_le = data.get("originLoadingExperience", {}) or {}

        page_metrics = _parse_field_metrics(page_le)
        if page_metrics:
            field         = page_metrics
            field_overall = page_le.get("overall_category")
            field_scope   = "page"
        else:
            origin_metrics = _parse_field_metrics(origin_le)
            if origin_metrics:
                field         = origin_metrics
                field_overall = origin_le.get("overall_category")
                field_scope   = "origin"   # origin-level fallback
        if field is not None:
            field["scope"] = field_scope   # "page" or "origin" fallback
    except Exception:
        # Never let a field-parsing problem break the lab response.
        field = None
        field_overall = None

    # Opportunities (audits that could save time/bytes)
    opps = []
    for aid, a in audits.items():
        if (
            isinstance(a, dict)
            and a.get("details", {}).get("type") == "opportunity"
            and a.get("score") is not None
            and a.get("score") < 0.9
        ):
            opps.append({
                "id":           aid,
                "title":        a.get("title", ""),
                "description":  a.get("description", ""),
                "displayValue": a.get("displayValue", ""),
                "score":        a.get("score"),
            })
    opps.sort(key=lambda x: x["score"] or 0)

    # Image sizes: pulled from Lighthouse audits so they work even on CDNs
    # that block server-side requests (Cloudflare, Webflow, etc.)
    image_sizes = {}
    for _audit_key in ("network-requests",):
        for _item in (audits.get(_audit_key, {}).get("details", {}).get("items", []) or []):
            _url  = _item.get("url") or ""
            _size = _item.get("resourceSize") or _item.get("transferSize") or 0
            _type = (_item.get("resourceType") or "").lower()
            if _url and _size and _type == "image":
                image_sizes[_url] = int(_size)
    for _audit_key in ("uses-optimized-images", "uses-responsive-images",
                       "modern-image-formats", "efficiently-encode-images"):
        for _item in (audits.get(_audit_key, {}).get("details", {}).get("items", []) or []):
            _url  = _item.get("url") or ""
            _size = _item.get("totalBytes") or 0
            if _url and _size and _url not in image_sizes:
                image_sizes[_url] = int(_size)

    return {
        "success":             True,
        "source":              "PageSpeed Insights (Lighthouse)",
        "strategy":            strategy,
        "performance_score":   _cat_score("performance"),
        "accessibility_score": _cat_score("accessibility"),
        "seo_score":           _cat_score("seo"),
        "best_practices_score":_cat_score("best-practices"),
        "fcp":  fcp,
        "lcp":  lcp,
        "tbt":  tbt,
        "cls":  cls,
        "si":   si,
        "ttfb": ttfb,
        "inp":  inp,
        "field":          field,          # CrUX real-user CWV (ranking signal) or None
        "field_overall":  field_overall,  # CrUX overall_category: FAST/AVERAGE/SLOW or None
        "opportunities":  opps[:10],
        "image_sizes":    image_sizes,
    }
