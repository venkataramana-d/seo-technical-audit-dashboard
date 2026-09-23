"""Technical SEO Audit use-case checklist: the 35-check composite view ported
from the standalone Streamlit tool's tools/phase1.py / docs/USECASE_GUIDES.md
"Technical SEO" use case (crawlability + on-page + site-health, no API key
required).

Unlike modules/scoring.py (which only ever emits an issue when something is
wrong), this module produces an explicit pass/warning/fail verdict for every
one of the 35 named checks (including the ones that already look fine), so
the UI can render the same 35-item checklist the source tool did, grouped by
crawlability / on-page / site-health.

This is a *read-only view* over data already computed by modules/auditor.py,
modules/advanced_checks.py, and modules/technical_checks.py: it derives
status from existing result fields and never re-fetches or re-scores.
"""

TTFB_GOOD_S = 0.2
TTFB_OK_S = 0.5


def _item(check_id, label, group, status, detail=""):
    return {"id": check_id, "label": label, "group": group, "status": status, "detail": detail}


def _severity_status(issues, fail_severities=("Critical", "High")):
    """Derive pass/warning/fail from a list of issue dicts.

    "Notice" is advisory-only (0 score penalty) and must not turn a check yellow:
    a page whose only findings are Notices is passing. So we ignore Notices when
    deciding warning vs pass.
    """
    real_issues = [i for i in issues if i.get("severity") != "Notice"]
    if not real_issues:
        return "pass"
    severities = {i.get("severity") for i in real_issues}
    if severities & set(fail_severities):
        return "fail"
    return "warning"


def build_technical_audit_checklist(result: dict) -> dict:
    """Build the 35-check Technical SEO Audit checklist from an audit_url() result."""
    metadata = result.get("metadata", {}) or {}
    canonical = result.get("canonical", {}) or {}
    indexability = result.get("indexability", {}) or {}
    url_structure = result.get("url_structure", {}) or {}
    content = result.get("content", {}) or {}
    heading_detail = result.get("heading_detail", result.get("headings", {})) or {}
    image_detail = result.get("image_detail", result.get("images", {})) or {}
    advanced = result.get("advanced", {}) or {}
    technical_seo = advanced.get("technical_seo", {}) or {}
    http_headers_data = advanced.get("http_headers_data", {}) or {}
    redirect_analysis = result.get("redirect_analysis", {}) or {}
    internal_links = result.get("internal_links", {}) or {}
    site_health = result.get("site_health", {}) or {}

    checks = []

    # ── Crawlability (12) ───────────────────────────────────────────────────
    robots = site_health.get("robots", {})
    if robots.get("allowed") is False or robots.get("googlebot_allowed") is False:
        robots_status = "fail"
    else:
        robots_status = "pass"
    checks.append(_item("robots_check", "robots.txt allows crawling", "crawlability",
        robots_status, "Blocked by robots.txt" if robots_status == "fail" else "Crawlable"))

    status_code = result.get("status_code", 0)
    if 200 <= status_code < 300:
        http_status = "pass"
    elif 300 <= status_code < 400:
        http_status = "warning"
    else:
        http_status = "fail"
    checks.append(_item("http_status_check", "HTTP status healthy", "crawlability",
        http_status, f"Status {status_code}"))

    chain_len = redirect_analysis.get("chain_length", 0)
    if chain_len <= 1:
        redirect_status = "pass"
    elif chain_len <= 3:
        redirect_status = "warning"
    else:
        redirect_status = "fail"
    checks.append(_item("redirect_check", "No excessive redirect chain", "crawlability",
        redirect_status, f"{chain_len} hop(s)"))

    broken_count = internal_links.get("broken_count", 0)
    checks.append(_item("broken_link_check", "No broken internal links", "crawlability",
        "fail" if broken_count > 0 else "pass", f"{broken_count} broken"))

    total_internal = internal_links.get("total_links", 0)
    checks.append(_item("internal_links_check", "Has internal links", "crawlability",
        "warning" if total_internal == 0 else "pass", f"{total_internal} internal link(s)"))

    sitemap = site_health.get("sitemap", {})
    if not sitemap.get("exists"):
        sitemap_status = "fail"
    elif any("Malformed" in i.get("issue", "") for i in sitemap.get("issues", [])):
        sitemap_status = "warning"
    else:
        sitemap_status = "pass"
    checks.append(_item("sitemap_validate", "Valid XML sitemap", "crawlability",
        sitemap_status, f"{sitemap.get('url_count', 0)} URL(s)" if sitemap.get("exists") else "Not found"))

    # A missing or non-self-referencing canonical is NOT a warning: Google
    # self-canonicalizes a page that omits the tag, and a canonical pointing
    # elsewhere is often intentional. Only multiple conflicting canonical tags
    # (a genuinely broken state) fail. (Clearly-broken canonical detection is
    # left for a later phase.)
    if canonical.get("canonical_count", 0) > 1:
        canonical_status = "fail"
    else:
        canonical_status = "pass"
    checks.append(_item("canonical_check", "Canonical tag present & self-referencing", "crawlability",
        canonical_status, canonical.get("canonical_url", "") or "No canonical tag (Google self-canonicalizes)"))

    checks.append(_item("meta_robots_check", "Indexable (meta robots / X-Robots-Tag)", "crawlability",
        "fail" if not indexability.get("is_indexable", True) else "pass",
        indexability.get("robots_meta", "") or "index, follow"))

    hreflang_tags = advanced.get("hreflang_tags", [])
    has_xdefault = any(h.get("lang") == "x-default" for h in hreflang_tags)
    if hreflang_tags and not has_xdefault:
        hreflang_status = "warning"
    else:
        hreflang_status = "pass"
    checks.append(_item("hreflang_check", "Hreflang tags valid (if present)", "crawlability",
        hreflang_status, f"{len(hreflang_tags)} hreflang tag(s)"))

    response_time = result.get("response_time", 0.0)
    if response_time < TTFB_GOOD_S:
        ttfb_status = "pass"
    elif response_time < TTFB_OK_S:
        ttfb_status = "warning"
    else:
        ttfb_status = "fail"
    checks.append(_item("ttfb_check", "Time to First Byte", "crawlability",
        ttfb_status, f"{round(response_time * 1000)}ms"))

    url_issues = [i for i in url_structure.get("issues", []) if "HTTPS" not in i.get("issue", "")]
    checks.append(_item("url_structure_check", "Clean URL structure", "crawlability",
        _severity_status(url_issues, fail_severities=("Critical",)),
        f"{url_structure.get('length', 0)} chars"))

    canonical_loop = site_health.get("canonical_loop", {})
    loop_issues = canonical_loop.get("issues", [])
    if any("Loop" in i.get("issue", "") for i in loop_issues):
        loop_status = "fail"
    elif loop_issues:
        loop_status = "warning"
    else:
        loop_status = "pass"
    checks.append(_item("canonical_loop_check", "No canonical redirect loop", "crawlability",
        loop_status, ""))

    # ── On-page (11) ─────────────────────────────────────────────────────────
    # Length outside the ~30-60 pixel-guidance band is advisory, not a warning:
    # only a missing title fails. (Duplicate-title detection is cross-page and
    # handled elsewhere.)
    if not metadata.get("has_title"):
        title_status = "fail"
    else:
        title_status = "pass"
    title_len = metadata.get("title_length", 0)
    title_detail = f"{title_len} chars"
    if title_status == "pass" and not (30 <= title_len <= 60):
        title_detail = f"{title_len} chars (outside 30-60 guidance, advisory)"
    checks.append(_item("title_check", "Title tag present & well-sized", "on_page",
        title_status, title_detail))

    # Length outside the ~150-160 pixel-guidance band is advisory, not a
    # warning: only a missing description fails.
    if not metadata.get("has_description"):
        desc_status = "fail"
    else:
        desc_status = "pass"
    desc_len = metadata.get("description_length", 0)
    desc_detail = f"{desc_len} chars"
    if desc_status == "pass" and not (150 <= desc_len <= 160):
        desc_detail = f"{desc_len} chars (outside 150-160 guidance, advisory)"
    checks.append(_item("meta_description_check", "Meta description present & well-sized", "on_page",
        desc_status, desc_detail))

    checks.append(_item("heading_check", "Heading structure valid", "on_page",
        _severity_status(heading_detail.get("issues", [])), ""))

    # image_detail is analyze_images_advanced()'s output, which exposes the count
    # as "missing_alt" (the legacy result["images"] block used "missing_alt_count").
    # Read the advanced key first so this check doesn't always pass.
    missing_alt = image_detail.get("missing_alt", image_detail.get("missing_alt_count", 0))
    checks.append(_item("image_alt_check", "Images have alt text", "on_page",
        "fail" if missing_alt > 0 else "pass", f"{missing_alt} missing alt"))

    # Thin content is a soft signal: under 300 words is at most a warning
    # (not a fail), and 300-600 words is fine (a short page can fully answer a
    # query). Only genuinely thin pages get flagged.
    word_count = content.get("word_count", 0)
    if word_count < 300:
        wc_status = "warning"
    else:
        wc_status = "pass"
    checks.append(_item("word_count_check", "Sufficient content depth", "on_page",
        wc_status, f"{word_count} words"))

    readability = site_health.get("readability", {})
    checks.append(_item("readability_check", "Readable content", "on_page",
        "warning" if readability.get("issues") else "pass",
        f"Grade {readability.get('fk_grade')}" if readability.get("available") else "N/A"))

    schema_errors = advanced.get("schema_errors", [])
    if schema_errors:
        schema_status = "fail"
    elif not advanced.get("has_schema"):
        schema_status = "warning"
    else:
        schema_status = "pass"
    checks.append(_item("schema_check", "Structured data (JSON-LD) valid", "on_page",
        schema_status, ", ".join(advanced.get("schema_types", [])[:3]) or "None found"))

    og_ok = metadata.get("has_og_tags") and advanced.get("twitter_complete")
    checks.append(_item("og_check", "Open Graph & Twitter Card tags complete", "on_page",
        "pass" if og_ok else "warning", ""))

    if not advanced.get("has_viewport"):
        viewport_status = "fail"
    elif "width=device-width" not in (advanced.get("viewport_content", "") or "").lower():
        viewport_status = "warning"
    else:
        viewport_status = "pass"
    checks.append(_item("viewport_check", "Mobile viewport meta tag", "on_page", viewport_status, ""))

    checks.append(_item("lang_check", "HTML lang attribute set", "on_page",
        "fail" if not advanced.get("lang_attr") else "pass", advanced.get("lang_attr", "")))

    content_freshness = site_health.get("content_freshness", {})
    checks.append(_item("content_freshness_check", "Content freshness signal present", "on_page",
        "warning" if content_freshness.get("issues") else "pass", ""))

    # ── Site health (12) ─────────────────────────────────────────────────────
    ssl = site_health.get("ssl", {})
    if ssl.get("valid") is False:
        ssl_status = "fail"
    elif ssl.get("valid") is None:
        ssl_status = "warning"
    else:
        ssl_status = "pass"
    checks.append(_item("ssl_check", "Valid SSL certificate", "site_health", ssl_status, ""))

    domain_age = site_health.get("domain_age", {})
    age_years = domain_age.get("age_years")
    checks.append(_item("domain_age_check", "Domain age", "site_health",
        "warning" if age_years is not None and age_years < 0.5 else "pass",
        f"{age_years} years" if age_years is not None else "Unknown"))

    checks.append(_item("mixed_content_check", "No mixed content", "site_health",
        "fail" if technical_seo.get("has_mixed_content") else "pass",
        f"{technical_seo.get('mixed_content_count', 0)} resource(s)"))

    https_enforcement = site_health.get("https_enforcement", {})
    checks.append(_item("https_enforcement_check", "HTTP redirects to HTTPS", "site_health",
        "fail" if https_enforcement.get("enforced") is False else "pass", ""))

    security_flags = [
        http_headers_data.get("has_hsts"),
        http_headers_data.get("has_x_frame_options"),
        http_headers_data.get("has_x_content_type_options"),
    ]
    present = sum(1 for f in security_flags if f)
    if present == 0:
        sec_status = "fail"
    elif present < len(security_flags):
        sec_status = "warning"
    else:
        sec_status = "pass"
    checks.append(_item("security_headers_check", "Security headers present", "site_health",
        sec_status, f"{present}/{len(security_flags)} present"))

    # SPF / DMARC / MX are email-deliverability records, not SEO ranking signals.
    # They are reported as "info" (never warning/fail) so they show the data but
    # do not count toward the pass/warning/fail summary or the SEO score.
    dns = site_health.get("dns", {})
    checks.append(_item("spf_check", "SPF record configured", "site_health",
        "info", dns.get("spf") or "Not set (informational only)"))
    checks.append(_item("dmarc_check", "DMARC record configured", "site_health",
        "info", dns.get("dmarc") or "Not set (informational only)"))
    checks.append(_item("mx_records_check", "MX records configured", "site_health",
        "info", ", ".join(dns.get("mx", [])[:2]) or "Not set (informational only)"))

    checks.append(_item("favicon_check", "Favicon present", "site_health",
        "warning" if not advanced.get("has_favicon") else "pass", ""))

    checks.append(_item("dns_health_check", "Overall DNS/email health", "site_health",
        "info", "Informational only, does not affect SEO score"))

    www_redirect = site_health.get("www_redirect", {})
    checks.append(_item("www_redirect_check", "www/non-www consolidated", "site_health",
        "warning" if www_redirect.get("consolidated") is False else "pass", ""))

    http2 = site_health.get("http2", {})
    http2_ok = (not http2.get("available")) or http2.get("http_version") in ("HTTP/2", "HTTP/3")
    checks.append(_item("http2_check", "HTTP/2 or HTTP/3 support", "site_health",
        "pass" if http2_ok else "warning", http2.get("http_version", "Unknown")))

    groups = {"crawlability": [], "on_page": [], "site_health": []}
    for c in checks:
        groups[c["group"]].append(c)

    total = len(checks)
    passed = sum(1 for c in checks if c["status"] == "pass")
    warned = sum(1 for c in checks if c["status"] == "warning")
    failed = sum(1 for c in checks if c["status"] == "fail")
    info = sum(1 for c in checks if c["status"] == "info")

    return {
        "groups": groups,
        "checks": checks,
        # `info` items (email-DNS) are excluded from pass/warning/fail by design,
        # so total == pass + warning + fail + info.
        "summary": {"total": total, "pass": passed, "warning": warned, "fail": failed, "info": info},
    }
