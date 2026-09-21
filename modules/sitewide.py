"""Site-wide audit checks - 02-AUDIT-ENGINE.md §2.

These are the cross-page checks that only make sense once a whole crawl exists
(duplicate titles across pages, orphan pages, redirect loops spanning multiple
URLs, sitewide hreflang reciprocity, ...). Per the spec, they run as a
post-crawl aggregation pass over the persisted `pages`/`links` rows.

Design: every function here is **pure** and operates on plain dataclass records
(`SitePage` / `SiteLink`), never on SQLAlchemy models or a live Session. The
crawl-finalization glue (added when the DB path is verified) is responsible for
loading rows out of Postgres, mapping them onto these records, calling these
functions, and writing the resulting `SiteIssue`s back as crawl-level `Issue`
rows (page_id NULL, affected URLs in explanation_json). Keeping the analysis
decoupled from persistence is what lets it be unit-tested without a database.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


# ---- Input records (mapped from the pages/links tables by the caller) ----

@dataclass
class SitePage:
    normalized_url: str
    url: str = ""
    status_code: int | None = None
    title: str | None = None
    meta_description: str | None = None
    h1: str | None = None
    content_hash: str | None = None
    # Redirect hops recorded for this URL, in order, as stored in
    # pages.redirect_chain_json: [{"url": ..., "status_code": ...}, ...].
    redirect_chain: list[dict] = field(default_factory=list)
    # (lang, href) pairs declared on this page via hreflang annotations.
    hreflang: list[tuple[str, str]] = field(default_factory=list)
    depth: int | None = None  # clicks from the seed URL (None if unknown)
    in_sitemap: bool = False


@dataclass
class SiteLink:
    source_url: str  # normalized URL of the page the link is on
    target_url: str  # normalized target
    link_type: str = "internal"  # internal|external|... (see models.LinkType)
    status_code: int | None = None
    is_broken: bool = False


# ---- Output ----

@dataclass
class SiteIssue:
    """A crawl-level finding. Mirrors modules.types.AuditIssue but carries the
    set of URLs the finding spans (a single Issue row rather than one per URL)."""
    issue_type: str
    category: str
    severity: str  # error|warning|notice
    impact_score: int  # 1-10
    effort_level: str  # low|medium|high
    what: str
    why: str
    root_cause: str
    fix: str
    affected_urls: list[str] = field(default_factory=list)

    def to_explanation_json(self) -> dict:
        return {
            "what": self.what,
            "why": self.why,
            "root_cause": self.root_cause,
            "fix": self.fix,
            "affected_urls": self.affected_urls,
            "affected_count": len(self.affected_urls),
        }


# 4xx/5xx is treated as broken for link aggregation.
def _is_error_status(code: int | None) -> bool:
    return code is not None and code >= 400


def _indexable_html_pages(pages: list[SitePage]) -> list[SitePage]:
    """Only 200-OK pages participate in duplicate grouping - a 301/404 sharing a
    blank title with another is not a meaningful 'duplicate title' finding."""
    return [p for p in pages if p.status_code == 200]


# ---- Duplicate metadata (exact-string grouping across the crawl) ----

def _group_by(pages: list[SitePage], key) -> dict[str, list[SitePage]]:
    groups: dict[str, list[SitePage]] = defaultdict(list)
    for page in pages:
        raw = key(page)
        if raw is None:
            continue
        norm = raw.strip()
        if not norm:
            continue
        groups[norm].append(page)
    return groups


def _duplicate_field_issues(
    pages: list[SitePage],
    key,
    *,
    field_label: str,
    issue_type: str,
    category: str,
    severity: str,
    impact_score: int,
) -> list[SiteIssue]:
    issues: list[SiteIssue] = []
    for value, group in _group_by(_indexable_html_pages(pages), key).items():
        if len(group) < 2:
            continue
        urls = sorted(p.normalized_url for p in group)
        issues.append(SiteIssue(
            issue_type=issue_type,
            category=category,
            severity=severity,
            impact_score=impact_score,
            effort_level="medium",
            what=f"{len(urls)} pages share the same {field_label}: {value!r}.",
            why=(f"Duplicate {field_label}s make it harder for search engines to tell the pages "
                 "apart, splitting relevance signals and risking the wrong page ranking."),
            root_cause=(f"A shared template or CMS field emits an identical {field_label} across "
                        "multiple URLs instead of a page-specific value."),
            fix=f"Give each page a unique, descriptive {field_label}.",
            affected_urls=urls,
        ))
    return issues


def duplicate_titles(pages: list[SitePage]) -> list[SiteIssue]:
    return _duplicate_field_issues(
        pages, lambda p: p.title,
        field_label="title", issue_type="duplicate_title", category="Metadata",
        severity="warning", impact_score=6,
    )


def duplicate_descriptions(pages: list[SitePage]) -> list[SiteIssue]:
    return _duplicate_field_issues(
        pages, lambda p: p.meta_description,
        field_label="meta description", issue_type="duplicate_meta_description", category="Metadata",
        severity="warning", impact_score=4,
    )


def duplicate_h1s(pages: list[SitePage]) -> list[SiteIssue]:
    return _duplicate_field_issues(
        pages, lambda p: p.h1,
        field_label="H1", issue_type="duplicate_h1", category="Headings",
        severity="notice", impact_score=2,
    )


def duplicate_content(pages: list[SitePage]) -> list[SiteIssue]:
    """Exact-duplicate body content, grouped by the content hash stored per page
    (02-AUDIT-ENGINE.md §2, Screaming Frog's exact-duplicate check)."""
    return _duplicate_field_issues(
        pages, lambda p: p.content_hash,
        field_label="content (identical hash)", issue_type="duplicate_content", category="Content",
        severity="warning", impact_score=7,
    )


# ---- Redirect chains & loops (sitewide) ----

def redirect_chains_and_loops(pages: list[SitePage]) -> list[SiteIssue]:
    """Flag redirect chains longer than 2 hops and redirect loops.
    02-AUDIT-ENGINE.md §2 - extends the single-page redirect check across the crawl."""
    issues: list[SiteIssue] = []
    for page in pages:
        chain = page.redirect_chain or []
        if not chain:
            continue
        # Each hop entry's "url" is the URL that *issued* the redirect (see
        # crawler.fetcher.fetch), so chain[0].url is the originally-requested URL
        # and the final 200 destination lives in final_url, not the chain.
        hop_urls = [hop.get("url") for hop in chain if hop.get("url")]
        start = hop_urls[0] if hop_urls else (page.url or page.normalized_url)
        # A loop = the same URL issues a redirect twice, so following the chain
        # never terminates (the fetcher caps it at max_redirects).
        has_loop = len(hop_urls) != len(set(hop_urls))
        if has_loop:
            issues.append(SiteIssue(
                issue_type="redirect_loop", category="Internal Links", severity="error",
                impact_score=9, effort_level="medium",
                what=f"Redirect loop starting at {start}.",
                why="A redirect loop never reaches a real page, so users and crawlers get an error and the content is unreachable.",
                root_cause="Two or more redirect rules point back at each other (directly or via a chain).",
                fix="Break the cycle so the URL redirects to a single final 200-OK destination.",
                affected_urls=hop_urls,
            ))
            continue
        # hops = number of redirect responses before the final URL.
        if len(chain) > 2:
            issues.append(SiteIssue(
                issue_type="long_redirect_chain", category="Internal Links", severity="warning",
                impact_score=4, effort_level="medium",
                what=f"Redirect chain of {len(chain)} hops from {start}.",
                why="Each extra hop wastes crawl budget and link equity and slows the user; Google may stop following long chains.",
                root_cause="Successive redirect rules (e.g. http->https then non-www->www then trailing-slash) stack up instead of collapsing to one.",
                fix="Collapse the chain so the original URL redirects directly to the final destination in a single hop.",
                affected_urls=hop_urls,
            ))
    return issues


# ---- Orphan pages ----

def orphan_pages(pages: list[SitePage], links: list[SiteLink]) -> list[SiteIssue]:
    """Pages present in the sitemap but never reached by an internal link during
    the crawl (02-AUDIT-ENGINE.md §2, Screaming Frog's orphan-page report)."""
    internally_linked = {
        lk.target_url for lk in links if lk.link_type == "internal"
    }
    orphans = sorted(
        p.normalized_url for p in pages
        if p.in_sitemap and p.normalized_url not in internally_linked
    )
    if not orphans:
        return []
    return [SiteIssue(
        issue_type="orphan_page", category="Indexability", severity="notice",
        impact_score=3, effort_level="medium",
        what=f"{len(orphans)} page(s) are in the sitemap but have no internal links pointing to them.",
        why="Orphan pages get little crawl priority and pass no internal link equity, so they rank poorly despite being in the sitemap.",
        root_cause="The page is listed in the XML sitemap but is not linked from any navigation, body, or footer link.",
        fix="Add internal links from relevant pages, or remove the URL from the sitemap if it should not be indexed.",
        affected_urls=orphans,
    )]


# ---- Sitemap vs. crawl diff ----

def sitemap_vs_crawl_diff(pages: list[SitePage], sitemap_urls: set[str]) -> list[SiteIssue]:
    """Compare declared sitemap URLs against what the crawl actually saw.
    Flags stale sitemap entries (in sitemap, not found/200 in crawl) and pages
    reachable by crawl but missing from the sitemap."""
    issues: list[SiteIssue] = []
    ok_crawled = {p.normalized_url for p in pages if p.status_code == 200}
    all_crawled = {p.normalized_url for p in pages}

    stale = sorted(u for u in sitemap_urls if u not in ok_crawled)
    if stale:
        issues.append(SiteIssue(
            issue_type="sitemap_stale_entry", category="Indexability", severity="warning",
            impact_score=4, effort_level="low",
            what=f"{len(stale)} sitemap URL(s) did not resolve to a crawlable 200-OK page.",
            why="Sitemaps listing dead or redirected URLs waste crawl budget and signal a poorly-maintained site to search engines.",
            root_cause="The sitemap was not regenerated after pages were removed, redirected, or renamed.",
            fix="Regenerate the sitemap so it lists only live, canonical, 200-OK URLs.",
            affected_urls=stale,
        ))

    missing = sorted(u for u in all_crawled if u not in sitemap_urls)
    if missing:
        issues.append(SiteIssue(
            issue_type="sitemap_missing_page", category="Indexability", severity="notice",
            impact_score=2, effort_level="low",
            what=f"{len(missing)} crawlable page(s) are not listed in the sitemap.",
            why="Pages missing from the sitemap may be discovered more slowly by search engines.",
            root_cause="The sitemap generator does not include these routes, or they were added after the last sitemap build.",
            fix="Add the missing canonical URLs to the sitemap if they should be indexed.",
            affected_urls=missing,
        ))
    return issues


# ---- Broken internal link graph ----

def broken_internal_links(links: list[SiteLink]) -> list[SiteIssue]:
    """Aggregate every internal link that resolves to a 4xx/5xx (or is flagged
    broken) into one finding per broken target, with the source pages listed."""
    sources_by_target: dict[str, set[str]] = defaultdict(set)
    for lk in links:
        if lk.link_type != "internal":
            continue
        if lk.is_broken or _is_error_status(lk.status_code):
            sources_by_target[lk.target_url].add(lk.source_url)

    issues: list[SiteIssue] = []
    for target, sources in sorted(sources_by_target.items()):
        src_list = sorted(sources)
        issues.append(SiteIssue(
            issue_type="broken_internal_link", category="Internal Links", severity="error",
            impact_score=8, effort_level="low",
            what=f"Internal link target {target} is broken; linked from {len(src_list)} page(s).",
            why="Broken internal links dead-end users and crawlers and waste the link equity the source pages pass.",
            root_cause="The target URL was moved or deleted without updating the links pointing to it.",
            fix="Update the links to the correct URL, or restore/redirect the target.",
            affected_urls=[target, *src_list],
        ))
    return issues


# ---- Sitewide hreflang reciprocity ----

def hreflang_reciprocity(pages: list[SitePage]) -> list[SiteIssue]:
    """Flag non-reciprocal hreflang annotations: if page A declares an hreflang
    to B, B must declare one back to A (02-AUDIT-ENGINE.md §2). Return-tag
    errors are the most common hreflang mistake and are invisible per-page."""
    declared: dict[str, set[str]] = {}
    for page in pages:
        targets = {href for _lang, href in page.hreflang if href and href != page.normalized_url}
        declared[page.normalized_url] = targets

    issues: list[SiteIssue] = []
    for source, targets in declared.items():
        for target in sorted(targets):
            back = declared.get(target)
            if back is not None and source not in back:
                issues.append(SiteIssue(
                    issue_type="hreflang_no_return_tag", category="Advanced", severity="warning",
                    impact_score=5, effort_level="medium",
                    what=f"{source} declares an hreflang to {target}, but {target} does not link back.",
                    why="hreflang annotations are ignored by Google unless they are reciprocal, so the language/region targeting silently fails.",
                    root_cause="The return hreflang tag on the target page is missing or points to a different URL.",
                    fix=f"Add a reciprocal hreflang annotation on {target} pointing back to {source}.",
                    affected_urls=[source, target],
                ))
    return issues


# ---- Crawl-depth analytics (stats, not issues) ----

def crawl_depth_stats(pages: list[SitePage]) -> dict:
    """Site-structure depth distribution - max depth, pages per depth level, and
    average clicks-from-home (02-AUDIT-ENGINE.md §2). Returns analytics for the
    crawl summary, not issues."""
    depths = [p.depth for p in pages if p.depth is not None]
    pages_per_depth: dict[int, int] = defaultdict(int)
    for d in depths:
        pages_per_depth[d] += 1
    return {
        "max_depth": max(depths) if depths else 0,
        "pages_per_depth": dict(sorted(pages_per_depth.items())),
        "avg_depth": round(sum(depths) / len(depths), 2) if depths else 0.0,
        "pages_with_known_depth": len(depths),
    }


# ---- Orchestrator ----

def run_sitewide_audit(
    pages: list[SitePage],
    links: list[SiteLink],
    sitemap_urls: set[str] | None = None,
    documents: dict[str, str] | None = None,
    severity_overrides: dict[str, str] | None = None,
    root_url: str | None = None,
) -> list[SiteIssue]:
    """Run every site-wide check and return the combined findings. The caller
    maps these onto crawl-level Issue rows (page_id NULL).

    `documents` (normalized_url -> content text) enables near-duplicate content
    detection; omit it to skip that pass (e.g. when page text isn't available).
    `severity_overrides` ({issue_type: severity}) applies the project's severity
    model to the findings (02-AUDIT-ENGINE.md §3).
    `root_url` (normalized homepage) enables the crawl-depth pass (flags pages
    buried too deep from the homepage); omit to skip it.
    """
    sitemap_urls = sitemap_urls or set()
    issues: list[SiteIssue] = []
    issues += duplicate_titles(pages)
    issues += duplicate_descriptions(pages)
    issues += duplicate_h1s(pages)
    issues += duplicate_content(pages)
    issues += redirect_chains_and_loops(pages)
    issues += orphan_pages(pages, links)
    issues += broken_internal_links(links)
    issues += hreflang_reciprocity(pages)
    if sitemap_urls:
        issues += sitemap_vs_crawl_diff(pages, sitemap_urls)
    if documents:
        # imported lazily to keep the two modules independently importable
        from modules.near_duplicate import near_duplicate_content
        issues += near_duplicate_content(documents)
    if root_url:
        from modules.crawl_graph import build_depth_report, excessive_depth_issues
        page_urls = {p.normalized_url for p in pages}
        report = build_depth_report(root_url, page_urls, links)
        issues += excessive_depth_issues(report, links, page_urls)
    if severity_overrides:
        from modules.severity import apply_severity_overrides
        issues = apply_severity_overrides(issues, severity_overrides)
    return issues
