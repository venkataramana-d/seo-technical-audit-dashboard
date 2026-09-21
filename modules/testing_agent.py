"""Testing AI Agent - 09-AI-AGENT-SUBSYSTEMS.md §3.

Tests the audit ENGINE's own accuracy (not a customer's site): runs the per-page
audit against a library of golden HTML fixtures with hand-verified expected
issues and computes per-issue-type precision/recall. Feeds §2's sampling rate
(historical_fp_rate = 1 - precision).

Deterministic - no LLM. Fixture *generation* from a production-flagged case
produces an ANONYMIZED, unapproved proposal (a review item), never auto-merged
into the gating suite (guardrail §3).

Pure module: `run_fixture_suite` works on in-memory Fixture records; the DB glue
(load approved fixtures, persist runs, update fp rates) is in testing_runner.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Fixture:
    html: str
    expected_issue_types: set[str]
    source: str = "manual"


@dataclass
class PrecisionRecall:
    issue_type: str
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 1.0  # no predictions => vacuously precise

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 1.0

    @property
    def false_positive_rate(self) -> float:
        return round(1.0 - self.precision, 3)


def emitted_issue_types(html: str) -> set[str]:
    """Per-page issue types the audit engine emits for one HTML document."""
    from modules.pipeline import audit

    result = audit(html, {}, "http://fixture.local/", 200)
    return {i.issue_type for i in result.issues}


def run_fixture_suite(fixtures: list[Fixture]) -> dict[str, PrecisionRecall]:
    """Confusion-matrix accumulation per issue_type across the fixture library."""
    stats: dict[str, PrecisionRecall] = {}

    def _pr(t: str) -> PrecisionRecall:
        return stats.setdefault(t, PrecisionRecall(issue_type=t))

    for fx in fixtures:
        predicted = emitted_issue_types(fx.html)
        expected = set(fx.expected_issue_types)
        for t in predicted & expected:
            _pr(t).tp += 1
        for t in predicted - expected:
            _pr(t).fp += 1
        for t in expected - predicted:
            _pr(t).fn += 1
    return stats


# ---- Fixture generation from a flagged case (anonymized proposal) ----

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_HREF_SRC_RE = re.compile(r'\b(href|src)\s*=\s*(["\'])(.*?)\2', re.IGNORECASE)
_TEXT_RE = re.compile(r">([^<>]+)<")


def anonymize_html(html: str) -> str:
    """Strip customer-identifying content while preserving the structural triggers
    the audit checks key on (tags, attributes, lengths). Guardrail §3 - required,
    since fixtures may be shared across a team/repo.

    - hrefs/srcs -> example.com equivalents (keeps link structure, drops real URLs)
    - emails -> user@example.com
    - visible text nodes -> repeated placeholder words of the SAME word count
      (so word-count-based checks like thin_content still reproduce)
    """
    def _attr(m: re.Match) -> str:
        attr, quote, value = m.group(1), m.group(2), m.group(3)
        if value.startswith("#") or value.startswith("mailto:") or value.startswith("tel:"):
            repl = value  # anchors/mailto/tel structure matters to link classification
        elif value.startswith("/") or value.startswith("./") or value.startswith("../"):
            repl = "/path"
        else:
            repl = "https://example.com/"
        return f'{attr}={quote}{repl}{quote}'

    def _text(m: re.Match) -> str:
        original = m.group(1)
        if not original.strip():
            return m.group(0)
        n = len(original.split())
        return ">" + " ".join(["word"] * n) + "<"

    out = _HREF_SRC_RE.sub(_attr, html)
    out = _EMAIL_RE.sub("user@example.com", out)
    out = _TEXT_RE.sub(_text, out)
    return out


def propose_fixture_from_flag(html: str, expected_issue_types: set[str]) -> Fixture:
    """Build an anonymized, production-flagged fixture PROPOSAL (unapproved).
    The caller persists it with approved=False for human review."""
    return Fixture(
        html=anonymize_html(html),
        expected_issue_types=set(expected_issue_types),
        source="production_flagged",
    )
