"""Static accessibility (WCAG) audit engine.

Detects high-confidence WCAG 2.x failures directly from the parsed
BeautifulSoup DOM — no rendering, no network, no CSS/JS execution. Every
check is deliberately conservative: it should never fire on a well-built,
accessible page (false positives erode trust in the whole report).

Issues use the same shape as modules/auditor.py::_issue():
    {"issue", "category", "severity", "recommendation",
     "impact_score", "effort", "affected": [{"value", "detail"}, ...]}

Category is always "Accessibility". Severity tiers:
    Critical / High / Medium / Warning / Low / Notice
(Notice = advisory, 0 score penalty downstream.)

Checks that other modules already own are intentionally NOT duplicated here:
    - image alt text            -> modules/image_auditor.py
    - <html lang>               -> modules/advanced_checks.py
    - document <title>          -> modules/metadata (analyze_metadata)
    - heading order / skips     -> modules/heading_auditor.py
"""

import re

CATEGORY = "Accessibility"

# Input types that carry their own accessible name or are non-interactive,
# so they do not need an associated <label>.
_SKIP_INPUT_TYPES = {"hidden", "submit", "reset", "button", "image"}

# Comprehensive WAI-ARIA 1.2 role token set. Kept broad on purpose: an
# unrecognised role is only flagged when it matches nothing here, so a
# generous list means fewer false positives on valid but uncommon roles.
_VALID_ARIA_ROLES = {
    "alert", "alertdialog", "application", "article", "banner", "blockquote",
    "button", "caption", "cell", "checkbox", "code", "columnheader",
    "combobox", "command", "complementary", "composite", "contentinfo",
    "definition", "deletion", "dialog", "directory", "document", "emphasis",
    "feed", "figure", "form", "generic", "grid", "gridcell", "group",
    "heading", "img", "input", "insertion", "landmark", "link", "list",
    "listbox", "listitem", "log", "main", "marquee", "math", "menu",
    "menubar", "menuitem", "menuitemcheckbox", "menuitemradio", "meter",
    "navigation", "none", "note", "option", "paragraph", "presentation",
    "progressbar", "radio", "radiogroup", "range", "region", "roletype",
    "row", "rowgroup", "rowheader", "scrollbar", "search", "searchbox",
    "section", "sectionhead", "select", "separator", "slider", "spinbutton",
    "status", "strong", "structure", "subscript", "superscript", "switch",
    "tab", "table", "tablist", "tabpanel", "term", "textbox", "time",
    "timer", "toolbar", "tooltip", "tree", "treegrid", "treeitem", "widget",
    "window",
}


def _attr(el, name):
    """Return a stripped attribute value ('' when absent/empty/non-string)."""
    try:
        val = el.get(name)
        if isinstance(val, (list, tuple)):
            val = " ".join(str(v) for v in val)
        return (val or "").strip() if isinstance(val, str) or val is None else str(val).strip()
    except Exception:
        return ""


def _has(el, name):
    """True if the element has a non-empty value for attribute `name`."""
    return bool(_attr(el, name))


def _text(el):
    """Visible text content, stripped. (alt text is NOT included by get_text.)"""
    try:
        return el.get_text(" ", strip=True)
    except Exception:
        return ""


def _snippet(el, limit=120):
    """A short one-line snippet of an element's markup, for the `affected` detail."""
    try:
        s = re.sub(r"\s+", " ", str(el)).strip()
        return s[:limit] + ("…" if len(s) > limit else "")
    except Exception:
        return ""


def _img_with_alt(el):
    """True if `el` contains a descendant <img> whose alt has real text."""
    try:
        for img in el.find_all("img"):
            if _attr(img, "alt"):
                return True
    except Exception:
        pass
    return False


def _svg_named(el):
    """True if `el` contains an <svg> carrying its own accessible name."""
    try:
        for svg in el.find_all("svg"):
            if _has(svg, "aria-label") or _has(svg, "aria-labelledby") or svg.find("title"):
                return True
    except Exception:
        pass
    return False


def _has_accessible_name(el):
    """Conservative check: does the control expose ANY accessible name?"""
    if _text(el):
        return True
    if _has(el, "aria-label") or _has(el, "aria-labelledby") or _has(el, "title"):
        return True
    if _img_with_alt(el) or _svg_named(el):
        return True
    return False


# ── Individual checks ────────────────────────────────────────────────────────

def _check_form_labels(soup):
    """1. Form controls with no associated label/aria-label/aria-labelledby/title."""
    issues = []
    try:
        # Collect ids targeted by any <label for="...">.
        labelled_ids = set()
        for lab in soup.find_all("label"):
            fid = _attr(lab, "for")
            if fid:
                labelled_ids.add(fid)

        affected = []
        for el in soup.find_all(["input", "select", "textarea"]):
            try:
                if el.name == "input":
                    itype = (_attr(el, "type") or "text").lower()
                    if itype in _SKIP_INPUT_TYPES:
                        continue
                # Any of these gives the control an accessible name.
                if _has(el, "aria-label") or _has(el, "aria-labelledby") or _has(el, "title"):
                    continue
                el_id = _attr(el, "id")
                if el_id and el_id in labelled_ids:
                    continue
                # A wrapping <label> also labels the control.
                if el.find_parent("label") is not None:
                    continue
                ident = el_id or _attr(el, "name") or (_attr(el, "type") or el.name)
                affected.append({
                    "value": f"<{el.name}> {ident}".strip(),
                    "detail": "no <label for>, aria-label, aria-labelledby, or title",
                })
            except Exception:
                continue

        if affected:
            issues.append({
                "issue": "Form Control Without an Accessible Label",
                "category": CATEGORY,
                "severity": "High",
                "recommendation": ("Give every input, select and textarea an accessible name: a "
                                   "<label for> pointing at its id, a wrapping <label>, or an "
                                   "aria-label/aria-labelledby. WCAG 1.3.1 / 4.1.2."),
                "impact_score": 7,
                "effort": "Medium",
                "affected": affected,
            })
    except Exception:
        pass
    return issues


def _check_link_button_names(soup):
    """2 & 3. Links/buttons with no discernible text; image links with no name."""
    empty_controls = []   # check 2
    image_links = []      # check 3
    try:
        for el in soup.find_all(["a", "button"]):
            try:
                if el.name == "a" and not _has(el, "href"):
                    continue  # non-link anchors (named targets) are not controls
                if _has_accessible_name(el):
                    continue
                has_img = False
                try:
                    has_img = el.find("img") is not None
                except Exception:
                    has_img = False

                if el.name == "a" and has_img:
                    # Image used as a link but the link has no accessible name.
                    image_links.append({
                        "value": _attr(el, "href") or "<a>",
                        "detail": "image link with empty/missing alt and no other link text",
                    })
                else:
                    ident = (_attr(el, "href") or _snippet(el)) if el.name == "a" else _snippet(el)
                    empty_controls.append({
                        "value": ident or f"<{el.name}>",
                        "detail": f"<{el.name}> has no text, aria-label, aria-labelledby or title",
                    })
            except Exception:
                continue
    except Exception:
        pass

    issues = []
    if empty_controls:
        issues.append({
            "issue": "Link or Button With No Discernible Text",
            "category": CATEGORY,
            "severity": "High",
            "recommendation": ("Every link and button needs discernible text. Add visible text, "
                               "or an aria-label / aria-labelledby / title. WCAG 2.4.4 / 4.1.2."),
            "impact_score": 7,
            "effort": "Medium",
            "affected": empty_controls,
        })
    if image_links:
        issues.append({
            "issue": "Image Link Has No Accessible Name",
            "category": CATEGORY,
            "severity": "High",
            "recommendation": ("A link wrapping only an image needs an accessible name: give the "
                               "<img> meaningful alt text, or add aria-label to the link. WCAG 2.4.4 / 4.1.2."),
            "impact_score": 7,
            "effort": "Low",
            "affected": image_links,
        })
    return issues


def _check_duplicate_ids(soup):
    """4. Duplicate id attributes on the page."""
    issues = []
    try:
        counts = {}
        for el in soup.find_all(id=True):
            _id = _attr(el, "id")
            if _id:
                counts[_id] = counts.get(_id, 0) + 1
        dups = [{"value": _id, "detail": f"used {n} times"} for _id, n in counts.items() if n > 1]
        if dups:
            issues.append({
                "issue": "Duplicate id Attributes",
                "category": CATEGORY,
                "severity": "Medium",
                "recommendation": ("id values must be unique within a page; duplicates break label "
                                   "associations, aria references and in-page anchors. WCAG 4.1.1."),
                "impact_score": 5,
                "effort": "Medium",
                "affected": dups,
            })
    except Exception:
        pass
    return issues


def _check_positive_tabindex(soup):
    """5. Positive tabindex (> 0) disrupts natural focus order."""
    issues = []
    try:
        affected = []
        for el in soup.find_all(tabindex=True):
            try:
                raw = _attr(el, "tabindex")
                ti = int(raw)
            except (TypeError, ValueError):
                continue
            if ti > 0:
                ident = _attr(el, "id") or _attr(el, "name") or el.name
                affected.append({
                    "value": f"<{el.name}> {ident}".strip(),
                    "detail": f"tabindex={ti}",
                })
        if affected:
            issues.append({
                "issue": "Positive tabindex Disrupts Focus Order",
                "category": CATEGORY,
                "severity": "Medium",
                "recommendation": ("Avoid tabindex greater than 0; it forces an unnatural tab order. "
                                   "Use tabindex=\"0\" (or rely on DOM order) instead. WCAG 2.4.3."),
                "impact_score": 5,
                "effort": "Medium",
                "affected": affected,
            })
    except Exception:
        pass
    return issues


def _check_data_tables(soup):
    """7. Data tables (>1 row, >1 col) with no <th> and no scope. Advisory."""
    issues = []
    try:
        affected = []
        for idx, table in enumerate(soup.find_all("table")):
            try:
                # Skip explicit layout tables.
                if (_attr(table, "role") or "").lower() in ("presentation", "none"):
                    continue
                rows = table.find_all("tr")
                if len(rows) <= 1:
                    continue
                max_cols = 0
                for r in rows:
                    max_cols = max(max_cols, len(r.find_all(["td", "th"], recursive=False)))
                if max_cols <= 1:
                    continue
                has_th = table.find("th") is not None
                has_scope = table.find(attrs={"scope": True}) is not None
                if not has_th and not has_scope:
                    affected.append({
                        "value": f"table #{idx + 1}",
                        "detail": f"{len(rows)} rows x {max_cols} cols, no <th> and no scope",
                    })
            except Exception:
                continue
        if affected:
            issues.append({
                "issue": "Data Table Without Header Cells",
                "category": CATEGORY,
                "severity": "Notice",
                "recommendation": ("Advisory: data tables should use <th> header cells (with scope "
                                   "where needed) so screen readers can associate cells with headers. WCAG 1.3.1."),
                "impact_score": 3,
                "effort": "Medium",
                "affected": affected,
            })
    except Exception:
        pass
    return issues


def _check_placeholder_links(soup):
    """8. href="#" / javascript:void(0) used as a control without button semantics. Advisory."""
    issues = []
    try:
        affected = []
        for a in soup.find_all("a"):
            try:
                href = _attr(a, "href")
                if not href:
                    continue
                low = href.lower()
                is_placeholder = href == "#" or low.startswith("javascript:")
                if not is_placeholder:
                    continue
                role = (_attr(a, "role") or "").lower()
                if role in ("button", "menuitem", "tab", "link"):
                    continue
                affected.append({
                    "value": href,
                    "detail": "placeholder href used as a control without role=\"button\"",
                })
            except Exception:
                continue
        if affected:
            issues.append({
                "issue": "Placeholder Link Used as a Control",
                "category": CATEGORY,
                "severity": "Notice",
                "recommendation": ("Advisory: anchors with href=\"#\" or javascript:void(0) that act as "
                                   "controls should be <button>s, or at least carry role=\"button\" and "
                                   "keyboard handling. WCAG 4.1.2."),
                "impact_score": 2,
                "effort": "Medium",
                "affected": affected,
            })
    except Exception:
        pass
    return issues


def _check_invalid_roles(soup):
    """9. role attributes that match no known ARIA role. Advisory, conservative."""
    issues = []
    try:
        affected = []
        for el in soup.find_all(attrs={"role": True}):
            try:
                role_val = _attr(el, "role")
                if not role_val:
                    continue
                tokens = [t for t in role_val.split() if t]
                if not tokens:
                    continue
                # role supports fallback tokens; only flag when NONE is valid.
                if any(t.lower() in _VALID_ARIA_ROLES for t in tokens):
                    continue
                affected.append({
                    "value": f"<{el.name}> role=\"{role_val}\"",
                    "detail": "not a recognised ARIA role",
                })
            except Exception:
                continue
        if affected:
            issues.append({
                "issue": "Invalid ARIA role Value",
                "category": CATEGORY,
                "severity": "Notice",
                "recommendation": ("Advisory: the role attribute contains no recognised ARIA role, so "
                                   "assistive technology ignores it. Use a valid WAI-ARIA role. WCAG 4.1.2."),
                "impact_score": 2,
                "effort": "Low",
                "affected": affected,
            })
    except Exception:
        pass
    return issues


def analyze_accessibility(soup, url=""):
    """Run the static WCAG checks against a parsed BeautifulSoup document.

    Args:
        soup: a BeautifulSoup DOM of the page.
        url:  the page URL (accepted for signature parity; not required).

    Returns:
        {"issues": [<issue dicts>], "issue_count", "by_severity"}.
        Never raises: a malformed DOM yields an empty (or partial) result.
    """
    issues = []
    if soup is not None:
        for check in (
            _check_form_labels,
            _check_link_button_names,
            _check_duplicate_ids,
            _check_positive_tabindex,
            _check_data_tables,
            _check_placeholder_links,
            _check_invalid_roles,
        ):
            try:
                issues.extend(check(soup))
            except Exception:
                continue

    by_severity = {}
    for i in issues:
        sev = i.get("severity", "Notice")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return {
        "issues": issues,
        "issue_count": len(issues),
        "by_severity": by_severity,
    }
