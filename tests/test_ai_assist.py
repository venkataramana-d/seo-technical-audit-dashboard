"""Tests for modules/ai_assist.py: issue aggregation for the summary, the
JSON-mode summary parsing, and the personalized fix-suggestion drafting.
Network (_chat / requests.post) is mocked; nothing here hits the real Groq API."""

import json
from unittest.mock import MagicMock, patch

from modules.ai_assist import (
    _aggregate_issues,
    _chat,
    _parse_summary_reply,
    answer_crawl_question,
    detect_fix_target,
    explain_audit,
    suggest_fix,
)


# ── answer_crawl_question (M4 T4.2 ask-your-crawl) ───────────────────────────

def test_ask_requires_api_key():
    r = answer_crawl_question("what is wrong?", [], 50, "")
    assert r["ok"] is False and "key" in r["error"].lower()


def test_ask_requires_non_empty_question():
    r = answer_crawl_question("   ", [], 50, "fake-key")
    assert r["ok"] is False


@patch("modules.ai_assist._chat")
def test_ask_grounds_on_issue_digest_and_returns_answer(mock_chat):
    mock_chat.return_value = json.dumps({"answer": "Fix the missing titles first."})
    issues = [{"issue": "Missing Meta Title", "severity": "High", "category": "Metadata",
               "recommendation": "Add a title", "impact_score": 8}]
    r = answer_crawl_question("what first?", issues, 62, "fake-key",
                              context_label="across 10 pages")
    assert r["ok"] is True
    assert r["answer"] == "Fix the missing titles first."
    # JSON mode requested, and the real issue is in the grounding prompt.
    assert mock_chat.call_args.kwargs.get("json_mode") is True
    user_msg = mock_chat.call_args[0][0][1]["content"]
    assert "Missing Meta Title" in user_msg and "what first?" in user_msg


@patch("modules.ai_assist._chat")
def test_ask_falls_back_to_raw_text_when_not_json(mock_chat):
    mock_chat.return_value = "Plain text answer."
    r = answer_crawl_question("q", [], 50, "fake-key")
    assert r["ok"] is True and r["answer"] == "Plain text answer."


def _mk(issue, severity="Medium", category="Metadata"):
    return {"issue": issue, "severity": severity, "category": category,
            "recommendation": "fix", "impact_score": 5}


def test_aggregate_dedupes_by_title_with_page_counts():
    issues = [_mk("Missing meta description") for _ in range(180)]
    issues += [_mk("H1 too long", "Warning") for _ in range(120)]
    issues += [_mk("Blocked by robots.txt", "Critical", "Site Health") for _ in range(3)]
    agg, totals = _aggregate_issues(issues)
    assert len(agg) == 3  # 303 raw rows collapse to 3 distinct issues
    counts = {e["issue"]: e["count"] for e in agg}
    assert counts["Missing meta description"] == 180
    assert counts["Blocked by robots.txt"] == 3
    assert totals["Medium"] == 180 and totals["Critical"] == 3


def test_aggregate_sorts_severe_first_even_when_rare():
    # The rare Critical (3 pages) must sort ABOVE the frequent Medium (180 pages)
    # so it survives the character-budget truncation instead of dropping off.
    issues = [_mk("Missing meta description") for _ in range(180)]
    issues += [_mk("Blocked by robots.txt", "Critical", "Site Health") for _ in range(3)]
    agg, _ = _aggregate_issues(issues)
    assert agg[0]["issue"] == "Blocked by robots.txt"
    assert agg[0]["severity"] == "Critical"


# ── _parse_summary_reply / explain_audit JSON mode ──────────────────────────

def test_parse_summary_reply_handles_valid_json():
    reply = json.dumps({"explanation": "Site is healthy.", "top_actions": ["Fix X", "Fix Y"]})
    explanation, actions = _parse_summary_reply(reply)
    assert explanation == "Site is healthy."
    assert actions == ["Fix X", "Fix Y"]


def test_parse_summary_reply_caps_actions_at_five():
    reply = json.dumps({"explanation": "ok", "top_actions": [f"Fix {i}" for i in range(10)]})
    _, actions = _parse_summary_reply(reply)
    assert len(actions) == 5


def test_parse_summary_reply_falls_back_on_malformed_json():
    reply = "1. Fix the meta description because it's missing.\n2. Add an H1 heading.\nOverall the site needs work."
    explanation, actions = _parse_summary_reply(reply)
    assert len(actions) == 2
    assert actions[0] == "Fix the meta description because it's missing."
    assert "Overall the site needs work." in explanation


def test_parse_summary_reply_falls_back_on_empty_json_object():
    # Valid JSON but neither expected key present -> still use the fallback.
    reply = '{"unexpected": "shape"}'
    explanation, actions = _parse_summary_reply(reply)
    assert explanation == '{"unexpected": "shape"}'
    assert actions == []


@patch("modules.ai_assist._chat")
def test_explain_audit_requests_json_mode(mock_chat):
    mock_chat.return_value = json.dumps({"explanation": "Good.", "top_actions": ["Fix meta"]})
    result = explain_audit([], 80, "fake-key", url="https://example.com/")
    assert result["ok"] is True
    assert result["explanation"] == "Good."
    assert result["top_actions"] == ["Fix meta"]
    assert mock_chat.call_args.kwargs.get("json_mode") is True


@patch("modules.ai_assist._chat")
def test_explain_audit_context_label_overrides_url_phrasing(mock_chat):
    mock_chat.return_value = json.dumps({"explanation": "ok", "top_actions": []})
    explain_audit([], 50, "fake-key", url="https://example.com/", context_label="across 10 audited pages (sitewide)")
    user_msg = mock_chat.call_args[0][0][1]["content"]
    assert "across 10 audited pages (sitewide)" in user_msg
    assert "for https://example.com/" not in user_msg


@patch("modules.ai_assist.requests.post")
def test_chat_json_mode_falls_back_to_plain_on_400(mock_post):
    # `body` is mutated in place across retries, so snapshot each call's
    # json kwarg (a dict copy) at call time rather than reading it back from
    # mock_post.call_args_list afterward, which would all see the same
    # already-mutated dict.
    seen_bodies = []
    rejected = MagicMock(status_code=400)
    ok_resp = MagicMock(status_code=200)
    ok_resp.json.return_value = {"choices": [{"message": {"content": "plain text reply"}}]}
    responses = [rejected, ok_resp]

    def _side_effect(*args, **kwargs):
        seen_bodies.append(dict(kwargs["json"]))
        return responses.pop(0)

    mock_post.side_effect = _side_effect

    result = _chat([{"role": "user", "content": "hi"}], "fake-key", json_mode=True)

    assert result == "plain text reply"
    assert mock_post.call_count == 2
    assert "response_format" in seen_bodies[0]
    assert "response_format" not in seen_bodies[1]


# ── detect_fix_target / suggest_fix ─────────────────────────────────────────

def test_detect_fix_target_matches_meta_title():
    assert detect_fix_target("Missing Meta Title") == "title"
    assert detect_fix_target("Meta Title Too Short") == "title"
    assert detect_fix_target("Meta Title Too Long") == "title"


def test_detect_fix_target_matches_meta_description():
    assert detect_fix_target("Missing Meta Description") == "description"
    assert detect_fix_target("Meta Description Too Long") == "description"


def test_detect_fix_target_matches_h1():
    assert detect_fix_target("Missing H1 heading") == "h1"
    assert detect_fix_target("H1 heading is too short (12 chars)") == "h1"


def test_detect_fix_target_matches_og_and_alt():
    assert detect_fix_target("Missing Open Graph Tags: og:title, og:description") == "og"
    assert detect_fix_target("Missing alt text on 3 image(s)") == "alt"
    assert detect_fix_target("Empty alt text on 2 image(s) (verify decorative)") == "alt"


def test_detect_fix_target_returns_none_for_unsupported_issue():
    assert detect_fix_target("Broken Internal Link") is None
    assert detect_fix_target("Missing Cache-Control Header") is None


def test_suggest_fix_without_api_key_returns_error():
    result = suggest_fix("Missing Meta Description", {}, api_key="")
    assert result == {"ok": False, "error": "Groq API key not configured"}


def test_suggest_fix_returns_error_for_unsupported_issue():
    result = suggest_fix("Broken Internal Link", {}, api_key="fake-key")
    assert result["ok"] is False
    assert "No fix generator" in result["error"]


@patch("modules.ai_assist._chat")
def test_suggest_fix_returns_drafted_suggestion(mock_chat):
    mock_chat.return_value = json.dumps({
        "suggestion": "Buy Handmade Ceramic Mugs Online | Acme Pottery",
        "rationale": "Includes the product and brand within 60 characters.",
    })
    result = suggest_fix(
        "Missing Meta Title",
        {"url": "https://example.com/mugs", "content_snippet": "Handmade ceramic mugs, fired in our studio."},
        api_key="fake-key",
    )
    assert result["ok"] is True
    assert result["target"] == "title"
    assert "Ceramic Mugs" in result["suggestion"]
    assert mock_chat.call_args.kwargs.get("json_mode") is True


@patch("modules.ai_assist._chat")
def test_suggest_fix_falls_back_to_raw_text_on_malformed_json(mock_chat):
    mock_chat.return_value = "Just a plain suggestion with no JSON wrapper."
    result = suggest_fix("Missing H1 heading", {"url": "https://example.com/"}, api_key="fake-key")
    assert result["ok"] is True
    assert result["suggestion"] == "Just a plain suggestion with no JSON wrapper."
    assert result["rationale"] == ""


@patch("modules.ai_assist._chat")
def test_suggest_fix_returns_error_when_suggestion_empty(mock_chat):
    mock_chat.return_value = json.dumps({"suggestion": "", "rationale": "n/a"})
    result = suggest_fix("Missing Meta Description", {}, api_key="fake-key")
    assert result["ok"] is False


@patch("modules.ai_assist._chat")
def test_suggest_fix_unwraps_double_nested_object(mock_chat):
    # A structured target (description) in JSON mode: some models double-wrap,
    # putting another {"suggestion": …} object inside the suggestion string.
    inner = json.dumps({"suggestion": "A crisp 155-char meta description.", "rationale": "why"})
    mock_chat.return_value = json.dumps({"suggestion": inner, "rationale": ""})
    result = suggest_fix("Missing Meta Description", {"url": "https://example.com/"}, api_key="fake-key")
    assert result["ok"] is True
    assert result["suggestion"] == "A crisp 155-char meta description."
    assert result["rationale"] == "why"


@patch("modules.ai_assist._chat")
def test_suggest_fix_unwraps_nested_array(mock_chat):
    # A structured target whose model reply nested an ARRAY inside `suggestion`.
    arr = json.dumps([{"suggestion": "Line one"}, {"suggestion": "Line two"}])
    mock_chat.return_value = json.dumps({"suggestion": arr, "rationale": ""})
    result = suggest_fix("Missing Meta Description", {"url": "https://example.com/"}, api_key="fake-key")
    assert result["ok"] is True
    assert result["suggestion"] == "Line one\nLine two"


@patch("modules.ai_assist._chat")
def test_suggest_fix_og_uses_plain_text(mock_chat):
    # Open Graph is a multi-line target: it is requested as PLAIN TEXT (not JSON),
    # and a markdown code fence around it is stripped.
    mock_chat.return_value = (
        "```html\n<meta property=\"og:title\" content=\"X\"/>\n"
        "<meta property=\"og:description\" content=\"Y\"/>\n```"
    )
    result = suggest_fix("Missing Open Graph Tags", {"url": "https://example.com/"}, api_key="fake-key")
    assert result["ok"] is True
    assert result["suggestion"].startswith("<meta")
    assert "```" not in result["suggestion"]
    # og/alt must NOT be sent in JSON mode (that caused the empty/nested failures).
    assert mock_chat.call_args.kwargs.get("json_mode") is not True


@patch("modules.ai_assist._chat", side_effect=RuntimeError("Groq API unavailable (HTTP 500)"))
def test_suggest_fix_returns_error_on_api_failure(mock_chat):
    result = suggest_fix("Missing Meta Description", {}, api_key="fake-key")
    assert result["ok"] is False
    assert "500" in result["error"]
