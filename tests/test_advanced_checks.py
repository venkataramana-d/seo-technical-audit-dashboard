"""Regression tests for modules/advanced_checks.py false-positive fixes:
- mixed-content must not count non-resource <link> rels (canonical/alternate/…),
  only real sub-resources (stylesheet/preload/…).
- an empty <script type="application/ld+json"> must not be flagged "Invalid".
- a charset declared via the HTTP Content-Type header satisfies the charset check.
"""

from bs4 import BeautifulSoup

from modules.advanced_checks import analyze_advanced, analyze_technical_seo


def _ttfb_issues(response_time_s):
    soup = BeautifulSoup("<html><head></head><body><p>hi</p></body></html>", "lxml")
    r = analyze_technical_seo(soup, "https://x.com/", 1000, response_time_s)
    return [i for i in r.get("issues", []) if "server response" in i["issue"].lower()]


def test_ttfb_thresholds_widened_for_reproducibility():
    # BUG#3: a single noisy response_time used to flip a ~500-700ms page between
    # "Poor TTFB" (High, -25 perf) and lower bands, changing the score run-to-run.
    # Bands are widened: ~300ms -> nothing, ~700ms -> Warning (not High),
    # clearly-slow >1200ms -> High.
    assert _ttfb_issues(0.3) == []
    warn = _ttfb_issues(0.7)
    assert warn and warn[0]["severity"] == "Warning"
    high = _ttfb_issues(1.5)
    assert high and high[0]["severity"] == "High"


def _issues(result):
    return [i["issue"] for i in result.get("issues", [])]


def test_http_canonical_on_https_page_is_not_mixed_content():
    html = """
    <html><head>
      <link rel="canonical" href="http://example.com/page">
      <link rel="alternate" hreflang="en" href="http://example.com/en">
    </head><body><p>hi</p></body></html>
    """
    result = analyze_technical_seo(BeautifulSoup(html, "lxml"), "https://example.com/page", 1000, 0.1)
    assert not any("Mixed Content" in i for i in _issues(result))


def test_real_http_stylesheet_on_https_page_is_mixed_content():
    html = '<html><head><link rel="stylesheet" href="http://cdn.example.com/a.css"></head><body></body></html>'
    result = analyze_technical_seo(BeautifulSoup(html, "lxml"), "https://example.com/", 1000, 0.1)
    assert any("Mixed Content" in i for i in _issues(result))


def test_empty_json_ld_script_not_flagged_invalid():
    html = '<html><head><script type="application/ld+json"></script></head><body></body></html>'
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers={})
    assert not any("Invalid JSON-LD" in i for i in _issues(result))


def test_broken_json_ld_still_flagged():
    html = '<html><head><script type="application/ld+json">{ not valid json </script></head><body></body></html>'
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers={})
    assert any("Invalid JSON-LD" in i for i in _issues(result))


def test_charset_from_http_header_satisfies_check():
    html = "<html><head><title>t</title></head><body></body></html>"  # no <meta charset>
    headers = {"Content-Type": "text/html; charset=utf-8"}
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers=headers)
    assert not any("Missing Charset" in i for i in _issues(result))


def test_missing_charset_flagged_when_absent_everywhere():
    html = "<html><head><title>t</title></head><body></body></html>"
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers={})
    assert any("Missing Charset" in i for i in _issues(result))


# ── Phase 3: hreflang validity + structured-data required/recommended ─────────

def _issue_objs(result):
    return result.get("issues", [])


def _find_issue(result, substr):
    for i in result.get("issues", []):
        if substr.lower() in i["issue"].lower():
            return i
    return None


def test_invalid_hreflang_code_flagged():
    html = (
        '<html><head>'
        '<link rel="alternate" hreflang="en-UK" href="https://example.com/uk">'
        '<link rel="alternate" hreflang="en-GB" href="https://example.com/gb">'
        '</head><body></body></html>'
    )
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers={})
    issue = _find_issue(result, "Invalid hreflang Language Code")
    assert issue is not None and issue["severity"] == "Warning"
    assert any("en-UK" in a["value"] for a in issue["affected"])


def test_relative_hreflang_url_flagged():
    html = (
        '<html><head>'
        '<link rel="alternate" hreflang="fr" href="/fr/page">'
        '</head><body></body></html>'
    )
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers={})
    assert _find_issue(result, "hreflang URL(s) Not Absolute") is not None


def test_hreflang_canonical_conflict_flagged():
    html = (
        '<html><head>'
        '<link rel="canonical" href="https://example.com/other-lang">'
        '<link rel="alternate" hreflang="de" href="https://example.com/de">'
        '</head><body></body></html>'
    )
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/de", http_headers={})
    assert _find_issue(result, "hreflang Conflicts With Canonical") is not None


def test_schema_missing_required_property_is_warning():
    # Article without headline (required) -> Warning.
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Article","author":"Jane"}'
        '</script></head><body></body></html>'
    )
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers={})
    issue = _find_issue(result, "Missing Required Property")
    assert issue is not None and issue["severity"] == "Warning"


def test_schema_missing_recommended_property_is_notice():
    # Article WITH headline (required satisfied) but missing image/datePublished
    # (recommended) -> Notice.
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Article","headline":"Hello"}'
        '</script></head><body></body></html>'
    )
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers={})
    issue = _find_issue(result, "Missing Recommended Property")
    assert issue is not None and issue["severity"] == "Notice"


def test_graph_only_schema_not_reported_as_missing():
    # A @graph-only document HAS structured data; must not fire "No Structured Data Found".
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"Organization","name":"Acme"},'
        '{"@type":"WebSite","name":"Acme","url":"https://example.com"}]}'
        '</script></head><body></body></html>'
    )
    result = analyze_advanced(BeautifulSoup(html, "lxml"), "https://example.com/", http_headers={})
    assert _find_issue(result, "No Structured Data Found") is None
