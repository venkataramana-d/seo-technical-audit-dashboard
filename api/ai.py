import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import read_json_body, require_str, send_json  # noqa: E402
from modules.ai_assist import answer_crawl_question, explain_audit, suggest_fix  # noqa: E402
from modules.content_agent import SUPPORTED_ISSUE_TYPES, PageContext, draft_for_issue_type  # noqa: E402
from worker.api_key_service import get_default_org_vaulted_key  # noqa: E402
from worker.auth import AuthError, require_authenticated  # noqa: E402

logger = logging.getLogger(__name__)


def _handle_summary(handler, payload):
    try:
        all_issues = payload.get("allIssues") or []
        seo_score = payload.get("seoScore", 0)
        url = (payload.get("url") or "").strip()
        # Optional: overrides the default "for {url}" phrasing, e.g. the
        # Results page's sitewide summary passes "across N audited pages".
        context_label = (payload.get("contextLabel") or "").strip() or None
        # Precedence: a key pasted for this one request > a saved vault
        # entry (Phase 5) > the server-wide env var.
        api_key = payload.get("apiKey") or get_default_org_vaulted_key("groq") or os.environ.get("GROQ_API_KEY")

        summary = explain_audit(all_issues, seo_score, api_key, url=url, context_label=context_label)
        status = 200 if summary.get("ok") else 400
        send_json(handler, status, summary)
    except Exception:  # noqa: BLE001
        logger.exception("ai.py (summary) request failed")
        send_json(handler, 500, {"ok": False, "error": "Internal error while generating the summary."})


def _handle_fix_suggestion(handler, payload):
    try:
        issue_title = require_str(handler, payload, "issue", field_name="issue")
        if issue_title is None:
            return

        page_context = payload.get("pageContext") or {}
        if not isinstance(page_context, dict):
            page_context = {}
        # Precedence: a key pasted for this one request > a saved vault
        # entry (Phase 5) > the server-wide env var.
        api_key = payload.get("apiKey") or get_default_org_vaulted_key("groq") or os.environ.get("GROQ_API_KEY")

        result = suggest_fix(issue_title, page_context, api_key)
        status = 200 if result.get("ok") else 400
        send_json(handler, status, result)
    except Exception:  # noqa: BLE001
        logger.exception("ai.py (fix-suggestion) request failed")
        send_json(handler, 500, {"ok": False, "error": "Internal error while generating the fix."})


def _handle_ask(handler, payload):
    """Ask-your-crawl (M4 T4.2): answer a natural-language question grounded in
    the supplied audit issue data. Same Groq key precedence as the summary."""
    try:
        question = require_str(handler, payload, "question", field_name="question")
        if question is None:
            return
        all_issues = payload.get("allIssues") or []
        seo_score = payload.get("seoScore", 0)
        url = (payload.get("url") or "").strip()
        context_label = (payload.get("contextLabel") or "").strip() or None
        api_key = payload.get("apiKey") or get_default_org_vaulted_key("groq") or os.environ.get("GROQ_API_KEY")

        result = answer_crawl_question(question, all_issues, seo_score, api_key,
                                       url=url, context_label=context_label)
        status = 200 if result.get("ok") else 400
        send_json(handler, status, result)
    except Exception:  # noqa: BLE001
        logger.exception("ai.py (ask) request failed")
        send_json(handler, 500, {"ok": False, "error": "Internal error while answering the question."})


def _handle_content_draft(handler, payload):
    """Anthropic-backed content draft (rebuild's Content Agent, 09 §1): given an
    issue type + page context, draft an improved title or meta description.
    Draft-only/additive - it never writes to a page. Uses the vaulted
    'anthropic' key (or a per-request apiKey); degrades gracefully with no key.
    """
    try:
        issue_type = require_str(handler, payload, "issueType", field_name="issueType")
        if issue_type is None:
            return
        if issue_type not in SUPPORTED_ISSUE_TYPES:
            send_json(handler, 400, {
                "ok": False,
                "error": f"No content draft available for '{issue_type}' "
                         f"(supported: {sorted(SUPPORTED_ISSUE_TYPES)}).",
            })
            return

        api_key = payload.get("apiKey") or get_default_org_vaulted_key("anthropic")
        if not api_key:
            send_json(handler, 400, {
                "ok": False,
                "error": "No Anthropic API key is configured. Add one under "
                         "Settings → API keys (provider 'anthropic') to enable AI drafts.",
            })
            return

        ctx = PageContext(
            url=(payload.get("url") or "").strip(),
            title=payload.get("title"),
            meta_description=payload.get("metaDescription"),
            h1=payload.get("h1"),
        )

        from modules.llm import AnthropicLLM  # local import: only when a draft is requested
        llm = AnthropicLLM(api_key=api_key)
        draft = draft_for_issue_type(llm, issue_type, ctx)
        if draft is None:
            send_json(handler, 502, {"ok": False, "error": "The model did not return a usable draft."})
            return
        send_json(handler, 200, {
            "ok": True,
            "suggestionType": draft.suggestion_type,
            "draftText": draft.draft_text,
            "confidence": draft.confidence,
            "model": draft.model,
            "withinBounds": draft.within_bounds,
        })
    except Exception:  # noqa: BLE001
        logger.exception("ai.py (content-draft) request failed")
        send_json(handler, 500, {"ok": False, "error": "Internal error while drafting content."})


# Consolidates what used to be separate api/*.py files (ai-summary,
# fix-suggestion, config-status) into one Vercel serverless function - see
# api/audit-pipeline.py's module docstring-equivalent comment for why
# (Vercel's Python builder reinstalls the full requirements.txt per function,
# so fewer functions means fewer ~14s installs at build time). Dispatch is by
# an "action" field in the POST body; callers POST to /api/ai with
# {"action": "summary"|"fix-suggestion", ...}. (The "chat" action + floating
# ChatWidget were removed in Session 24 - the AI is now focused on the
# per-page/per-issue personalized fix suggestions and the audit summary.)
_ACTIONS = {
    "summary": _handle_summary,
    "ask": _handle_ask,
    "fix-suggestion": _handle_fix_suggestion,
    "content-draft": _handle_content_draft,
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Config-status is the one GET in this group (just reports whether
        # server-side keys are set - env var or vault). Sign-in required so it
        # doesn't disclose config to anonymous callers (audit finding #8).
        try:
            require_authenticated(self)
        except AuthError as e:
            send_json(self, e.status, {"error": e.message})
            return
        send_json(self, 200, {
            "psiConfigured": bool(os.environ.get("PSI_API_KEY")) or bool(get_default_org_vaulted_key("psi")),
            "groqConfigured": bool(os.environ.get("GROQ_API_KEY")) or bool(get_default_org_vaulted_key("groq")),
        })

    def do_POST(self):
        # All POST actions spend AI/vaulted credentials -> require sign-in
        # (audit finding #4).
        try:
            require_authenticated(self)
        except AuthError as e:
            send_json(self, e.status, {"ok": False, "error": e.message})
            return
        try:
            payload = read_json_body(self)
        except (ValueError, json.JSONDecodeError):
            send_json(self, 400, {"ok": False, "error": "Request body must be valid JSON."})
            return
        except Exception:  # noqa: BLE001
            logger.exception("ai.py request body could not be parsed")
            send_json(self, 500, {"ok": False, "error": "Internal error while processing the request."})
            return

        action = payload.get("action")
        fn = _ACTIONS.get(action)
        if fn is None:
            send_json(self, 400, {"ok": False, "error": f"Unknown or missing action (expected one of {sorted(_ACTIONS)})"})
            return
        fn(self, payload)
