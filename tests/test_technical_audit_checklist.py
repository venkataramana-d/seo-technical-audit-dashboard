"""Unit tests for modules/technical_audit_checklist.py: the 35-check Technical
SEO Audit use-case view ported from the standalone tool's phase1.py checklist."""

from modules.technical_audit_checklist import build_technical_audit_checklist


def _base_result(**overrides):
    result = {
        "status_code": 200,
        "response_time": 0.1,
        "metadata": {
            "has_title": True, "title_length": 45,
            "has_description": True, "description_length": 155,
            "has_og_tags": True,
        },
        "canonical": {"canonical_count": 1, "is_self_referencing": True, "canonical_url": "https://example.com/"},
        "indexability": {"is_indexable": True, "robots_meta": ""},
        "url_structure": {"length": 30, "issues": []},
        "content": {"word_count": 800},
        "heading_detail": {"issues": []},
        "image_detail": {"missing_alt_count": 0},
        "advanced": {
            "has_hreflang": False, "hreflang_tags": [], "has_schema": True,
            "schema_errors": [], "schema_types": ["Article"],
            "has_viewport": True, "viewport_content": "width=device-width, initial-scale=1",
            "lang_attr": "en", "has_favicon": True, "twitter_complete": True,
            "http_headers_data": {
                "has_hsts": True, "has_x_frame_options": True, "has_x_content_type_options": True,
            },
            "technical_seo": {"has_mixed_content": False, "mixed_content_count": 0},
        },
        "redirect_analysis": {"chain_length": 1},
        "internal_links": {"broken_count": 0, "total_links": 10},
        "site_health": {
            "robots": {"exists": True, "allowed": True, "googlebot_allowed": True},
            "sitemap": {"exists": True, "url_count": 20, "issues": []},
            "canonical_loop": {"issues": []},
            "readability": {"available": True, "fk_grade": 8.0, "issues": []},
            "content_freshness": {"available": True, "issues": []},
            "ssl": {"valid": True},
            "domain_age": {"available": True, "age_years": 5.0},
            "https_enforcement": {"enforced": True},
            "dns": {"spf": "v=spf1 ...", "dmarc": "v=DMARC1; p=reject", "mx": ["mx1.example.com"]},
            "www_redirect": {"consolidated": True},
            "http2": {"available": True, "http_version": "HTTP/2"},
        },
    }
    result.update(overrides)
    return result


def _find(checklist, check_id):
    return next(c for c in checklist["checks"] if c["id"] == check_id)


def test_all_pass_scenario_yields_35_checks_and_no_failures():
    checklist = build_technical_audit_checklist(_base_result())
    assert checklist["summary"]["total"] == 35
    assert checklist["summary"]["fail"] == 0
    assert checklist["summary"]["pass"] >= 30
    assert set(checklist["groups"].keys()) == {"crawlability", "on_page", "site_health"}


def test_missing_title_and_description_fail():
    result = _base_result(metadata={
        "has_title": False, "title_length": 0,
        "has_description": False, "description_length": 0,
        "has_og_tags": False,
    })
    checklist = build_technical_audit_checklist(result)
    assert _find(checklist, "title_check")["status"] == "fail"
    assert _find(checklist, "meta_description_check")["status"] == "fail"


def test_robots_blocked_is_fail():
    result = _base_result()
    result["site_health"]["robots"] = {"exists": True, "allowed": False, "googlebot_allowed": False}
    checklist = build_technical_audit_checklist(result)
    assert _find(checklist, "robots_check")["status"] == "fail"


def test_bad_http_status_is_fail():
    checklist = build_technical_audit_checklist(_base_result(status_code=500))
    assert _find(checklist, "http_status_check")["status"] == "fail"


def test_redirect_status_thresholds():
    assert _find(build_technical_audit_checklist(
        _base_result(redirect_analysis={"chain_length": 1})), "redirect_check")["status"] == "pass"
    assert _find(build_technical_audit_checklist(
        _base_result(redirect_analysis={"chain_length": 2})), "redirect_check")["status"] == "warning"
    assert _find(build_technical_audit_checklist(
        _base_result(redirect_analysis={"chain_length": 5})), "redirect_check")["status"] == "fail"


def test_broken_internal_links_fail():
    result = _base_result()
    result["internal_links"] = {"broken_count": 3, "total_links": 10}
    checklist = build_technical_audit_checklist(result)
    assert _find(checklist, "broken_link_check")["status"] == "fail"


def test_ttfb_thresholds():
    assert _find(build_technical_audit_checklist(_base_result(response_time=0.1)), "ttfb_check")["status"] == "pass"
    assert _find(build_technical_audit_checklist(_base_result(response_time=0.3)), "ttfb_check")["status"] == "warning"
    assert _find(build_technical_audit_checklist(_base_result(response_time=1.0)), "ttfb_check")["status"] == "fail"


def test_https_not_enforced_is_fail():
    result = _base_result()
    result["site_health"]["https_enforcement"] = {"enforced": False}
    checklist = build_technical_audit_checklist(result)
    assert _find(checklist, "https_enforcement_check")["status"] == "fail"


def test_mixed_content_is_fail():
    result = _base_result()
    result["advanced"]["technical_seo"] = {"has_mixed_content": True, "mixed_content_count": 2}
    checklist = build_technical_audit_checklist(result)
    assert _find(checklist, "mixed_content_check")["status"] == "fail"


def test_no_security_headers_is_fail_partial_is_warning():
    result = _base_result()
    result["advanced"]["http_headers_data"] = {
        "has_hsts": False, "has_x_frame_options": False, "has_x_content_type_options": False,
    }
    assert _find(build_technical_audit_checklist(result), "security_headers_check")["status"] == "fail"

    result["advanced"]["http_headers_data"] = {
        "has_hsts": True, "has_x_frame_options": False, "has_x_content_type_options": True,
    }
    assert _find(build_technical_audit_checklist(result), "security_headers_check")["status"] == "warning"


def test_email_dns_checks_are_informational_not_scored():
    # SPF/DMARC/MX are email-deliverability records, not SEO ranking signals, so
    # they are reported as "info" (never pass/warning/fail) regardless of value
    # and are excluded from the pass/warning/fail summary.
    result = _base_result()
    result["site_health"]["dns"] = {"spf": None, "dmarc": None, "mx": []}
    checklist = build_technical_audit_checklist(result)
    for cid in ("spf_check", "dmarc_check", "mx_records_check", "dns_health_check"):
        assert _find(checklist, cid)["status"] == "info"

    # Even when the records ARE present, they stay informational (not "pass").
    result["site_health"]["dns"] = {"spf": "v=spf1 ...", "dmarc": "v=DMARC1; p=reject", "mx": ["mx1.example.com"]}
    checklist = build_technical_audit_checklist(result)
    for cid in ("spf_check", "dmarc_check", "mx_records_check", "dns_health_check"):
        assert _find(checklist, cid)["status"] == "info"

    # Summary reconciles: total == pass + warning + fail + info.
    s = checklist["summary"]
    assert s["total"] == s["pass"] + s["warning"] + s["fail"] + s["info"]
    assert s["info"] >= 4


def test_missing_hreflang_is_pass_not_penalised():
    checklist = build_technical_audit_checklist(_base_result())
    assert _find(checklist, "hreflang_check")["status"] == "pass"


def test_hreflang_present_without_xdefault_is_warning():
    result = _base_result()
    result["advanced"]["hreflang_tags"] = [{"lang": "en", "url": "https://example.com/en"}]
    checklist = build_technical_audit_checklist(result)
    assert _find(checklist, "hreflang_check")["status"] == "warning"


def test_thin_content_warns_word_count_check():
    # Thin content (<300 words) is a soft signal: at most a warning, not a fail.
    # Word count is not a Google ranking factor, and short pages can fully answer
    # a query, so we no longer fail the check (Phase 1 severity recalibration).
    result = _base_result(content={"word_count": 120})
    checklist = build_technical_audit_checklist(result)
    assert _find(checklist, "word_count_check")["status"] == "warning"
