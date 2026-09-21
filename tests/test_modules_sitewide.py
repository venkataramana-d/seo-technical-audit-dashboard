"""Unit tests for the Phase 2 site-wide audit checks (02-AUDIT-ENGINE.md §2).

Pure-function tests over in-memory records - no database required.
"""
from modules.sitewide import (
    SiteLink,
    SitePage,
    broken_internal_links,
    crawl_depth_stats,
    duplicate_content,
    duplicate_descriptions,
    duplicate_h1s,
    duplicate_titles,
    hreflang_reciprocity,
    orphan_pages,
    redirect_chains_and_loops,
    run_sitewide_audit,
    sitemap_vs_crawl_diff,
)


def _page(url, **kw):
    kw.setdefault("status_code", 200)
    return SitePage(normalized_url=url, url=url, **kw)


# ---- duplicate metadata ----

def test_duplicate_titles_groups_only_repeats():
    pages = [
        _page("http://s/a", title="Same Title"),
        _page("http://s/b", title="Same Title"),
        _page("http://s/c", title="Unique Title"),
    ]
    issues = duplicate_titles(pages)
    assert len(issues) == 1
    assert issues[0].issue_type == "duplicate_title"
    assert issues[0].affected_urls == ["http://s/a", "http://s/b"]
    assert issues[0].severity == "warning"


def test_duplicate_titles_ignores_whitespace_and_blanks():
    pages = [
        _page("http://s/a", title="  Title  "),
        _page("http://s/b", title="Title"),
        _page("http://s/c", title="   "),  # blank after strip -> ignored
        _page("http://s/d", title=None),
    ]
    issues = duplicate_titles(pages)
    assert len(issues) == 1
    assert set(issues[0].affected_urls) == {"http://s/a", "http://s/b"}


def test_duplicate_titles_ignores_non_200_pages():
    pages = [
        _page("http://s/a", title="T", status_code=200),
        _page("http://s/b", title="T", status_code=301),  # redirect, not a real dup
    ]
    assert duplicate_titles(pages) == []


def test_duplicate_descriptions_and_h1_and_content():
    pages = [
        _page("http://s/a", meta_description="D", h1="H", content_hash="abc"),
        _page("http://s/b", meta_description="D", h1="H", content_hash="abc"),
    ]
    assert len(duplicate_descriptions(pages)) == 1
    assert len(duplicate_h1s(pages)) == 1
    assert duplicate_h1s(pages)[0].category == "Headings"
    dc = duplicate_content(pages)
    assert len(dc) == 1 and dc[0].issue_type == "duplicate_content"


# ---- redirect chains & loops ----

def test_long_redirect_chain_flagged_over_two_hops():
    page = _page("http://s/a", status_code=200, redirect_chain=[
        {"url": "http://s/a", "status_code": 301},
        {"url": "http://s/b", "status_code": 301},
        {"url": "http://s/c", "status_code": 301},
    ])
    issues = redirect_chains_and_loops([page])
    assert len(issues) == 1
    assert issues[0].issue_type == "long_redirect_chain"
    assert issues[0].severity == "warning"


def test_two_hop_chain_not_flagged():
    page = _page("http://s/a", redirect_chain=[
        {"url": "http://s/a", "status_code": 301},
        {"url": "http://s/b", "status_code": 301},
    ])
    assert redirect_chains_and_loops([page]) == []


def test_redirect_loop_detected():
    # Realistic loop shape: a -> b -> a -> ... ; the fetcher records each URL
    # that issued a redirect, so the same source URL reappears in the hop list.
    page = SitePage(
        normalized_url="http://s/a", url="http://s/a", status_code=None,
        redirect_chain=[
            {"url": "http://s/a", "status_code": 301},
            {"url": "http://s/b", "status_code": 301},
            {"url": "http://s/a", "status_code": 301},  # a issues a redirect again -> loop
        ],
    )
    issues = redirect_chains_and_loops([page])
    assert len(issues) == 1
    assert issues[0].issue_type == "redirect_loop"
    assert issues[0].severity == "error"


# ---- orphan pages ----

def test_orphan_page_in_sitemap_but_not_linked():
    pages = [
        _page("http://s/", in_sitemap=True),
        _page("http://s/orphan", in_sitemap=True),
        _page("http://s/linked", in_sitemap=True),
    ]
    links = [SiteLink(source_url="http://s/", target_url="http://s/linked", link_type="internal")]
    issues = orphan_pages(pages, links)
    assert len(issues) == 1
    # "/" and "/orphan" are in the sitemap but not link targets
    assert set(issues[0].affected_urls) == {"http://s/", "http://s/orphan"}


def test_no_orphans_when_all_linked():
    pages = [_page("http://s/x", in_sitemap=True)]
    links = [SiteLink(source_url="http://s/", target_url="http://s/x", link_type="internal")]
    assert orphan_pages(pages, links) == []


# ---- sitemap diff ----

def test_sitemap_diff_flags_stale_and_missing():
    pages = [
        _page("http://s/live", status_code=200),
        _page("http://s/gone", status_code=404),
    ]
    sitemap = {"http://s/live", "http://s/gone", "http://s/never-crawled"}
    issues = sitemap_vs_crawl_diff(pages, sitemap)
    types = {i.issue_type: i for i in issues}
    assert "sitemap_stale_entry" in types
    # gone (404) and never-crawled are stale; live is fine
    assert set(types["sitemap_stale_entry"].affected_urls) == {"http://s/gone", "http://s/never-crawled"}


def test_sitemap_diff_flags_missing_from_sitemap():
    pages = [_page("http://s/live", status_code=200)]
    issues = sitemap_vs_crawl_diff(pages, {"http://s/other"})
    assert any(i.issue_type == "sitemap_missing_page" and "http://s/live" in i.affected_urls
               for i in issues)


# ---- broken internal links ----

def test_broken_internal_links_grouped_by_target():
    links = [
        SiteLink("http://s/a", "http://s/dead", "internal", status_code=404),
        SiteLink("http://s/b", "http://s/dead", "internal", is_broken=True),
        SiteLink("http://s/a", "http://s/ok", "internal", status_code=200),
        SiteLink("http://s/a", "http://ext/dead", "external", status_code=404),  # external excluded
    ]
    issues = broken_internal_links(links)
    assert len(issues) == 1
    assert issues[0].issue_type == "broken_internal_link"
    assert issues[0].affected_urls[0] == "http://s/dead"
    assert set(issues[0].affected_urls[1:]) == {"http://s/a", "http://s/b"}


# ---- hreflang reciprocity ----

def test_hreflang_missing_return_tag_flagged():
    pages = [
        SitePage("http://s/en", url="http://s/en", hreflang=[("es", "http://s/es")]),
        SitePage("http://s/es", url="http://s/es", hreflang=[]),  # no return tag
    ]
    issues = hreflang_reciprocity(pages)
    assert len(issues) == 1
    assert issues[0].issue_type == "hreflang_no_return_tag"
    assert issues[0].affected_urls == ["http://s/en", "http://s/es"]


def test_hreflang_reciprocal_ok():
    pages = [
        SitePage("http://s/en", url="http://s/en", hreflang=[("es", "http://s/es")]),
        SitePage("http://s/es", url="http://s/es", hreflang=[("en", "http://s/en")]),
    ]
    assert hreflang_reciprocity(pages) == []


# ---- depth stats ----

def test_crawl_depth_stats():
    pages = [
        _page("http://s/", depth=0),
        _page("http://s/a", depth=1),
        _page("http://s/b", depth=1),
        _page("http://s/c", depth=2),
        _page("http://s/nodepth", depth=None),
    ]
    stats = crawl_depth_stats(pages)
    assert stats["max_depth"] == 2
    assert stats["pages_per_depth"] == {0: 1, 1: 2, 2: 1}
    assert stats["avg_depth"] == 1.0
    assert stats["pages_with_known_depth"] == 4


# ---- orchestrator ----

def test_run_sitewide_audit_combines_checks():
    pages = [
        _page("http://s/", title="Home", in_sitemap=True),
        _page("http://s/a", title="Dup", meta_description="D", in_sitemap=True),
        _page("http://s/b", title="Dup", meta_description="D", in_sitemap=True),
    ]
    links = [SiteLink("http://s/", "http://s/a", "internal")]  # /b is orphan
    issues = run_sitewide_audit(pages, links, sitemap_urls={"http://s/", "http://s/a", "http://s/b"})
    kinds = {i.issue_type for i in issues}
    assert "duplicate_title" in kinds
    assert "duplicate_meta_description" in kinds
    assert "orphan_page" in kinds
    # every issue serializes with affected-url metadata
    for i in issues:
        exp = i.to_explanation_json()
        assert exp["affected_count"] == len(i.affected_urls)
