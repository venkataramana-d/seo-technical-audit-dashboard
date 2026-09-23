"""Groq AI assistance layer: plain-English audit summary.
Groq is OpenAI-compatible, fast, and has a generous free tier.
API key: https://console.groq.com -> API Keys. Env var: GROQ_API_KEY.

Ported from the standalone Streamlit SEO audit tool's core/ai_assist.py,
adapted to this project's {issue, category, severity, recommendation,
impact_score, effort} issue schema.
"""

import json
import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
# llama-3.3-70b-versatile (still free on Groq) over the weaker 8b-instant: the
# audit summary + fix drafts are reasoning tasks where the larger model is more
# reliable and specific, at negligible extra latency (~1s vs 0.7s, single call).
_DEFAULT_MODEL = "llama-3.3-70b-versatile"
_MAX_AUDIT_CHARS = 8000

# NOTE: the multi-turn chatbot (`chat_with_assistant`, `_trim_chat_messages`,
# the app-help system prompt) and its floating ChatWidget were removed in
# Session 24. The AI layer is now focused on two grounded, per-page tasks:
# `explain_audit` (the audit summary) and `suggest_fix` (personalized fix
# drafts). Do not reintroduce a general-purpose chatbot without discussing it.


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _chat(messages, api_key, model=_DEFAULT_MODEL, temperature=0.4, max_tokens=800, json_mode=False):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    last_status = None
    for attempt in range(3):
        resp = requests.post(_GROQ_CHAT_URL, headers=headers, json=body, timeout=30)
        last_status = resp.status_code
        if json_mode and resp.status_code == 400 and "response_format" in body:
            # This model/account doesn't support JSON mode; drop it and retry
            # plain rather than burning the whole call on an unsupported param.
            body.pop("response_format")
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < 2:
                retry_after = resp.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if (retry_after and retry_after.replace(".", "", 1).isdigit())
                    else 0.5 * (2 ** attempt)
                )
                time.sleep(delay)
                continue
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    raise RuntimeError(f"Groq API unavailable (HTTP {last_status})")


def _parse_summary_reply(reply: str) -> tuple[str, list[str]]:
    """Parse explain_audit's model reply. Tries JSON first (the mode
    explain_audit requests via _chat's json_mode=True); falls back to the
    legacy numbered-list regex parse for a model/account that doesn't honor
    response_format, or an occasional malformed reply."""
    try:
        data = json.loads(reply)
        explanation = str(data.get("explanation", "")).strip()
        top_actions = [str(a).strip() for a in (data.get("top_actions") or []) if str(a).strip()]
        if explanation or top_actions:
            return explanation, top_actions[:5]
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass

    lines_out = [ln.strip() for ln in reply.split("\n") if ln.strip()]
    action_pattern = re.compile(r"^[\d]+[\.\)]\s+")
    explanation_lines = [ln for ln in lines_out if not action_pattern.match(ln)]
    action_lines = [re.sub(r"^[\d]+[\.\)]\s+", "", ln) for ln in lines_out if action_pattern.match(ln)]
    return " ".join(explanation_lines[:4]), action_lines[:5]


_SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Warning": 3, "Low": 4, "Notice": 5}


def _aggregate_issues(all_issues: list[dict]) -> tuple[list[dict], dict]:
    """Deduplicate issues by title into a count-annotated, severity-sorted digest.

    A sitewide audit passes the SAME issue title once per affected page (e.g.
    "Missing meta description" x 180), so the raw list is mostly repeats. Feeding
    those repeats to the model wasted the character budget on duplicates - the
    truncation then silently dropped rare-but-severe issues off the end, and the
    model couldn't state how many pages each issue hit. Aggregating by title
    keeps every DISTINCT issue in the budget, records the affected-page count,
    and lets the model cite accurate numbers.

    Returns (aggregated_list, severity_totals). `aggregated_list` is sorted by
    severity (Critical first) then affected-page count, each entry carrying
    {issue, severity, category, recommendation, count, impact_score}.
    """
    by_severity = {"Critical": 0, "High": 0, "Medium": 0, "Warning": 0, "Low": 0}
    agg: dict[str, dict] = {}
    for issue in all_issues:
        sev = issue.get("severity", "Low")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        title = str(issue.get("issue", "")).strip()
        if not title:
            continue
        entry = agg.get(title)
        if entry:
            entry["count"] += 1
            # Keep the most severe classification seen for this title.
            if _SEVERITY_RANK.get(sev, 4) < _SEVERITY_RANK.get(entry["severity"], 4):
                entry["severity"] = sev
        else:
            agg[title] = {
                "issue": title,
                "severity": sev,
                "category": issue.get("category", ""),
                "recommendation": str(issue.get("recommendation", "")).strip(),
                "count": 1,
                "impact_score": issue.get("impact_score", 0),
            }
    ordered = sorted(
        agg.values(),
        key=lambda e: (_SEVERITY_RANK.get(e["severity"], 4), -e["count"], -e.get("impact_score", 0)),
    )
    return ordered, by_severity


def answer_crawl_question(question: str, all_issues: list[dict], seo_score: float,
                          api_key: str, url: str = "", context_label: str | None = None,
                          model: str = _DEFAULT_MODEL) -> dict:
    """Answer a natural-language question about an audit/crawl, grounded ONLY in
    the supplied issue data (M4 T4.2 "ask your crawl"). Returns
    {ok, answer, model} or {ok: False, error}. Strictly no-hallucination: the
    model is told to answer only from the data and say so when the answer isn't
    present."""
    if not api_key:
        return {"ok": False, "error": "Groq API key not configured"}
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "Ask a question about the audit."}
    if len(question) > 500:
        question = question[:500]

    aggregated, by_severity = _aggregate_issues(all_issues)
    is_sitewide = any(e["count"] > 1 for e in aggregated)
    lines = []
    for e in aggregated:
        scope = f" (on {e['count']} pages)" if e["count"] > 1 else ""
        rec = f" Fix: {e['recommendation']}" if e["recommendation"] else ""
        lines.append(f"[{e['severity'].upper()}] {e['category']}: {e['issue']}{scope}.{rec}")
    digest = "\n".join(lines)
    if len(digest) > _MAX_AUDIT_CHARS:
        digest = digest[:_MAX_AUDIT_CHARS]
    totals_line = ", ".join(f"{n} {sev}" for sev, n in by_severity.items() if n)
    site_context = context_label or (f"for {url}" if url else "this audit")

    system_msg = (
        "You are an expert technical SEO consultant answering a website owner's "
        "question about their SEO audit. Answer ONLY using the audit data provided "
        "- the deduplicated issues (each annotated with severity, category, "
        "affected-page count, and fix) and the severity totals. Do NOT invent "
        "issues, numbers, URLs, or facts not present in the data. If the data does "
        "not contain the answer, say so plainly and suggest what to check instead. "
        "Be concise and specific, cite the real issues and their counts, and give "
        "actionable next steps when relevant. Plain English, no fluff."
    )
    scope_hint = (
        "This is a sitewide audit; counts show how many pages each issue affects. "
        if is_sitewide else
        "This is a single-page audit; every issue is on this one page. "
    )
    user_msg = (
        f"SEO health score: {seo_score}/100. {scope_hint}"
        f"Issue totals by severity: {totals_line or 'none'}. "
        f"Audit data {site_context}:\n\n{digest or '(no issues found)'}\n\n"
        f"Question: {question}\n\n"
        'Respond with ONLY a JSON object: {"answer": "your grounded answer"}. '
        "Keep the answer under ~150 words."
    )
    try:
        reply = _chat(
            [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            api_key, model=model, max_tokens=500, json_mode=True,
        )
        answer = ""
        try:
            answer = (json.loads(reply).get("answer") or "").strip()
        except (json.JSONDecodeError, AttributeError):
            answer = reply.strip()  # model ignored JSON mode; use raw text
        if not answer:
            return {"ok": False, "error": "The assistant did not return an answer."}
        return {"ok": True, "answer": answer, "model": model}
    except Exception as exc:
        logger.warning("answer_crawl_question failed: %s", exc)
        return {"ok": False, "error": _safe_error(exc)}


def explain_audit(all_issues: list[dict], seo_score: float, api_key: str,
                   url: str = "", context_label: str | None = None, model: str = _DEFAULT_MODEL) -> dict:
    """Summarise SEO audit issues in plain English.

    `context_label`, when given, overrides the default "for {url}" phrasing
    (e.g. "across 42 audited pages (sitewide)" for the Results page's rollup
    summary, which passes the aggregated issue list across every audited URL
    instead of one page's).

    Returns {ok, explanation, top_actions, model, stats} or {ok: False, error}.
    """
    if not api_key:
        return {"ok": False, "error": "Groq API key not configured"}

    aggregated, by_severity = _aggregate_issues(all_issues)
    is_sitewide = any(e["count"] > 1 for e in aggregated)

    lines = []
    for e in aggregated:
        # "xN pages" only makes sense sitewide; on a single page every count is 1.
        scope = f" (on {e['count']} pages)" if e["count"] > 1 else ""
        rec = f" Fix: {e['recommendation']}" if e["recommendation"] else ""
        lines.append(f"[{e['severity'].upper()}] {e['category']}: {e['issue']}{scope}.{rec}")

    summary_text = "\n".join(lines)
    if len(summary_text) > _MAX_AUDIT_CHARS:
        summary_text = summary_text[:_MAX_AUDIT_CHARS]

    totals_line = ", ".join(f"{n} {sev}" for sev, n in by_severity.items() if n)
    site_context = context_label or (f"for {url}" if url else "")
    system_msg = (
        "You are an expert technical SEO consultant. Given a deduplicated list of "
        "SEO audit issues (each already annotated with its severity, category, how "
        "many pages it affects, and the recommended fix), explain the findings "
        "clearly to a non-technical website owner. Use plain English. Be specific "
        "and reference the ACTUAL issues and their affected-page counts - do not "
        "invent issues, numbers, or facts not present in the data. Prioritise by "
        "severity and reach (an issue on many pages matters more than one on a "
        "single page). Skip anything that passed."
    )
    scope_hint = (
        "This is a sitewide audit; the counts show how many pages each issue affects. "
        if is_sitewide else
        "This is a SINGLE-PAGE audit - every issue below is on THIS ONE page. Do NOT "
        "claim or imply the issues affect 'multiple pages', 'several pages', or "
        "'across the site'; refer only to this page. "
    )
    user_msg = (
        f"SEO health score: {seo_score}/100. {scope_hint}"
        f"Issue totals by severity: {totals_line or 'none'}. "
        f"Deduplicated issues found {site_context}:\n\n"
        f"{summary_text or '(no issues found)'}\n\n"
        "Respond with ONLY a JSON object of this exact shape, no other text:\n"
        '{"explanation": "3-5 sentence plain-English assessment of overall technical '
        'SEO health that names the most important problems and, where relevant, how '
        'many pages they affect", '
        '"top_actions": ["specific action starting with a verb, referencing the real '
        'issue", "..."]}\n'
        "Include 3-6 items in top_actions, ordered by priority (most severe / most "
        "widespread first). Each action must correspond to an actual issue above. Do "
        "not repeat the raw list back verbatim."
    )

    try:
        reply = _chat(
            [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            api_key, model=model, max_tokens=900, json_mode=True,
        )
        explanation, top_actions = _parse_summary_reply(reply)
        return {
            "ok": True,
            "explanation": explanation,
            "top_actions": top_actions,
            "model": model,
            "stats": by_severity,
        }
    except Exception as exc:
        logger.warning("explain_audit failed: %s", exc)
        return {"ok": False, "error": _safe_error(exc)}


# ── Specific fix suggestions ─────────────────────────────────────────────────
# Unlike explain_audit (a narrative summary), this drafts an actual ready-to-use
# replacement value for a well-defined set of issue types - "add a meta
# description" becomes a real 150-160 char draft grounded in THIS page's content,
# not generic advice. Only supports issue titles matching _FIX_TARGET_PATTERNS;
# anything else returns ok:False so the caller can hide the "Suggest a fix"
# action rather than show a useless generic reply. Keep this list in sync with
# lib/fixSuggestable.ts's FIX_TARGET_PATTERNS.

_FIX_TARGET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"missing meta title|meta title too (short|long)|title tag", re.I), "title"),
    (re.compile(r"missing meta description|meta description too (short|long)", re.I), "description"),
    (re.compile(r"missing h1|h1 heading is too (short|long)|multiple h1", re.I), "h1"),
    (re.compile(r"open graph|missing og:|social preview|twitter card", re.I), "og"),
    (re.compile(r"missing alt|empty alt text|generic alt text", re.I), "alt"),
]

_FIX_TARGET_INSTRUCTIONS = {
    "title": (
        "Draft a page <title> tag, 30-60 characters, that accurately and specifically "
        "describes the page, includes the likely primary keyword naturally, and would "
        "stand out in a search results list. Output only the title text."
    ),
    "description": (
        "Draft a meta description, 150-160 characters, that summarizes the page and gives "
        "a compelling, specific reason to click through from search results. Output only "
        "the description text."
    ),
    "h1": (
        "Draft a single H1 heading, roughly 20-70 characters, that clearly states the "
        "page's main topic. Output only the heading text."
    ),
    "og": (
        "Draft Open Graph + Twitter Card meta tags for a compelling social-media share "
        "preview of THIS page. Output the ready-to-paste HTML: an og:title (<=60 chars), "
        "og:description (<=110 chars), og:type, twitter:card=summary_large_image, "
        "twitter:title and twitter:description, grounded in the page's real title and "
        "content. Output only the <meta> tags."
    ),
    "alt": (
        "Write concise, descriptive alt text (under ~125 characters, no 'image of' "
        "prefix, no keyword stuffing) appropriate for the key content images on THIS "
        "page, based on the page's actual topic. Give 2-3 example alt-text strings the "
        "author can adapt, each on its own line."
    ),
}


def detect_fix_target(issue_title: str) -> str | None:
    """Which concrete fix (if any) `suggest_fix` can draft for this issue
    title. Exposed separately from `suggest_fix` so callers (and the
    frontend's mirrored client-side check) can decide whether to show a
    "Suggest a fix" action without spending an API call to find out."""
    for pattern, target in _FIX_TARGET_PATTERNS:
        if pattern.search(issue_title):
            return target
    return None


# Targets whose fix is a single short value fit JSON mode well. og/alt produce
# multi-line HTML / several text lines and are requested as plain text instead.
_JSON_FIX_TARGETS = {"title", "description", "h1"}


def _strip_code_fence(text: str) -> str:
    """Remove a leading/trailing markdown code fence (```html … ```), which
    models sometimes wrap plain-text output in."""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.split("\n")
        lines = lines[1:] if lines and lines[0].startswith("```") else lines
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _extract_fix_suggestion(reply: str, structured: bool) -> tuple[str, str]:
    """Pull (suggestion, rationale) out of a suggest_fix reply. Plain-text
    replies are returned as-is; JSON replies are parsed and de-nested (some
    models double-wrap the payload as an object or an array inside `suggestion`).
    Falls back to the raw text if JSON parsing fails."""
    if not structured:
        return _strip_code_fence(reply).strip(), ""
    try:
        data = json.loads(reply)
        suggestion = str(data.get("suggestion", "")).strip()
        rationale = str(data.get("rationale", "")).strip()
    except (json.JSONDecodeError, AttributeError, TypeError):
        return _strip_code_fence(reply).strip(), ""

    stripped = suggestion.strip()
    if stripped[:1] in ("{", "["):
        try:
            inner = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            inner = None
        if isinstance(inner, dict) and inner.get("suggestion"):
            suggestion = str(inner.get("suggestion", "")).strip()
            rationale = rationale or str(inner.get("rationale", "")).strip()
        elif isinstance(inner, list):
            parts = []
            for el in inner:
                if isinstance(el, dict) and el.get("suggestion"):
                    parts.append(str(el["suggestion"]).strip())
                elif isinstance(el, str) and el.strip():
                    parts.append(el.strip())
            if parts:
                suggestion = "\n".join(parts)
    return suggestion, rationale


def suggest_fix(issue_title: str, page_context: dict, api_key: str, model: str = _DEFAULT_MODEL) -> dict:
    """Draft a concrete, ready-to-use fix for a metadata/H1 issue, grounded in
    the page's own content rather than invented facts.

    `page_context`: {url, title, description, h1, content_snippet} - all optional.

    Returns {ok, suggestion, rationale, target, model} or {ok: False, error}.
    """
    if not api_key:
        return {"ok": False, "error": "Groq API key not configured"}

    target = detect_fix_target(issue_title)
    if not target:
        return {"ok": False, "error": f"No fix generator available for: {issue_title}"}

    url = str(page_context.get("url", ""))[:300]
    title = str(page_context.get("title", ""))[:200]
    description = str(page_context.get("description", ""))[:400]
    h1 = str(page_context.get("h1", ""))[:200]
    snippet = str(page_context.get("content_snippet", ""))[:1500]

    system_msg = (
        "You are an expert technical SEO copywriter. Given real information about a page, "
        "draft a concrete, ready-to-use replacement for the specific element requested. "
        "Ground it in the page's actual content - do not invent facts, products, or claims "
        "not implied by the given context. Keep the tone matching the existing copy where possible."
    )
    base_ctx = (
        f"Page URL: {url or 'unknown'}\n"
        f"Current title: {title or '(none)'}\n"
        f"Current meta description: {description or '(none)'}\n"
        f"Current H1: {h1 or '(none)'}\n"
        f"Page content snippet: {snippet or '(none captured)'}\n\n"
        f"{_FIX_TARGET_INSTRUCTIONS[target]}\n\n"
    )
    # Single-value targets (title/description/H1) are reliable in JSON mode.
    # Multi-line targets (Open Graph tag block, several alt-text lines) are NOT:
    # wrapping multi-line HTML/text in a forced JSON object made models nest the
    # payload or return an empty "suggestion" ("didn't return a usable
    # suggestion"). Those get plain-text output instead, which is far more robust.
    structured = target in _JSON_FIX_TARGETS
    if structured:
        user_msg = base_ctx + (
            "Respond with ONLY a JSON object of this exact shape, no other text:\n"
            '{"suggestion": "the drafted text", "rationale": "one sentence on why this works"}'
        )
    else:
        user_msg = base_ctx + (
            "Output ONLY the requested text/tags - no JSON, no markdown code fences, "
            "no commentary, preamble, or explanation."
        )

    try:
        reply = _chat(
            [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            api_key, model=model, max_tokens=500, json_mode=structured,
        )
        suggestion, rationale = _extract_fix_suggestion(reply, structured)

        # Robust fallback: if we still have nothing usable, retry once as plain
        # text (no JSON constraint) - the most reliable shape for any target. The
        # extractor still de-JSONs the retry if the model ignores the request and
        # returns JSON anyway (a genuinely empty payload then stays empty → error).
        if not suggestion:
            retry = _chat(
                [{"role": "system", "content": system_msg},
                 {"role": "user", "content": base_ctx + "Output ONLY the requested text/tags, nothing else."}],
                api_key, model=model, max_tokens=500,
            )
            suggestion, rationale = _extract_fix_suggestion(retry, structured=True)

        if not suggestion:
            return {"ok": False, "error": "The assistant didn't return a usable suggestion."}
        return {"ok": True, "suggestion": suggestion, "rationale": rationale, "target": target, "model": model}
    except Exception as exc:
        logger.warning("suggest_fix failed: %s", exc)
        return {"ok": False, "error": _safe_error(exc)}
