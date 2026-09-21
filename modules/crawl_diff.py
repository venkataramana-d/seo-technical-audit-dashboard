"""Crawl comparison / diff engine - 02-AUDIT-ENGINE.md §6, the foundation for
Phase 3 "Always-on" scheduled auditing (00-PLAN-OVERVIEW.md Phase 3).

Compares two crawls of the same project and reports which issues are new, fixed,
or still-present since last time, plus the Health/SEO score trend. Same logic
whether triggered manually (Compare UI) or by a cron schedule.

Pure module: `diff_issue_records` / `evaluate_alert_rules` work on plain records
and never touch the DB. The DB-facing `compare_crawls` (loads Issue rows, maps
them onto records, calls the pure functions) lives in crawler/diff_runner.py so
this stays importable without a database.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiffIssue:
    """One issue as seen for diffing. `scope` makes 'the same issue' stable across
    crawls: a page URL for per-page issues, or a signature of the affected URLs
    for crawl-level (sitewide) findings."""
    issue_type: str
    severity: str
    scope: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.issue_type, self.scope)


def make_scope(page_url: str | None, affected_urls: list[str] | None) -> str:
    """Stable scope string for an issue. Per-page issues key on the page URL;
    crawl-level issues key on their sorted affected-URL set."""
    if page_url:
        return page_url
    if affected_urls:
        return "|".join(sorted(affected_urls))
    return ""


@dataclass
class CrawlDiff:
    new_issues: list[DiffIssue] = field(default_factory=list)
    fixed_issues: list[DiffIssue] = field(default_factory=list)
    persisting_issues: list[DiffIssue] = field(default_factory=list)
    prev_health_score: float | None = None
    cur_health_score: float | None = None
    prev_seo_score_avg: float | None = None
    cur_seo_score_avg: float | None = None

    @property
    def health_score_delta(self) -> float | None:
        if self.prev_health_score is None or self.cur_health_score is None:
            return None
        return round(self.cur_health_score - self.prev_health_score, 1)

    @property
    def seo_score_delta(self) -> float | None:
        if self.prev_seo_score_avg is None or self.cur_seo_score_avg is None:
            return None
        return round(self.cur_seo_score_avg - self.prev_seo_score_avg, 1)

    def new_count(self, severity: str | None = None) -> int:
        return sum(1 for i in self.new_issues if severity is None or i.severity == severity)

    def fixed_count(self, severity: str | None = None) -> int:
        return sum(1 for i in self.fixed_issues if severity is None or i.severity == severity)

    def summary(self) -> dict:
        return {
            "new": len(self.new_issues),
            "fixed": len(self.fixed_issues),
            "persisting": len(self.persisting_issues),
            "new_errors": self.new_count("error"),
            "fixed_errors": self.fixed_count("error"),
            "health_score_delta": self.health_score_delta,
            "seo_score_delta": self.seo_score_delta,
        }


def diff_issue_records(
    previous: list[DiffIssue],
    current: list[DiffIssue],
    prev_scores: tuple[float | None, float | None] = (None, None),
    cur_scores: tuple[float | None, float | None] = (None, None),
) -> CrawlDiff:
    """Diff two issue sets by (issue_type, scope). Deduplicates within each crawl
    so a repeated finding doesn't distort the counts."""
    prev_by_key = {i.key: i for i in previous}
    cur_by_key = {i.key: i for i in current}

    new = [i for k, i in cur_by_key.items() if k not in prev_by_key]
    fixed = [i for k, i in prev_by_key.items() if k not in cur_by_key]
    persisting = [i for k, i in cur_by_key.items() if k in prev_by_key]

    _sort = lambda xs: sorted(xs, key=lambda i: (i.issue_type, i.scope))
    return CrawlDiff(
        new_issues=_sort(new),
        fixed_issues=_sort(fixed),
        persisting_issues=_sort(persisting),
        prev_health_score=prev_scores[0],
        cur_health_score=cur_scores[0],
        prev_seo_score_avg=prev_scores[1],
        cur_seo_score_avg=cur_scores[1],
    )


# ---- Alert rules (alert_rules.condition_json) ----

@dataclass
class TriggeredAlert:
    rule_id: str | None
    channel: str
    message: str
    condition: dict


def _matches(condition: dict, diff: CrawlDiff) -> str | None:
    """Return an alert message if `condition` fires against `diff`, else None.

    Supported condition shapes (condition_json):
      {"type": "new_issues", "severity": "error", "min_count": 1}
      {"type": "health_score_drop", "min_drop": 5}
      {"type": "seo_score_drop", "min_drop": 5}
      {"type": "regression"}   # shorthand for any new error-severity issue
    """
    ctype = condition.get("type")
    if ctype == "new_issues":
        severity = condition.get("severity")  # None => any severity
        min_count = int(condition.get("min_count", 1))
        n = diff.new_count(severity)
        if n >= min_count:
            label = f"{severity} " if severity else ""
            return f"{n} new {label}issue(s) since the previous crawl (threshold {min_count})."
    elif ctype == "regression":
        n = diff.new_count("error")
        if n >= 1:
            return f"{n} new error-severity issue(s) since the previous crawl."
    elif ctype == "health_score_drop":
        min_drop = float(condition.get("min_drop", 1))
        d = diff.health_score_delta
        if d is not None and d <= -min_drop:
            return f"Health Score dropped {abs(d)} points (threshold {min_drop})."
    elif ctype == "seo_score_drop":
        min_drop = float(condition.get("min_drop", 1))
        d = diff.seo_score_delta
        if d is not None and d <= -min_drop:
            return f"Average SEO Score dropped {abs(d)} points (threshold {min_drop})."
    return None


def evaluate_alert_rules(diff: CrawlDiff, rules: list) -> list[TriggeredAlert]:
    """Evaluate alert rules against a diff. Each rule exposes `condition_json`
    (dict) and `channel`, plus optional `id` - the SQLAlchemy AlertRule model or
    any object/tuple with those attributes."""
    triggered: list[TriggeredAlert] = []
    for rule in rules:
        if isinstance(rule, tuple):
            rule_id, condition, channel = rule
        else:
            rule_id = getattr(rule, "id", None)
            condition = getattr(rule, "condition_json", None) or {}
            channel = getattr(rule, "channel", "email")
        if not isinstance(condition, dict):
            continue
        message = _matches(condition, diff)
        if message:
            triggered.append(TriggeredAlert(
                rule_id=str(rule_id) if rule_id is not None else None,
                channel=channel,
                message=message,
                condition=condition,
            ))
    return triggered
