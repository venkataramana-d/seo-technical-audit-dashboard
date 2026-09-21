"""Content AI Agent - 09-AI-AGENT-SUBSYSTEMS.md §1.

Turns rule-based findings into specific, page-aware DRAFT fix content: rewritten
title / meta description (respecting the 60 / 155 char bounds), alt text, etc.
Everything is a draft - never written to pages; the user must explicitly accept.

Guardrail: the system prompt is scoped strictly to on-page SEO mechanics
(length, keyword placement, structure) - it must NOT invent business facts
(pricing, claims, credentials).

Pure module (LLM injected). `content_runner.py` does the DB I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

TITLE_MIN, TITLE_MAX = 30, 60
META_MIN, META_MAX = 70, 155

_SYSTEM = (
    "You are an SEO copy assistant. You rewrite on-page SEO elements - title tags, meta "
    "descriptions, image alt text - for length, clarity, and keyword placement only. You must "
    "NOT invent facts about the business (pricing, claims, awards, credentials); work only from "
    "the page content you are given. Every output is a draft a human will review before it goes "
    "live. Respond with ONLY a JSON object, no prose."
)


@dataclass
class PageContext:
    url: str
    title: str | None = None
    meta_description: str | None = None
    h1: str | None = None


@dataclass
class ContentDraft:
    suggestion_type: str  # title | meta_description | alt_text
    draft_text: str
    confidence: float | None
    model: str
    within_bounds: bool


def _confidence(obj: dict) -> float | None:
    c = obj.get("confidence")
    try:
        return max(0.0, min(1.0, float(c))) if c is not None else None
    except (TypeError, ValueError):
        return None


def suggest_title(llm, ctx: PageContext) -> ContentDraft | None:
    from modules.llm import parse_json_object

    user = (
        f"Page URL: {ctx.url}\nCurrent <title>: {ctx.title!r}\nH1: {ctx.h1!r}\n"
        f"Meta description: {ctx.meta_description!r}\n\n"
        f"Write an improved <title> of {TITLE_MIN}-{TITLE_MAX} characters that reflects the page's "
        f"primary topic. Respond as JSON: {{\"title\": \"...\", \"confidence\": 0.0-1.0}}."
    )
    resp = llm.complete(_SYSTEM, user, max_tokens=200)
    obj = parse_json_object(resp.text)
    if not obj or not str(obj.get("title", "")).strip():
        return None
    text = str(obj["title"]).strip()
    return ContentDraft(
        suggestion_type="title", draft_text=text, confidence=_confidence(obj),
        model=resp.model, within_bounds=TITLE_MIN <= len(text) <= TITLE_MAX,
    )


def suggest_meta_description(llm, ctx: PageContext) -> ContentDraft | None:
    from modules.llm import parse_json_object

    user = (
        f"Page URL: {ctx.url}\nTitle: {ctx.title!r}\nH1: {ctx.h1!r}\n"
        f"Current meta description: {ctx.meta_description!r}\n\n"
        f"Write an improved meta description of {META_MIN}-{META_MAX} characters that accurately "
        f"summarizes the page and encourages clicks, without inventing facts. Respond as JSON: "
        f"{{\"meta_description\": \"...\", \"confidence\": 0.0-1.0}}."
    )
    resp = llm.complete(_SYSTEM, user, max_tokens=300)
    obj = parse_json_object(resp.text)
    if not obj or not str(obj.get("meta_description", "")).strip():
        return None
    text = str(obj["meta_description"]).strip()
    return ContentDraft(
        suggestion_type="meta_description", draft_text=text, confidence=_confidence(obj),
        model=resp.model, within_bounds=META_MIN <= len(text) <= META_MAX,
    )


# Which issue types the Content Agent can currently draft a fix for.
SUPPORTED_ISSUE_TYPES = {
    "missing_title": suggest_title,
    "title_length": suggest_title,
    "missing_meta_description": suggest_meta_description,
    "meta_description_length": suggest_meta_description,
}


def draft_for_issue_type(llm, issue_type: str, ctx: PageContext) -> ContentDraft | None:
    generator = SUPPORTED_ISSUE_TYPES.get(issue_type)
    if generator is None:
        return None
    return generator(llm, ctx)
