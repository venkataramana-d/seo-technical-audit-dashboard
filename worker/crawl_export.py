"""M5 T5.2 shared spine for exporting a *site crawl* (persisted Page/Issue/Link
rows in the DB) as a report, and for the public shared-report link.

`modules/report_generator.py` already turns a list of per-page audit-result
dicts into CSV/Excel/PDF, but it was only ever fed the client-side
`AuditResult[]` shape (api/export.py). This module reconstructs that same
per-page shape from what a crawl persisted to the DB, so the exact same
generators produce a crawl report - no second reporting code path.

Fields the crawl pipeline persists per page (url, status, scores, title/meta/
h1, canonical, indexability, its issues and links) are filled in; fields it
never stored per page (word count, image alt counts, checklist) are left blank
rather than faked - `report_generator.flatten()` tolerates missing keys.

Also owns the share-token helpers (mint/lookup) so both the authenticated
export endpoint and the public share endpoint share one implementation.
"""

from __future__ import annotations

import secrets

from sqlalchemy import select

from worker.db.models import Crawl, CrawlConfig, Issue, Link, Page, Project


# --------------------------------------------------------------------------- #
# share tokens
# --------------------------------------------------------------------------- #
def new_share_token() -> str:
    """A long, URL-safe, unguessable token - the token IS the capability for
    the public read path, so it must not be enumerable. 32 bytes -> 43 chars."""
    return secrets.token_urlsafe(32)


def get_crawl_by_share_token(db, token: str) -> Crawl | None:
    """Resolve a crawl by its share token, bypassing org scope (the public
    shared-report path). Returns None for an unknown/blank token."""
    token = (token or "").strip()
    if not token:
        return None
    return db.execute(select(Crawl).where(Crawl.share_token == token)).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# crawl -> report shape
# --------------------------------------------------------------------------- #
def crawl_meta(db, crawl_id: int) -> dict:
    """Header/summary metadata for a crawl (root URL, scores, counts) - used
    for filenames, report headers and the public share summary card."""
    crawl = db.get(Crawl, crawl_id)
    if crawl is None:
        return {}
    project = db.get(Project, crawl.project_id) if crawl.project_id else None
    crawl_config = db.get(CrawlConfig, crawl.crawl_config_id) if crawl.crawl_config_id else None
    return {
        "id": crawl.id,
        "rootUrl": project.root_url if project else None,
        "status": crawl.status,
        "healthScore": crawl.health_score,
        "seoScoreAvg": crawl.seo_score_avg,
        "pagesCrawled": crawl.pages_crawled,
        "startedAt": crawl.started_at.isoformat() if crawl.started_at else None,
        "finishedAt": crawl.finished_at.isoformat() if crawl.finished_at else None,
        "scheduleCron": crawl_config.schedule_cron if crawl_config else None,
    }


def _issues_by_page(db, crawl_id: int) -> dict[int, list[dict]]:
    """All issues for the crawl, grouped by page_id, in one query (no N+1).
    Uses the original 5-tier severity preserved in explanation_json so the
    report's Critical/High/Medium/Low columns keep their granularity, matching
    the client-side export."""
    rows = db.execute(select(Issue).where(Issue.crawl_id == crawl_id)).scalars().all()
    out: dict[int, list[dict]] = {}
    for i in rows:
        expl = i.explanation_json or {}
        out.setdefault(i.page_id, []).append({
            "issue": i.issue_type,
            "severity": expl.get("original_severity") or i.severity,
            "category": expl.get("category", "Other"),
            "recommendation": expl.get("recommendation", ""),
        })
    return out


def _links_by_page(db, crawl_id: int) -> dict[int, dict[str, list[dict]]]:
    """Internal + external links for the crawl, grouped by page_id then type,
    in one query. Shaped like the per-page audit's internal_links/external_links
    `links` arrays so report_generator's link sheet works unchanged."""
    rows = db.execute(
        select(Link, Page.id)
        .join(Page, Link.page_id == Page.id)
        .where(Page.crawl_id == crawl_id)
    ).all()
    out: dict[int, dict[str, list[dict]]] = {}
    for link, page_id in rows:
        bucket = out.setdefault(page_id, {"internal": [], "external": []})
        key = link.link_type if link.link_type in ("internal", "external") else "external"
        bucket[key].append({
            "url": link.target_url,
            "anchor_text": link.anchor_text,
            "is_dofollow": link.is_dofollow,
            "is_nofollow": link.is_nofollow,
            "status_code": link.status_code,
            "is_broken": link.is_broken,
        })
    return out


def build_crawl_results(db, crawl_id: int) -> list[dict]:
    """Reconstruct the per-page `AuditResult`-shaped dicts that
    modules/report_generator.py (flatten/generate_*) consumes, from the crawl's
    persisted Page/Issue/Link rows. One dict per crawled page."""
    pages = db.execute(
        select(Page).where(Page.crawl_id == crawl_id).order_by(Page.id.asc())
    ).scalars().all()
    if not pages:
        return []

    issues_by_page = _issues_by_page(db, crawl_id)
    links_by_page = _links_by_page(db, crawl_id)

    results: list[dict] = []
    for p in pages:
        page_issues = issues_by_page.get(p.id, [])
        page_links = links_by_page.get(p.id, {"internal": [], "external": []})
        internal = page_links.get("internal", [])
        external = page_links.get("external", [])
        results.append({
            "url": p.url,
            "audit_type": "crawl",
            "status_code": p.status_code,
            "seo_score": p.seo_score if p.seo_score is not None else 0,
            "all_issues": page_issues,
            "metadata": {
                "title": p.title or "",
                "title_length": len(p.title) if p.title else "",
                "description": p.meta_description or "",
                "description_length": len(p.meta_description) if p.meta_description else "",
            },
            "headings": {
                # Only the first H1's text is persisted per page, not full counts.
                "h1_count": 1 if p.h1 else 0,
            },
            "canonical": {"canonical_url": p.canonical_url or ""},
            "indexability": {"is_indexable": p.is_indexable},
            "internal_links": {
                "total_links": len(internal),
                "broken_count": sum(1 for link in internal if link.get("is_broken")),
                "links": internal,
            },
            "external_links": {
                "total_links": len(external),
                "broken_count": sum(1 for link in external if link.get("is_broken")),
                "links": external,
            },
        })
    return results
