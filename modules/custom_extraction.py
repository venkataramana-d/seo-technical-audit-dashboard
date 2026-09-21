"""Custom Extraction - Phase 4.5 (00-PLAN-OVERVIEW.md) / 08-SCREAMING-FROG-TECHNICAL-REFERENCE.md §4.

User-defined scraping of arbitrary data from crawled pages via XPath, CSS-Path,
or Regex, matching Screaming Frog's feature exactly:

  - up to 100 extractors per crawl
  - a combined cap of 1,000 total extracted values across all extractors in one crawl
  - selector types: xpath | css | regex  (CSS is translated to XPath, not a
    separate engine - same as SF)
  - each extractor targets the raw (unrendered) or rendered HTML  (rendered is
    only meaningful once Playwright rendering lands in Phase 4; until then a
    'rendered' extractor simply runs against whatever HTML it is handed and the
    caller decides what that is)

Config lives in crawl_configs.custom_extractors (JSON list, already in the
schema); results are returned as ExtractionResult records for the caller to
persist per page. This module is pure - no DB, no network - so it is fully
unit-testable on HTML fixtures.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from lxml import html as lxml_html
from lxml.etree import XPath, XPathError

# Screaming Frog's confirmed caps (08-SF §4).
MAX_EXTRACTORS_PER_CRAWL = 100
MAX_TOTAL_EXTRACTIONS_PER_CRAWL = 1_000


class SelectorType(str, Enum):
    xpath = "xpath"
    css = "css"
    regex = "regex"


class SourceType(str, Enum):
    raw = "raw"
    rendered = "rendered"


@dataclass
class CustomExtractor:
    name: str
    selector_type: SelectorType
    expression: str
    source: SourceType = SourceType.raw


@dataclass
class ExtractionResult:
    extractor_name: str
    values: list[str] = field(default_factory=list)
    error: str | None = None  # populated when the expression fails to compile/run

    @property
    def match_count(self) -> int:
        return len(self.values)


class ExtractorConfigError(ValueError):
    """Raised when a custom_extractors config is structurally invalid."""


# ---- Config parsing / validation ----

def parse_extractors(raw_config: list[dict] | None) -> list[CustomExtractor]:
    """Validate and normalize the crawl_configs.custom_extractors JSON into typed
    extractors. Enforces the 100-extractor cap and rejects malformed entries so a
    bad config fails fast at crawl-setup time, not silently per page."""
    if not raw_config:
        return []
    if len(raw_config) > MAX_EXTRACTORS_PER_CRAWL:
        raise ExtractorConfigError(
            f"{len(raw_config)} extractors configured; the maximum is {MAX_EXTRACTORS_PER_CRAWL}."
        )

    extractors: list[CustomExtractor] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(raw_config):
        if not isinstance(entry, dict):
            raise ExtractorConfigError(f"Extractor #{i} is not an object.")
        name = (entry.get("name") or "").strip()
        if not name:
            raise ExtractorConfigError(f"Extractor #{i} is missing a name.")
        if name in seen_names:
            raise ExtractorConfigError(f"Duplicate extractor name: {name!r}.")
        seen_names.add(name)

        try:
            selector_type = SelectorType(str(entry.get("selector_type", "")).lower())
        except ValueError:
            raise ExtractorConfigError(
                f"Extractor {name!r}: selector_type must be one of "
                f"{[t.value for t in SelectorType]}."
            )
        try:
            source = SourceType(str(entry.get("source", "raw")).lower())
        except ValueError:
            raise ExtractorConfigError(
                f"Extractor {name!r}: source must be one of {[t.value for t in SourceType]}."
            )

        expression = entry.get("expression") or ""
        if not expression.strip():
            raise ExtractorConfigError(f"Extractor {name!r}: expression is empty.")

        extractors.append(CustomExtractor(name, selector_type, expression, source))
    return extractors


# ---- Value extraction ----

def _node_to_text(node) -> str:
    """Normalize an lxml result node/attribute/string to plain text."""
    if isinstance(node, str):
        return node.strip()
    # element nodes -> their text content
    text = node.text_content() if hasattr(node, "text_content") else str(node)
    return " ".join(text.split())


def _run_xpath(tree, expression: str) -> list[str]:
    compiled = XPath(expression)
    result = compiled(tree)
    if not isinstance(result, list):  # xpath can return a scalar (e.g. count())
        return [str(result).strip()]
    return [_node_to_text(n) for n in result]


def _run_css(tree, expression: str) -> list[str]:
    # CSSSelector translates the CSS path to XPath under the hood - same approach
    # SF documents. Import locally so a missing optional dep only affects CSS.
    from lxml.cssselect import CSSSelector
    selector = CSSSelector(expression)
    return [_node_to_text(n) for n in selector(tree)]


def _run_regex(html_text: str, expression: str) -> list[str]:
    pattern = re.compile(expression, re.IGNORECASE | re.DOTALL)
    out: list[str] = []
    for m in pattern.finditer(html_text):
        # If the pattern has a capturing group, return group 1; else the whole match.
        out.append((m.group(1) if m.groups() else m.group(0)).strip())
    return out


def run_extractor(extractor: CustomExtractor, html_text: str, tree=None) -> ExtractionResult:
    """Run one extractor against one page's HTML. Compile/runtime errors are
    captured on the result (never raised) so one bad expression can't abort the
    whole page's crawl."""
    try:
        if extractor.selector_type is SelectorType.regex:
            values = _run_regex(html_text, extractor.expression)
        else:
            if tree is None:
                tree = lxml_html.fromstring(html_text)
            if extractor.selector_type is SelectorType.xpath:
                values = _run_xpath(tree, extractor.expression)
            else:
                values = _run_css(tree, extractor.expression)
    except (XPathError, re.error, ValueError, SyntaxError) as exc:
        return ExtractionResult(extractor.name, [], error=f"{type(exc).__name__}: {exc}")
    # drop empty strings - an empty capture is not a useful extracted value
    values = [v for v in values if v]
    return ExtractionResult(extractor.name, values)


def run_extractors(
    extractors: list[CustomExtractor],
    html_text: str,
    remaining_budget: int = MAX_TOTAL_EXTRACTIONS_PER_CRAWL,
) -> tuple[list[ExtractionResult], int]:
    """Run all extractors against one page, honoring the crawl-wide 1,000-value
    combined cap.

    `remaining_budget` is the values still allowed for this crawl; pass the value
    returned by the previous page's call so the cap spans the whole crawl, not a
    single page. Returns (results, new_remaining_budget). Once the budget hits 0,
    further values are truncated (recorded via the shortened `values` list) rather
    than silently dropped without trace.
    """
    results: list[ExtractionResult] = []
    tree = None
    if any(e.selector_type is not SelectorType.regex for e in extractors):
        try:
            tree = lxml_html.fromstring(html_text) if html_text else None
        except (ValueError, SyntaxError):
            tree = None

    for extractor in extractors:
        if remaining_budget <= 0:
            results.append(ExtractionResult(extractor.name, [], error="crawl_extraction_cap_reached"))
            continue
        result = run_extractor(extractor, html_text, tree=tree)
        if result.values and len(result.values) > remaining_budget:
            result.values = result.values[:remaining_budget]
            result.error = "crawl_extraction_cap_reached"
        remaining_budget -= len(result.values)
        results.append(result)
    return results, remaining_budget
