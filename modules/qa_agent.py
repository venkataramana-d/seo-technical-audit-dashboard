"""QA AI Agent - 09-AI-AGENT-SUBSYSTEMS.md §2.

Catches false positives and template-wide noise in the tool's OWN rule-based
findings, before they reach the customer. Two distinct jobs:

  1. Statistical template rollup (DETERMINISTIC, no LLM): if an issue type fires
     on >= threshold of crawled pages, it's almost certainly one template-wide
     condition, not N independent problems - collapse it into a single flag with
     an affected-page count instead of showing thousands of identical rows.
  2. LLM false-positive validation (SAMPLED, not exhaustive): for issue types
     with historically higher false-positive rates, sample a few flagged pages
     and ask the model whether the finding is actually real.

STRICTLY ADDITIVE: this never deletes or hides a rule-based finding - it only
annotates. The deterministic auditor remains the source of truth.

The pure functions here take plain records; `run_qa_pass` (DB-facing) loads
Issue rows, runs both jobs, and writes IssueQaFlag rows. The LLM half no-ops
when no llm client is supplied (e.g. the org has no vaulted key).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

DEFAULT_TEMPLATE_THRESHOLD = 0.95  # >=95% of pages => template-wide condition
DEFAULT_FP_RATE_SAMPLING_FLOOR = 0.2  # only sample issue types this noisy or worse
DEFAULT_SAMPLE_SIZE = 5


@dataclass
class QaIssue:
    """One per-page rule-based finding, as seen by the QA pass."""
    issue_id: str
    issue_type: str
    page_id: str
    page_url: str = ""
    severity: str = "warning"


@dataclass
class TemplateRollup:
    issue_type: str
    affected_count: int
    total_pages: int
    ratio: float
    sample_issue_id: str  # a representative issue to attach the flag to

    def evidence(self) -> dict:
        return {
            "issue_type": self.issue_type,
            "affected_count": self.affected_count,
            "total_pages": self.total_pages,
            "ratio": round(self.ratio, 3),
            "note": "Fires on nearly every page - likely one template-wide condition, not N problems.",
        }


def template_rollups(
    issues: list[QaIssue],
    total_pages: int,
    threshold: float = DEFAULT_TEMPLATE_THRESHOLD,
) -> list[TemplateRollup]:
    """Group by issue_type; flag types that fire on >= threshold of pages."""
    if total_pages <= 0:
        return []
    pages_by_type: dict[str, set[str]] = defaultdict(set)
    first_issue_by_type: dict[str, str] = {}
    for issue in issues:
        pages_by_type[issue.issue_type].add(issue.page_id)
        first_issue_by_type.setdefault(issue.issue_type, issue.issue_id)

    rollups: list[TemplateRollup] = []
    for issue_type, pages in pages_by_type.items():
        ratio = len(pages) / total_pages
        if ratio >= threshold and len(pages) >= 2:
            rollups.append(TemplateRollup(
                issue_type=issue_type,
                affected_count=len(pages),
                total_pages=total_pages,
                ratio=ratio,
                sample_issue_id=first_issue_by_type[issue_type],
            ))
    return rollups


def select_validation_sample(
    issues: list[QaIssue],
    fp_rates: dict[str, float],
    sampling_floor: float = DEFAULT_FP_RATE_SAMPLING_FLOOR,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> list[QaIssue]:
    """Pick issues to LLM-validate: only issue types whose historical false-
    positive rate is at or above the floor, capped at `sample_size` per type.
    Deterministic (sorts by issue_id) so runs are reproducible - no RNG."""
    by_type: dict[str, list[QaIssue]] = defaultdict(list)
    for issue in issues:
        if fp_rates.get(issue.issue_type, 0.0) >= sampling_floor:
            by_type[issue.issue_type].append(issue)
    sample: list[QaIssue] = []
    for issue_type, group in by_type.items():
        group.sort(key=lambda i: i.issue_id)
        sample.extend(group[:sample_size])
    return sample


@dataclass
class FalsePositiveFlag:
    issue_id: str
    reason: str
    model: str

    def evidence(self) -> dict:
        return {"reason": self.reason, "model": self.model,
                "note": "LLM review suggests this rule-based finding may be a false positive; verify."}


_QA_SYSTEM = (
    "You are a meticulous SEO QA reviewer. You are given ONE automated SEO finding and the "
    "page it was raised on. Decide whether the finding is a genuine issue or a false positive "
    "(e.g. the flagged tag is inside a commented-out block, or 'duplicate content' pages are "
    "legitimate locale variants like /en/ vs /en-gb/). You are advisory only - a human reviews "
    "your call. Respond with ONLY a JSON object: "
    '{"is_false_positive": true|false, "reason": "<one sentence>"}.'
)


def validate_issue(llm, issue: QaIssue, page_context: str) -> FalsePositiveFlag | None:
    """Ask the LLM whether one finding is a false positive. Returns a flag only
    when the model says so; returns None on a genuine finding or an unparseable
    answer (fail-open - never invent a false-positive flag on bad output)."""
    from modules.llm import parse_json_object

    user = (
        f"Finding: issue_type={issue.issue_type}, severity={issue.severity}\n"
        f"Page: {issue.page_url}\n"
        f"Page context:\n{page_context[:4000]}"
    )
    resp = llm.complete(_QA_SYSTEM, user, max_tokens=300)
    obj = parse_json_object(resp.text)
    if not obj or not obj.get("is_false_positive"):
        return None
    return FalsePositiveFlag(
        issue_id=issue.issue_id,
        reason=str(obj.get("reason", "")).strip()[:500],
        model=resp.model,
    )
