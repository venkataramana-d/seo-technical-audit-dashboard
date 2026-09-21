"""Phase 3 diff engine (02-AUDIT-ENGINE.md §6) - comparing crawl N vs. N-1 of
the same project. Computed on demand from existing Page/Issue rows, not
persisted (same "recompute, don't materialize" choice as
worker/site_audit.py's get_thematic_report).

Matching is by URL, not row ID: each crawl creates entirely fresh Page rows
(Phase 1 design), so page_id isn't stable across crawls of the same project.
Page-level issues are matched by (Page.url, Issue.issue_type); sitewide
issues (page_id IS NULL) are matched by issue_type alone - coarser, since an
aggregate finding's exact member list isn't tracked as an identity.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from worker.db.models import Crawl, Issue, Page
from worker.db.session import SessionLocal


def get_previous_completed_crawl(crawl_id: int) -> Crawl | None:
    """The most recent completed crawl of the same project, before this one
    - the natural "N-1" to diff a given crawl against."""
    with SessionLocal() as db:
        crawl = db.get(Crawl, crawl_id)
        if crawl is None:
            return None
        reference_time = crawl.finished_at or datetime.now(timezone.utc)
        return (
            db.execute(
                select(Crawl)
                .where(
                    Crawl.project_id == crawl.project_id,
                    Crawl.status == "completed",
                    Crawl.id != crawl_id,
                    Crawl.finished_at.isnot(None),
                    Crawl.finished_at < reference_time,
                )
                .order_by(Crawl.finished_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )


def _page_issue_keys(db, crawl_id: int) -> set[tuple[str, str]]:
    rows = db.execute(
        select(Page.url, Issue.issue_type)
        .join(Issue, Issue.page_id == Page.id)
        .where(Issue.crawl_id == crawl_id, Issue.page_id.isnot(None))
    ).all()
    return set(rows)


def _sitewide_issue_types(db, crawl_id: int) -> set[str]:
    rows = db.execute(select(Issue.issue_type).where(Issue.crawl_id == crawl_id, Issue.page_id.is_(None))).all()
    return {r[0] for r in rows}


def compare_crawls(old_crawl_id: int, new_crawl_id: int) -> dict:
    with SessionLocal() as db:
        old_crawl = db.get(Crawl, old_crawl_id)
        new_crawl = db.get(Crawl, new_crawl_id)
        if old_crawl is None or new_crawl is None:
            raise ValueError("both crawl_ids must refer to existing crawls")

        old_page_keys = _page_issue_keys(db, old_crawl_id)
        new_page_keys = _page_issue_keys(db, new_crawl_id)
        old_sitewide = _sitewide_issue_types(db, old_crawl_id)
        new_sitewide = _sitewide_issue_types(db, new_crawl_id)

        new_issues = [{"url": u, "issue_type": t} for u, t in sorted(new_page_keys - old_page_keys)] + [
            {"url": None, "issue_type": t} for t in sorted(new_sitewide - old_sitewide)
        ]
        fixed_issues = [{"url": u, "issue_type": t} for u, t in sorted(old_page_keys - new_page_keys)] + [
            {"url": None, "issue_type": t} for t in sorted(old_sitewide - new_sitewide)
        ]

        old_scores = dict(
            db.execute(select(Page.url, Page.seo_score).where(Page.crawl_id == old_crawl_id, Page.seo_score.isnot(None))).all()
        )
        new_scores = dict(
            db.execute(select(Page.url, Page.seo_score).where(Page.crawl_id == new_crawl_id, Page.seo_score.isnot(None))).all()
        )

        regressed, improved = [], []
        for url, new_score in new_scores.items():
            old_score = old_scores.get(url)
            if old_score is None:
                continue  # page is new to this crawl; no prior score to compare
            delta = round(new_score - old_score, 2)
            if delta == 0:
                continue
            entry = {"url": url, "old_score": old_score, "new_score": new_score, "delta": delta}
            (regressed if delta < 0 else improved).append(entry)
        regressed.sort(key=lambda e: e["delta"])  # most-regressed (most negative) first
        improved.sort(key=lambda e: -e["delta"])  # most-improved first

        def _delta(new_val, old_val):
            return round(new_val - old_val, 2) if new_val is not None and old_val is not None else None

        return {
            "old_crawl_id": old_crawl_id,
            "new_crawl_id": new_crawl_id,
            "new_issues": new_issues,
            "fixed_issues": fixed_issues,
            "regressed_pages": regressed,
            "improved_pages": improved,
            "health_score_delta": _delta(new_crawl.health_score, old_crawl.health_score),
            "seo_score_avg_delta": _delta(new_crawl.seo_score_avg, old_crawl.seo_score_avg),
        }


def get_score_trend(project_id: int) -> list[dict]:
    """health_score/seo_score_avg for every completed crawl of a project, in
    chronological order - the trend-line data source."""
    with SessionLocal() as db:
        rows = db.execute(
            select(Crawl.id, Crawl.health_score, Crawl.seo_score_avg, Crawl.finished_at)
            .where(Crawl.project_id == project_id, Crawl.status == "completed")
            .order_by(Crawl.finished_at.asc())
        ).all()
        return [
            {
                "crawl_id": crawl_id,
                "health_score": health_score,
                "seo_score_avg": seo_score_avg,
                "finished_at": finished_at.isoformat() if finished_at else None,
            }
            for crawl_id, health_score, seo_score_avg, finished_at in rows
        ]
