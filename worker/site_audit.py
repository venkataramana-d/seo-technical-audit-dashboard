"""Post-crawl aggregation pass (02-AUDIT-ENGINE.md §2/§5) — checks that only
make sense once a whole crawl exists to compare across pages: duplicate
titles/descriptions/H1s/content, orphan pages, sitewide redirect chains/
loops, broken internal links, and hreflang reciprocity. Runs once, called
from `crawl_service.finalize_crawl()` right before a crawl is marked
completed.

Each finding becomes an `Issue` row. Aggregate findings that describe a
*group* of pages (duplicates, orphans, sitemap diff) get `page_id=None`
(confirmed nullable in Phase 0 for exactly this); findings that are
genuinely about one specific page (a long redirect chain, a broken link's
source page, a missing hreflang reciprocal) get that page's `page_id` set,
so they also surface in that page's own issue list.

`category` values below are deliberately chosen to match substrings already
in `modules.scoring.THEMES` (e.g. "Redirects", "Internal Links") so these new
sitewide issues sort into the right thematic tab via the existing
`get_thematic_issues()` — no new categorization logic to maintain.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select, update

from modules.crawler import discover_sitemap_urls
from modules.scoring import get_thematic_issues
from worker.db.models import Crawl, Issue, Link, Page, Project
from worker.db.session import SessionLocal

def _group_duplicates(db, crawl_id: int, column) -> dict:
    """Groups Page rows by a column's value, keeping only groups with >1
    member. Done in Python, not SQL GROUP_CONCAT, since crawl sizes here
    (thousands, not millions of rows) make this simple and portable."""
    rows = db.execute(
        select(Page.id, Page.url, column).where(Page.crawl_id == crawl_id, column.isnot(None), column != "")
    ).all()
    groups: dict = {}
    for page_id, url, value in rows:
        groups.setdefault(value, []).append({"page_id": page_id, "url": url})
    return {value: entries for value, entries in groups.items() if len(entries) > 1}


def _detect_redirect_issues(db, crawl_id: int) -> list[dict]:
    """Per scope decision in the Phase 2 plan: detects long chains (>2 hops)
    and self-loops from each page's own already-captured redirect hop list —
    not a full graph spanning separately-crawled pages.

    `chain` (Page.redirect_chain_json, built from fetch_page()'s
    redirect_history) always starts with the page's OWN originally-requested
    URL as chain[0] -- that's simply hop zero, not evidence of a loop. A real
    loop is a URL the chain revisits, i.e. a duplicate entry anywhere in the
    list (len(set(chain)) < len(chain)); checking `url in chain` on top of
    that was always true for any redirected page and mislabeled ordinary
    single-hop redirects (e.g. http -> https) as "Redirect loop" errors."""
    rows = db.execute(select(Page.id, Page.url, Page.redirect_chain_json).where(Page.crawl_id == crawl_id)).all()
    findings = []
    for page_id, url, chain in rows:
        if not chain:
            continue
        if len(set(chain)) < len(chain):
            findings.append({"type": "loop", "page_id": page_id, "url": url, "chain": chain})
        elif len(chain) > 2:
            findings.append({"type": "long_chain", "page_id": page_id, "url": url, "chain": chain})
    return findings


def _detect_broken_internal_links(db, crawl_id: int) -> list[dict]:
    """Per scope decision in the Phase 2 plan: a link is "broken" only if its
    target was ALSO crawled in this same crawl and got a 4xx/5xx status —
    links outside the crawl's scope/page-cap aren't separately validated
    (that needs a dedicated HTTP request per link)."""
    status_by_url = dict(db.execute(select(Page.url, Page.status_code).where(Page.crawl_id == crawl_id)).all())

    links = (
        db.execute(select(Link.id, Link.page_id, Link.target_url).join(Page, Link.page_id == Page.id).where(Page.crawl_id == crawl_id))
        .all()
    )
    findings = []
    for link_id, page_id, target_url in links:
        status = status_by_url.get(target_url)
        if status is not None and status >= 400:
            findings.append({"link_id": link_id, "page_id": page_id, "target_url": target_url, "status_code": status})

    # Back-fill the Link rows themselves too, not just an Issue — status_code
    # and is_broken are real Link columns from Phase 0 that Phase 1 never set.
    for finding in findings:
        db.execute(
            update(Link)
            .where(Link.id == finding["link_id"])
            .values(status_code=finding["status_code"], is_broken=True)
        )
    return findings


def _detect_hreflang_issues(db, crawl_id: int) -> list[dict]:
    """Reciprocity check: if page A's hreflang references page B, B should
    reference back to A. Only checkable when B was also crawled — an
    hreflang target outside the crawl can't be verified either way."""
    rows = db.execute(
        select(Page.id, Page.url, Page.hreflang_json).where(Page.crawl_id == crawl_id, Page.hreflang_json.isnot(None))
    ).all()
    # Keep every row, including pages with an empty hreflang list — an empty
    # list still means "this page was crawled and checked," which is a
    # different, findable case from "target wasn't crawled at all" (a plain
    # `if tags` filter here would wrongly conflate the two: [] is falsy).
    tags_by_url = {url: tags for _, url, tags in rows}

    findings = []
    for page_id, url, tags in rows:
        if not tags:
            continue
        for tag in tags:
            target_url = tag.get("url")
            if not target_url or target_url == url:
                continue
            target_tags = tags_by_url.get(target_url)
            if target_tags is None:
                continue  # target wasn't crawled; can't verify
            if not any(t.get("url") == url for t in target_tags):
                findings.append({"page_id": page_id, "url": url, "target_url": target_url})
    return findings


def run_site_audit(crawl_id: int) -> None:
    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        if crawl is None:
            return
        project = db.get(Project, crawl.project_id)

        for column, label, category in (
            (Page.title, "title", "Metadata"),
            (Page.meta_description, "meta description", "Metadata"),
            (Page.h1, "H1", "Heading Structure"),
            (Page.content_hash, "content", "Content"),
        ):
            for value, entries in _group_duplicates(db, crawl_id, column).items():
                db.add(
                    Issue(
                        crawl_id=crawl_id,
                        page_id=None,
                        issue_type=f"Duplicate {label}",
                        severity="warning",
                        impact_score=6,
                        effort_level="Medium",
                        explanation_json={
                            "category": category,
                            "recommendation": f"{len(entries)} pages share the same {label}. Make each unique.",
                            "value": str(value)[:200],
                            "urls": [e["url"] for e in entries],
                        },
                    )
                )

        if project and project.root_url:
            try:
                sitemap_urls = set(discover_sitemap_urls(project.root_url))
            except Exception:
                sitemap_urls = set()
            if sitemap_urls:
                crawled_urls = set(db.execute(select(Page.url).where(Page.crawl_id == crawl_id)).scalars().all())
                orphans = sitemap_urls - crawled_urls
                extra = crawled_urls - sitemap_urls
                if orphans:
                    db.add(
                        Issue(
                            crawl_id=crawl_id,
                            page_id=None,
                            issue_type="Orphan pages (in sitemap, not linked internally)",
                            severity="warning",
                            impact_score=6,
                            effort_level="Medium",
                            explanation_json={
                                "category": "Accessibility",
                                "recommendation": (
                                    f"{len(orphans)} sitemap URLs were never discovered via internal links "
                                    "during the crawl. Add internal links to them or remove them from the sitemap."
                                ),
                                "urls": sorted(orphans)[:200],
                            },
                        )
                    )
                if extra:
                    db.add(
                        Issue(
                            crawl_id=crawl_id,
                            page_id=None,
                            issue_type="Crawled pages missing from sitemap",
                            severity="notice",
                            impact_score=3,
                            effort_level="Low",
                            explanation_json={
                                "category": "Accessibility",
                                "recommendation": f"{len(extra)} crawled pages aren't listed in the sitemap. Consider adding them.",
                                "urls": sorted(extra)[:200],
                            },
                        )
                    )

        for finding in _detect_redirect_issues(db, crawl_id):
            is_loop = finding["type"] == "loop"
            db.add(
                Issue(
                    crawl_id=crawl_id,
                    page_id=finding["page_id"],
                    issue_type="Redirect loop" if is_loop else "Long redirect chain",
                    severity="error" if is_loop else "warning",
                    impact_score=8 if is_loop else 5,
                    effort_level="Medium",
                    explanation_json={
                        "category": "Redirects",
                        "recommendation": "Fix the redirect loop." if is_loop else "Shorten the redirect chain to a single hop.",
                        "chain": finding["chain"],
                    },
                )
            )

        for finding in _detect_broken_internal_links(db, crawl_id):
            db.add(
                Issue(
                    crawl_id=crawl_id,
                    page_id=finding["page_id"],
                    issue_type="Broken internal link",
                    severity="error",
                    impact_score=8,
                    effort_level="Low",
                    explanation_json={
                        "category": "Internal Links",
                        "recommendation": f"Update or remove the link to {finding['target_url']} (returns {finding['status_code']}).",
                        "target_url": finding["target_url"],
                        "status_code": finding["status_code"],
                    },
                )
            )

        for finding in _detect_hreflang_issues(db, crawl_id):
            db.add(
                Issue(
                    crawl_id=crawl_id,
                    page_id=finding["page_id"],
                    issue_type="Non-reciprocal hreflang",
                    severity="notice",
                    impact_score=3,
                    effort_level="Medium",
                    explanation_json={
                        "category": "International SEO",
                        "recommendation": f"{finding['target_url']} does not link back to {finding['url']} via hreflang.",
                        "target_url": finding["target_url"],
                    },
                )
            )

        db.commit()


def get_thematic_report(crawl_id: int) -> dict:
    """Groups every Issue row for a crawl (page-level + the sitewide ones
    from run_site_audit) into the existing THEMES categories, reusing
    modules.scoring.get_thematic_issues() directly, then adds a per-theme
    severity-count summary on top for the thematic-tab UI's chip counts."""
    with SessionLocal() as db:
        issues = db.execute(select(Issue).where(Issue.crawl_id == crawl_id)).scalars().all()
        issue_dicts = [
            {
                "issue": i.issue_type,
                "category": (i.explanation_json or {}).get("category", ""),
                "severity": i.severity,
                "recommendation": (i.explanation_json or {}).get("recommendation", ""),
                "impact_score": i.impact_score or 0,
                "effort": i.effort_level or "",
            }
            for i in issues
        ]

    grouped = get_thematic_issues(issue_dicts)
    return {
        theme: {
            "count": len(theme_issues),
            "by_severity": dict(Counter(iss["severity"] for iss in theme_issues)),
            "issues": theme_issues,
        }
        for theme, theme_issues in grouped.items()
    }
