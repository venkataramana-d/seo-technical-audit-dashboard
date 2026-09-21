"""Unit tests for the AI Agent subsystems (Phase 6) - pure, FakeLLM, no DB."""
import json

from modules.content_agent import (
    META_MAX, PageContext, draft_for_issue_type, suggest_meta_description, suggest_title,
)
from modules.llm import FakeLLM, LLMResponse, parse_json_object
from modules.qa_agent import (
    QaIssue, select_validation_sample, template_rollups, validate_issue,
)


# ---- llm helper ----

def test_parse_json_object_variants():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('here you go: {"a": 1} thanks') == {"a": 1}
    assert parse_json_object("```json\n{\"a\": 2}\n```") == {"a": 2}
    assert parse_json_object("not json") is None
    assert parse_json_object("") is None
    assert parse_json_object("[1,2,3]") is None  # not an object


def test_fake_llm_records_calls():
    llm = FakeLLM(lambda s, u: "hi")
    r = llm.complete("sys", "user")
    assert isinstance(r, LLMResponse) and r.text == "hi"
    assert llm.calls == [("sys", "user")]


# ---- QA: template rollup (deterministic) ----

def _qi(iid, itype, page):
    return QaIssue(issue_id=iid, issue_type=itype, page_id=page)


def test_template_rollup_flags_sitewide_condition():
    # missing_viewport on all 10 pages -> template-wide; thin_content on 1 -> not
    issues = [_qi(f"i{i}", "missing_viewport", f"p{i}") for i in range(10)]
    issues.append(_qi("t0", "thin_content", "p0"))
    rollups = template_rollups(issues, total_pages=10)
    assert len(rollups) == 1
    r = rollups[0]
    assert r.issue_type == "missing_viewport" and r.affected_count == 10 and r.ratio == 1.0
    assert r.evidence()["affected_count"] == 10


def test_template_rollup_respects_threshold():
    # 9/10 pages = 0.9, below the 0.95 default
    issues = [_qi(f"i{i}", "x", f"p{i}") for i in range(9)]
    assert template_rollups(issues, total_pages=10) == []
    assert len(template_rollups(issues, total_pages=10, threshold=0.9)) == 1


def test_template_rollup_zero_pages():
    assert template_rollups([_qi("i", "x", "p")], total_pages=0) == []


# ---- QA: sampling ----

def test_select_validation_sample_only_noisy_types_capped():
    issues = [_qi(f"a{i}", "noisy", f"p{i}") for i in range(8)]
    issues += [_qi(f"b{i}", "clean", f"q{i}") for i in range(8)]
    sample = select_validation_sample(
        issues, fp_rates={"noisy": 0.4, "clean": 0.05}, sample_size=5
    )
    assert len(sample) == 5  # only 'noisy', capped at 5
    assert all(s.issue_type == "noisy" for s in sample)


# ---- QA: LLM validation ----

def test_validate_issue_flags_false_positive():
    llm = FakeLLM(lambda s, u: json.dumps({"is_false_positive": True, "reason": "tag is commented out"}))
    flag = validate_issue(llm, _qi("i1", "page_noindexed", "p1"), "context")
    assert flag is not None and flag.issue_id == "i1"
    assert "commented" in flag.reason


def test_validate_issue_none_when_real_or_unparseable():
    real = FakeLLM(lambda s, u: json.dumps({"is_false_positive": False, "reason": "genuine"}))
    assert validate_issue(real, _qi("i1", "x", "p1"), "ctx") is None
    junk = FakeLLM(lambda s, u: "the model rambled without json")
    assert validate_issue(junk, _qi("i1", "x", "p1"), "ctx") is None  # fail-open


# ---- Content agent ----

def test_suggest_title_returns_draft_with_bounds_flag():
    good = FakeLLM(lambda s, u: json.dumps({"title": "A Clear Descriptive Title For The Page Here", "confidence": 0.8}))
    ctx = PageContext(url="http://x/", title=None, h1="Widget")
    draft = suggest_title(good, ctx)
    assert draft is not None and draft.suggestion_type == "title"
    assert draft.confidence == 0.8
    assert draft.within_bounds is True


def test_suggest_title_out_of_bounds_still_returned_but_flagged():
    short = FakeLLM(lambda s, u: json.dumps({"title": "Too short"}))
    draft = suggest_title(short, PageContext(url="http://x/"))
    assert draft is not None and draft.within_bounds is False  # 30-60 not met


def test_suggest_meta_description():
    text = "x" * (META_MAX - 5)
    llm = FakeLLM(lambda s, u: json.dumps({"meta_description": text, "confidence": 1.5}))
    draft = suggest_meta_description(llm, PageContext(url="http://x/"))
    assert draft is not None and draft.suggestion_type == "meta_description"
    assert draft.confidence == 1.0  # clamped to [0,1]
    assert draft.within_bounds is True


def test_draft_for_issue_type_dispatch_and_unsupported():
    llm = FakeLLM(lambda s, u: json.dumps({"title": "A Clear Descriptive Title For The Page Here"}))
    assert draft_for_issue_type(llm, "missing_title", PageContext(url="http://x/")) is not None
    assert draft_for_issue_type(llm, "title_length", PageContext(url="http://x/")) is not None
    assert draft_for_issue_type(llm, "some_unsupported_issue", PageContext(url="http://x/")) is None


def test_content_agent_never_returns_none_text():
    empty = FakeLLM(lambda s, u: json.dumps({"title": "   "}))
    assert suggest_title(empty, PageContext(url="http://x/")) is None
