"""UI/UX AI Agent - 09-AI-AGENT-SUBSYSTEMS.md §4.

Turns raw crawl/issue data into role-appropriate, navigable views. Three jobs,
each with a hard guardrail:

  1. Natural-language crawl summary - a TEMPLATED narrative filled from real
     aggregates. Numbers are always interpolated from the query result, never
     produced by the model; the LLM (optional) only rephrases text it is handed.
  2. Ask-your-crawl - the LLM selects one of a fixed set of PARAMETERIZED query
     templates and fills typed, validated params. It never writes SQL. Crawled
     page content (potentially adversarial) never reaches a query-executing path.
  3. Role-adaptive default views - a pure view-configuration layer.

Pure module: aggregation/rendering/template-selection here; DB execution in
uiux_runner.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---- 1. Templated crawl summary ----

@dataclass
class SummaryFacts:
    total_pages: int
    health_score: float | None
    seo_score_avg: float | None
    health_delta: float | None  # vs previous crawl, or None
    top_issue_types: list[tuple[str, int]] = field(default_factory=list)  # (issue_type, count), desc
    new_error_count: int = 0


def render_summary(facts: SummaryFacts) -> str:
    """Deterministic narrative - every number comes straight from `facts`, so it
    cannot state a metric it wasn't given (the anti-hallucination guardrail)."""
    parts: list[str] = [f"This crawl covered {facts.total_pages} page(s)."]
    if facts.health_score is not None:
        s = f"Health Score is {facts.health_score}%"
        if facts.health_delta is not None and facts.health_delta != 0:
            direction = "up" if facts.health_delta > 0 else "down"
            s += f" ({direction} {abs(facts.health_delta)} pts since the previous crawl)"
        parts.append(s + ".")
    if facts.seo_score_avg is not None:
        parts.append(f"Average SEO Score is {facts.seo_score_avg}.")
    if facts.top_issue_types:
        top = ", ".join(f"{t} ({n})" for t, n in facts.top_issue_types[:3])
        parts.append(f"Most common issues: {top}.")
    if facts.new_error_count:
        parts.append(f"{facts.new_error_count} new error-severity issue(s) since last time.")
    return " ".join(parts)


_NARRATE_SYSTEM = (
    "You are a concise SEO report writer. Rewrite the given crawl summary in a friendly, "
    "professional tone for a dashboard. You MUST NOT change, add, or remove any number - use "
    "exactly the figures provided. Return only the rewritten summary text, no preamble."
)


def narrate(llm, facts: SummaryFacts) -> str:
    """Optional LLM rephrase of the deterministic summary. The numbers are baked
    into the text we hand the model; its job is phrasing, not arithmetic. On any
    failure, fall back to the deterministic render."""
    base = render_summary(facts)
    try:
        resp = llm.complete(_NARRATE_SYSTEM, base, max_tokens=300)
        return resp.text.strip() or base
    except Exception:
        return base


# ---- 2. Ask-your-crawl: fixed parameterized query templates ----

# Each template's SQL uses ONLY bound parameters. The model may pick a template
# name and fill declared params; it never supplies SQL. `param_kinds` drives
# server-side validation in uiux_runner.execute_template.
QUERY_TEMPLATES: dict[str, dict] = {
    "top_issue_types": {
        "description": "The most common issue types in this crawl, with counts.",
        "param_kinds": {},
        "sql": ("SELECT issue_type, count(*) AS n FROM issues "
                "WHERE crawl_id = :crawl_id GROUP BY issue_type ORDER BY n DESC LIMIT :limit"),
    },
    "pages_with_issue_type": {
        "description": "Pages that have a specific issue type (param: issue_type).",
        "param_kinds": {"issue_type": "issue_type"},
        "sql": ("SELECT DISTINCT p.url FROM issues i JOIN pages p ON p.id = i.page_id "
                "WHERE i.crawl_id = :crawl_id AND i.issue_type = :issue_type LIMIT :limit"),
    },
    "worst_scoring_pages": {
        "description": "Pages with the lowest SEO scores.",
        "param_kinds": {},
        "sql": ("SELECT url, seo_score FROM pages "
                "WHERE crawl_id = :crawl_id AND seo_score IS NOT NULL "
                "ORDER BY seo_score ASC LIMIT :limit"),
    },
    "broken_internal_links": {
        "description": "Broken internal links found in this crawl.",
        "param_kinds": {},
        "sql": ("SELECT explanation_json FROM issues "
                "WHERE crawl_id = :crawl_id AND issue_type = 'broken_internal_link' LIMIT :limit"),
    },
}


class QuerySelectionError(ValueError):
    pass


def _valid_issue_type(value) -> bool:
    from modules.severity import DEFAULT_SEVERITIES
    return isinstance(value, str) and value in DEFAULT_SEVERITIES


def validate_selection(template: str, params: dict) -> dict:
    """Server-side validation of a (template, params) selection. Rejects unknown
    templates and any param that isn't a declared, well-typed value - so nothing
    the model emits can reach SQL as anything but a bound, validated value."""
    spec = QUERY_TEMPLATES.get(template)
    if spec is None:
        raise QuerySelectionError(f"unknown query template: {template!r}")
    clean: dict = {}
    for name, kind in spec["param_kinds"].items():
        if kind == "issue_type":
            v = params.get("issue_type")
            if not _valid_issue_type(v):
                raise QuerySelectionError(f"invalid issue_type: {v!r}")
            clean["issue_type"] = v
    return clean


_SELECT_SYSTEM = (
    "You route a user's question about an SEO crawl to ONE of a fixed set of query templates. "
    "You never write SQL. Respond with ONLY a JSON object: "
    '{"template": "<one of the template names>", "params": { ... }}. '
    "Available templates:\n"
)


def select_query(llm, question: str) -> tuple[str, dict]:
    """LLM picks a template + params for the question. Validated before return;
    raises QuerySelectionError on an unusable choice."""
    from modules.llm import parse_json_object

    catalogue = "\n".join(f"- {name}: {spec['description']}" for name, spec in QUERY_TEMPLATES.items())
    resp = llm.complete(_SELECT_SYSTEM + catalogue, f"Question: {question}", max_tokens=200)
    obj = parse_json_object(resp.text)
    if not obj or "template" not in obj:
        raise QuerySelectionError("model did not return a template selection")
    template = obj["template"]
    params = obj.get("params") or {}
    clean = validate_selection(template, params if isinstance(params, dict) else {})
    return template, clean


# ---- 3. Role-adaptive default views ----

_DEV_VIEW = {"tabs": ["Technical", "Crawlability", "Links", "Metadata"],
             "primary_widget": "status_codes_and_redirects"}
DEFAULT_VIEWS: dict[str, dict] = {
    "developer": _DEV_VIEW,
    "marketer": {"tabs": ["Metadata", "Content", "Social & Schema", "Links"],
                 "primary_widget": "health_score_trend"},
    "executive": {"tabs": ["Overview", "Content", "Technical"],
                  "primary_widget": "health_score_trend"},
    "agency": {"tabs": ["Overview", "Crawlability", "Metadata", "Content", "Links", "Technical"],
               "primary_widget": "top_impact_fixes"},
}


def default_view_for_role(role: str) -> dict:
    return DEFAULT_VIEWS.get(role, _DEV_VIEW)
