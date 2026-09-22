"""Phase 3 tests: redirect loops/chains and cross-signal indexing conflicts
(noindex + robots.txt disallow, noindex + cross-URL canonical)."""

from modules.auditor import analyze_redirect_chain, analyze_indexability_conflicts


def _titles(issues):
    return [i["issue"] for i in issues]


def _sev(issues, title_substr):
    for i in issues:
        if title_substr.lower() in i["issue"].lower():
            return i["severity"]
    return None


# ── Redirect chain / loop ────────────────────────────────────────────────────

def test_single_hop_is_not_flagged():
    # A clean single http->https (or non-www->www) 301 is expected, not a problem.
    res = analyze_redirect_chain(["http://example.com/"])
    assert res["issues"] == []
    assert res["has_loop"] is False


def test_multi_hop_chain_is_a_warning():
    res = analyze_redirect_chain([
        "https://example.com/a", "https://example.com/b", "https://example.com/c",
    ])
    assert _sev(res["issues"], "Redirect Chain") == "Warning"
    assert res["has_loop"] is False


def test_http_to_https_upgrade_chain_is_not_a_loop():
    # The standard http->https upgrade repeats the host+path across schemes; it
    # is a normal chain, NOT a loop (scheme is part of the loop key).
    res = analyze_redirect_chain([
        "http://example.com/a", "https://example.com/a", "https://example.com/final",
    ])
    assert res["has_loop"] is False
    assert _sev(res["issues"], "Redirect Chain") == "Warning"


def test_redirect_loop_is_critical():
    # /a -> /b -> /a (same address, same scheme, returns): a loop.
    res = analyze_redirect_chain([
        "https://example.com/a", "https://example.com/b", "https://example.com/a",
    ])
    assert res["has_loop"] is True
    assert _sev(res["issues"], "Redirect Loop") == "Critical"
    # A loop must not ALSO emit the plain chain warning.
    assert not any("Redirect Chain" in t for t in _titles(res["issues"]))


def test_loop_ignores_trailing_slash():
    res = analyze_redirect_chain([
        "https://example.com/a", "https://example.com/a/",
    ])
    assert res["has_loop"] is True


# ── Indexing conflicts ───────────────────────────────────────────────────────

def _result(*, indexable, canonical_url=None, is_self_ref=False, disallowed=False):
    site_health_issues = []
    if disallowed:
        site_health_issues.append({"issue": "Page Blocked by robots.txt", "severity": "Critical"})
    return {
        "url": "https://example.com/p",
        "final_url": "https://example.com/p",
        "indexability": {"is_indexable": indexable},
        "canonical": {"canonical_url": canonical_url, "is_self_ref": is_self_ref},
        "site_health": {"issues": site_health_issues},
    }


def test_indexable_page_has_no_conflicts():
    assert analyze_indexability_conflicts(_result(indexable=True, disallowed=True)) == []


def test_noindex_plus_disallow_is_critical():
    issues = analyze_indexability_conflicts(_result(indexable=False, disallowed=True))
    assert _sev(issues, "robots.txt Disallow") == "Critical"


def test_noindex_plus_cross_canonical_is_warning():
    issues = analyze_indexability_conflicts(
        _result(indexable=False, canonical_url="https://example.com/other", is_self_ref=False)
    )
    assert _sev(issues, "Cross-URL Canonical") == "Warning"


def test_noindex_with_self_canonical_no_canonical_conflict():
    issues = analyze_indexability_conflicts(
        _result(indexable=False, canonical_url="https://example.com/p", is_self_ref=True)
    )
    assert not any("Canonical" in i["issue"] for i in issues)
