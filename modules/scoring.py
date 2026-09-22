"""SEO Health Score calculation (0–100) with thematic category grouping."""

WEIGHTS = {
    "metadata":        0.16,
    "headings":        0.08,
    "canonical":       0.05,
    "indexability":    0.06,
    "url_structure":   0.05,
    "content":         0.15,
    "images":          0.07,
    "internal_links":  0.11,
    "external_links":  0.04,
    "advanced":        0.08,   # mobile, schema, social, hreflang
    "site_health":     0.10,   # domain age, SSL, DNS/SPF/DMARC/MX, robots, sitemap
    "page_specific":   0.05,   # course / blog
}

PENALTY = {
    "Critical": 25,
    "High":     15,
    "Medium":   8,
    "Warning":  6,   # Warning < Medium: it is a caution, not a confirmed problem
    "Low":      2,
    "Notice":   0,   # advisory only (browser-implied hardening, length overages,
                     # HTML5 multi-H1): surfaced for awareness, never drags score
}

# SEMrush-style thematic groupings.
# NOTE: matching is substring (`keyword.lower() in category.lower()`), so keywords
# must be substrings of the EXACT category strings the check modules emit (grep
# `"category":` across modules/*.py). Past bug: keyword "Headings" never matched
# the emitted category "Heading Structure" ("headings" is not a substring of
# "heading structure"), silently dumping every heading finding into "Other";
# likewise the mobile categories (Responsiveness/Usability/Navigation/User
# Experience/Layout) and "Security" (mixed-content) had no keyword and fell to
# "Other". Keep this table in sync with lib/aggregate.ts's THEMES.
THEMES = {
    "Crawlability": ["Accessibility", "Redirects", "Indexability", "URL Structure"],
    "Metadata":     ["Metadata"],
    # Page-Specific MUST come before Content: the generic "Content" keyword is a
    # substring of "Course Content"/"Blog Content", so if Content were checked
    # first those page-specific issues would be misfiled under Content (the match
    # loop breaks on the first hit). Keep this ordering in sync with
    # lib/aggregate.ts's THEMES.
    "Page-Specific": ["Course Content", "Blog Content", "Conversion"],
    "Content":      ["Content", "Heading", "Readability"],
    "Links":        ["Internal Links", "External Links"],
    "Technical":    ["Canonical", "Technical", "Mobile", "Performance",
                     "Responsiveness", "Usability", "Navigation",
                     "User Experience", "Layout"],
    "Social & Schema": ["Structured Data", "Social SEO", "International SEO"],
    "Images":       ["Images", "Image SEO"],
    "Site Health":  ["Site Health", "Security"],
}


def _category_score(issues):
    if not issues:
        return 100.0
    penalty = sum(PENALTY.get(i.get("severity", "Low"), 2) for i in issues)
    return max(0.0, 100.0 - penalty)


def calculate_seo_score(result):
    breakdown = {
        "metadata":       _category_score(result.get("metadata",    {}).get("issues", [])),
        # Use heading_detail (deep checks): heading{} is the legacy shallow checker
        "headings":       _category_score(result.get("heading_detail", result.get("headings", {})).get("issues", [])),
        "canonical":      _category_score(result.get("canonical",   {}).get("issues", [])),
        "indexability":   _category_score(result.get("indexability",{}).get("issues", [])),
        "url_structure":  _category_score(result.get("url_structure",{}).get("issues", [])),
        "content":        _category_score(result.get("content",     {}).get("issues", [])),
        # Use image_detail (deep checks): images{} is the legacy shallow checker
        "images":         _category_score(result.get("image_detail", result.get("images", {})).get("issues", [])),
        "internal_links": _category_score(result.get("internal_links",{}).get("issues", [])),
        "external_links": _category_score(result.get("external_links",{}).get("issues", [])),
        # `mobile_audit` MUST be included here: its issues are appended to
        # all_issues (auditor.py) so they show in the UI/counts/AI, but they were
        # previously not in any scoring bucket - a mobile-broken page (missing
        # viewport, intrusive interstitial) could still score 90+. Folded into
        # the "advanced" bucket, matching the WEIGHTS comment above.
        "advanced":       _category_score(
                              result.get("advanced", {}).get("issues", []) +
                              result.get("redirect_analysis", {}).get("issues", []) +
                              result.get("mobile_audit", {}).get("issues", [])
                          ),
        "site_health":    _category_score(result.get("site_health", {}).get("issues", [])),
        "page_specific":  _category_score(
                              result.get("course_audit", {}).get("issues", []) +
                              result.get("blog_audit",   {}).get("issues", [])
                          ),
    }

    total = sum(breakdown[cat] * weight for cat, weight in WEIGHTS.items())

    status = result.get("status_code", 200)
    if result.get("fetch_error") or status == 0:
        total = 0.0
    elif status >= 400:
        total = max(0.0, total - 50)
    elif 300 <= status < 400:
        total = max(0.0, total - 10)

    return {"score": round(total, 1), "breakdown": breakdown}


def get_thematic_issues(all_issues):
    """Group issues into SEMrush-style thematic categories."""
    grouped = {theme: [] for theme in THEMES}
    other = []
    for issue in all_issues:
        cat = issue.get("category", "")
        placed = False
        for theme, categories in THEMES.items():
            if any(c.lower() in cat.lower() for c in categories):
                grouped[theme].append(issue)
                placed = True
                break
        if not placed:
            other.append(issue)
    if other:
        grouped["Other"] = other
    return {k: v for k, v in grouped.items() if v}


def get_top_issues_by_impact(all_issues, top_n=10):
    """Return top N issues sorted by impact_score descending."""
    sorted_issues = sorted(all_issues, key=lambda x: x.get("impact_score", 0), reverse=True)
    return sorted_issues[:top_n]


