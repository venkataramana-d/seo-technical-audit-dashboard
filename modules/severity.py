"""Issue severity model & per-project overrides - 02-AUDIT-ENGINE.md §3.

Errors / Warnings / Notices, layered on top of the existing Impact (1-10) /
Effort fields. Per Ahrefs' model, the severity of each issue *type* is a global
default that a project can override (e.g. a team may treat "missing alt text" as
a Warning rather than a Notice). Defaults live here as the single source of
truth; per-project overrides come from the issue_type_config table
(03-DATA-MODEL-AND-API.md), keyed by org.

This module is pure - the DB layer builds an {issue_type: severity} override map
from issue_type_config rows and passes it in; nothing here touches a Session.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping

ERROR = "error"
WARNING = "warning"
NOTICE = "notice"
VALID_SEVERITIES = frozenset({ERROR, WARNING, NOTICE})

# Global default severity per issue_type - mirrors what the check modules emit and
# is the authoritative catalogue. Every issue_type produced anywhere in the audit
# engine MUST appear here (guarded by test_severity.py). Grouped by source module.
DEFAULT_SEVERITIES: dict[str, str] = {
    # --- auditor.py (per-page) ---
    "missing_title": ERROR,
    "title_length": WARNING,
    "missing_meta_description": WARNING,
    "meta_description_length": WARNING,
    "missing_h1": WARNING,
    "multiple_h1": NOTICE,
    "missing_canonical": NOTICE,
    "canonical_points_elsewhere": NOTICE,
    "unrecognized_directive": NOTICE,
    "page_noindexed": ERROR,
    "url_too_long": WARNING,
    "url_contains_uppercase": NOTICE,
    "url_contains_underscore": NOTICE,
    "thin_content": WARNING,
    "images_missing_alt": WARNING,
    # --- advanced_checks.py (per-page) ---
    "schema_parse_error": ERROR,
    "missing_structured_data": NOTICE,
    "hreflang_limit_reached": NOTICE,
    "duplicate_hreflang_lang": WARNING,
    "missing_og_tags": NOTICE,
    "missing_og_image": NOTICE,
    "missing_twitter_card": NOTICE,
    "missing_viewport": ERROR,
    "missing_charset": WARNING,
    "missing_favicon": NOTICE,
    # --- sitewide.py (cross-page) ---
    "duplicate_title": WARNING,
    "duplicate_meta_description": WARNING,
    "duplicate_h1": NOTICE,
    "duplicate_content": WARNING,
    "redirect_loop": ERROR,
    "long_redirect_chain": WARNING,
    "orphan_page": NOTICE,
    "sitemap_stale_entry": WARNING,
    "sitemap_missing_page": NOTICE,
    "broken_internal_link": ERROR,
    "hreflang_no_return_tag": WARNING,
    # --- crawl_graph.py (cross-page) ---
    "page_excessive_crawl_depth": NOTICE,
    # --- near_duplicate.py (cross-page) ---
    "near_duplicate_content": WARNING,
}

# Fallback for an issue_type not present in the catalogue - should never happen in
# practice (the coverage test prevents it), but resolving must never crash a crawl.
_FALLBACK_SEVERITY = WARNING


class SeverityConfigError(ValueError):
    """Raised when an override specifies a severity outside the valid set."""


def default_severity(issue_type: str) -> str:
    return DEFAULT_SEVERITIES.get(issue_type, _FALLBACK_SEVERITY)


def resolve_severity(issue_type: str, overrides: Mapping[str, str] | None = None) -> str:
    """Effective severity for an issue_type: the project override if one is set,
    otherwise the global default."""
    if overrides:
        override = overrides.get(issue_type)
        if override is not None:
            if override not in VALID_SEVERITIES:
                raise SeverityConfigError(
                    f"Invalid severity override {override!r} for {issue_type!r}; "
                    f"must be one of {sorted(VALID_SEVERITIES)}."
                )
            return override
    return default_severity(issue_type)


def build_overrides(rows: Iterable) -> dict[str, str]:
    """Build an {issue_type: severity} override map from issue_type_config rows
    (or any objects/tuples exposing issue_type + severity_override). Rows with a
    null override are skipped; invalid severities are rejected.

    Accepts either objects with `.issue_type` / `.severity_override` attributes
    (the SQLAlchemy model) or (issue_type, severity_override) tuples.
    """
    overrides: dict[str, str] = {}
    for row in rows:
        if isinstance(row, tuple):
            issue_type, severity_override = row
        else:
            issue_type = getattr(row, "issue_type")
            severity_override = getattr(row, "severity_override", None)
        if severity_override is None:
            continue
        sev = severity_override.value if hasattr(severity_override, "value") else str(severity_override)
        if sev not in VALID_SEVERITIES:
            raise SeverityConfigError(
                f"Invalid severity override {sev!r} for {issue_type!r}; "
                f"must be one of {sorted(VALID_SEVERITIES)}."
            )
        overrides[issue_type] = sev
    return overrides


def apply_severity_overrides(issues: list, overrides: Mapping[str, str] | None = None) -> list:
    """Return the issues with each severity re-resolved against the overrides.

    Works on any dataclass issue carrying `issue_type` + `severity` fields
    (modules.types.AuditIssue and modules.sitewide.SiteIssue both qualify).
    Returns new instances (via dataclasses.replace) - never mutates the inputs -
    so it is safe to call before scoring without disturbing the originals.
    """
    if not overrides:
        return issues
    resolved = []
    for issue in issues:
        new_sev = resolve_severity(issue.issue_type, overrides)
        resolved.append(issue if new_sev == issue.severity else dataclasses.replace(issue, severity=new_sev))
    return resolved
