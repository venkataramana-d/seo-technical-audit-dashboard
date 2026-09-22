"""Regression tests for modules/link_auditor.py false-positive fixes:
a live-but-access-limited link (403 WAF / 429 rate-limit / 503 / timeout) must
NOT be classified as broken, only genuinely dead resources (404/410/5xx) are."""

from bs4 import BeautifulSoup

from modules.link_auditor import (
    WEAK_ANCHORS,
    audit_links,
    categorize_domain,
    link_health,
)


def _all_link_issues(html, base_url="https://example.com/blog/post"):
    soup = BeautifulSoup(html, "lxml")
    res = audit_links(soup, base_url, validate=False)
    return res["internal"]["issues"] + res["external"]["issues"]


def test_correct_blog_links_produce_no_warning_or_worse():
    """Phase 1 headline guard: a correctly-built blog page (new-tab links with
    rel='noopener noreferrer', dofollow internal + external editorial links)
    must NOT raise any Warning/Medium/High/Critical link issue. Only advisory
    Notices are acceptable. This is the exact false-positive the user hit."""
    links = "".join(
        f'<a href="https://ref-site-{i}.com/article" target="_blank" '
        f'rel="noopener noreferrer">Descriptive external source {i}</a>'
        for i in range(60)  # >50: used to trip "Very High Dofollow" (now removed)
    )
    internal = "".join(
        f'<a href="/blog/related-{i}">Descriptive internal link {i}</a>'
        for i in range(5)
    )
    html = f"<html><body><article>{internal}{links}</article></body></html>"

    bad = [
        i for i in _all_link_issues(html)
        if i.get("severity") in ("Warning", "Medium", "High", "Critical")
    ]
    assert bad == [], f"correct blog links should not warn, got: {[b['issue'] for b in bad]}"


def test_new_tab_with_rel_is_not_flagged():
    """A new-tab link that correctly carries rel='noopener' must not appear in
    any noopener finding (the finding is only for links MISSING it, and is a
    Notice regardless)."""
    html = (
        '<html><body>'
        '<a href="https://x.com/" target="_blank" rel="noopener noreferrer">ok</a>'
        '</body></html>'
    )
    issues = _all_link_issues(html)
    noopener = [i for i in issues if "noopener" in i.get("issue", "").lower()]
    assert noopener == []


def test_opens_new_tab_covers_blank_named_and_whitespace_targets():
    """A new tab/window is opened by target="_blank" OR any named target (e.g.
    target="podcasts"); only "", _self, _parent, _top stay in the current tab.
    Regression: named targets were previously reported as same-tab."""
    from modules.link_auditor import parse_link_tag

    def _opens(html):
        a = BeautifulSoup(html, "lxml").find("a")
        return parse_link_tag(a, "https://example.com/")["opens_new_tab"]

    assert _opens('<a href="/a" target="_blank">x</a>') is True
    assert _opens('<a href="/a" target="_BLANK">x</a>') is True
    assert _opens('<a href="/a" target=" _blank ">x</a>') is True   # whitespace tolerated
    assert _opens('<a href="/a" target="podcasts">x</a>') is True   # named target = new context
    assert _opens('<a href="/a" target="_self">x</a>') is False
    assert _opens('<a href="/a" target="_parent">x</a>') is False
    assert _opens('<a href="/a" target="_top">x</a>') is False
    assert _opens('<a href="/a">x</a>') is False


def test_ugc_link_is_not_counted_as_dofollow():
    """Phase 2 bug fix: rel='ugc' is a ranking-suppressing qualifier, so a ugc
    link must NOT be classified as dofollow (it previously inflated the dofollow
    count and could be miscategorised)."""
    from modules.link_auditor import parse_link_tag

    soup = BeautifulSoup(
        '<a href="https://forum.example.com/post" rel="ugc">a comment link</a>',
        "lxml",
    )
    data = parse_link_tag(soup.find("a"), "https://example.com/")
    assert data["is_ugc"] is True
    assert data["is_dofollow"] is False


def test_nofollow_and_sponsored_still_excluded_from_dofollow():
    from modules.link_auditor import parse_link_tag

    for rel in ("nofollow", "sponsored", "ugc"):
        soup = BeautifulSoup(f'<a href="https://x.com/" rel="{rel}">x</a>', "lxml")
        data = parse_link_tag(soup.find("a"), "https://example.com/")
        assert data["is_dofollow"] is False, rel
    # A plain link with no qualifier IS dofollow.
    soup = BeautifulSoup('<a href="https://x.com/">x</a>', "lxml")
    assert parse_link_tag(soup.find("a"), "https://example.com/")["is_dofollow"] is True


def test_affiliate_follow_link_flagged_but_plain_outbound_is_not():
    """The new sponsored-link check must flag a clear affiliate destination that
    is follow, but NOT ordinary outbound links (incl. utm-tagged ones)."""
    html = (
        '<html><body>'
        '<a href="https://www.amzn.to/xyz">buy</a>'                      # affiliate, follow
        '<a href="https://news.example.org/story?utm_source=nl">news</a>'  # plain outbound + utm
        '</body></html>'
    )
    issues = _all_link_issues(html)
    sponsored = [i for i in issues if "sponsored" in i.get("issue", "").lower()]
    assert len(sponsored) == 1
    values = [a["value"] for a in sponsored[0]["affected"]]
    assert any("amzn.to" in v for v in values)
    assert not any("news.example.org" in v for v in values)


def test_link_health_broken_only_for_dead_codes():
    assert link_health(404) == "broken"
    assert link_health(410) == "broken"
    assert link_health(500) == "broken"
    assert link_health(502) == "broken"
    assert link_health(504) == "broken"


def test_link_health_blocked_not_broken():
    # WAF / auth / rate-limit / transient-unavailable: alive, just refused/throttled.
    for code in (401, 403, 408, 429, 451, 503, 999):
        assert link_health(code) == "blocked", code


def test_link_health_403_blocked_regardless_of_domain():
    # Previously only 403 on a hard-coded social-domain allowlist was "blocked";
    # a Cloudflare/WAF 403 on any other site was wrongly "broken".
    assert link_health(403, "some-random-cloudflare-site.com") == "blocked"


def test_link_health_ok_and_redirect():
    assert link_health(200) == "ok"
    assert link_health(301) == "redirect"
    assert link_health(308) == "redirect"


def test_link_health_unverifiable_is_unknown_not_broken():
    # code 0 / None come from timeout / connection / SSL failures.
    assert link_health(0) == "unknown"
    assert link_health(None) == "unknown"


def test_weak_anchors_exclude_contextual_terms():
    # "source" (citation), "download" (file link), "example" (demo) are
    # descriptive in context and were removed from the weak-anchor set.
    assert "source" not in WEAK_ANCHORS
    assert "download" not in WEAK_ANCHORS
    assert "example" not in WEAK_ANCHORS
    # Genuinely generic ones stay.
    assert "click here" in WEAK_ANCHORS
    assert "read more" in WEAK_ANCHORS


def test_categorize_domain_does_not_corrupt_leading_w_domains():
    # lstrip("www.") used to strip leading w/./ chars, turning worldbank.org into
    # orldbank.org. Ensure a "www"-lookalike domain is not mangled.
    assert categorize_domain("worldbank.org") == categorize_domain("worldbank.org")
    # www. prefix is still stripped correctly for a known domain category.
    assert categorize_domain("www.github.com") == categorize_domain("github.com")
