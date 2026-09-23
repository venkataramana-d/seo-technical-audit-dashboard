"""Tests for modules/scoring.py: category-score penalties, weighted total,
HTTP-status adjustments, and thematic grouping (incl. the Image SEO fix)."""

from modules.scoring import (
    PENALTY,
    THEMES,
    WEIGHTS,
    calculate_seo_score,
    get_thematic_issues,
    get_top_issues_by_impact,
)


def _issue(category="Metadata", severity="Warning", impact=5):
    return {"issue": f"{category} problem", "category": category, "severity": severity,
            "recommendation": "fix it", "impact_score": impact, "effort": "Low"}


def test_emitted_categories_do_not_fall_into_other_bucket():
    # Every category string the check modules actually emit must map to a real
    # theme, not the catch-all "Other". These 7 used to fall through: Heading
    # Structure (keyword was "Headings", not a substring of "heading structure"),
    # the mobile-UX categories, and Security (mixed content).
    emitted = [
        "Heading Structure", "Security", "Responsiveness", "Usability",
        "Navigation", "User Experience", "Layout",
        # spot-check ones that were already mapped, to guard against regressions
        "Metadata", "Image SEO", "Mobile SEO", "Internal Links", "Site Health",
    ]
    grouped = get_thematic_issues([_issue(category=c) for c in emitted])
    assert "Other" not in grouped, f"unmapped categories fell into Other: {grouped.get('Other')}"


def test_heading_and_security_map_to_expected_themes():
    grouped = get_thematic_issues([
        _issue(category="Heading Structure"),
        _issue(category="Security"),
        _issue(category="Responsiveness"),
    ])
    assert any("Heading Structure problem" == i["issue"] for i in grouped.get("Content", []))
    assert any("Security problem" == i["issue"] for i in grouped.get("Site Health", []))
    assert any("Responsiveness problem" == i["issue"] for i in grouped.get("Technical", []))


def test_accessibility_issues_affect_the_score():
    # Accessibility (WCAG) issues are in all_issues but must also count toward the
    # score (folded into the "advanced" bucket), like mobile_audit.
    clean = calculate_seo_score({"status_code": 200})["score"]
    a11y_broken = calculate_seo_score({
        "status_code": 200,
        "accessibility": {"issues": [{
            "issue": "Form control without an accessible label", "category": "Accessibility",
            "severity": "High", "recommendation": "Add a label.", "impact_score": 7, "effort": "Low",
        }]},
    })["score"]
    assert a11y_broken < clean


def test_mobile_audit_issues_affect_the_score():
    # BUG#1: mobile_audit issues are in all_issues but used to contribute 0 to
    # the score. A page with a Critical mobile issue must score below a clean one.
    clean = calculate_seo_score({"status_code": 200})["score"]
    mobile_broken = calculate_seo_score({
        "status_code": 200,
        "mobile_audit": {"issues": [{
            "issue": "Missing viewport meta tag", "category": "Mobile Basics",
            "severity": "Critical", "recommendation": "Add a viewport meta tag.",
            "impact_score": 9, "effort": "Low",
        }]},
    })["score"]
    assert mobile_broken < clean


def test_normalize_issues_backfills_impact_and_effort():
    # BUG#2: some modules (blog_auditor) build issue dicts without impact_score /
    # effort. The all_issues normalizer must backfill both from severity.
    from modules.auditor import _normalize_issues
    out = _normalize_issues([
        {"issue": "Missing Author Information", "category": "Blog Content",
         "severity": "High", "recommendation": "Add an author byline."},
        {"issue": "x", "category": "y", "severity": "Low", "recommendation": "z", "impact_score": None},
    ])
    assert isinstance(out[0]["impact_score"], (int, float)) and out[0]["impact_score"] > 0
    assert out[0]["effort"]
    assert isinstance(out[1]["impact_score"], (int, float))  # None was backfilled


def test_perfect_result_scores_100():
    result = {"status_code": 200}
    out = calculate_seo_score(result)
    assert out["score"] == 100.0
    assert all(v == 100.0 for v in out["breakdown"].values())


def test_empty_category_scores_100():
    from modules.scoring import _category_score
    assert _category_score([]) == 100.0


def test_penalty_severities_subtract_correctly():
    from modules.scoring import _category_score
    # One Critical (25) + one Warning (6) = 31 penalty -> 69.
    score = _category_score([_issue(severity="Critical"), _issue(severity="Warning")])
    assert score == 100.0 - PENALTY["Critical"] - PENALTY["Warning"]
    assert score == 69.0


def test_category_score_floors_at_zero():
    from modules.scoring import _category_score
    many_criticals = [_issue(severity="Critical") for _ in range(10)]  # 250 penalty
    assert _category_score(many_criticals) == 0.0


def test_fetch_error_zeroes_the_score():
    assert calculate_seo_score({"status_code": 0, "fetch_error": "boom"})["score"] == 0.0
    assert calculate_seo_score({"status_code": 0})["score"] == 0.0


def test_4xx_status_penalizes_50():
    # All categories perfect (100) but a 404 status subtracts 50.
    out = calculate_seo_score({"status_code": 404})
    assert out["score"] == 50.0


def test_3xx_status_penalizes_10():
    out = calculate_seo_score({"status_code": 301})
    assert out["score"] == 90.0


def test_weights_sum_to_one():
    # The weighted total only equals a clean 0-100 if the weights sum to 1.
    assert round(sum(WEIGHTS.values()), 6) == 1.0


def test_scoring_uses_image_detail_over_legacy_images():
    # A clean image_detail should keep images at 100 even if legacy images has issues.
    result = {
        "status_code": 200,
        "image_detail": {"issues": []},
        "images": {"issues": [_issue(category="Images", severity="Critical")]},
    }
    assert calculate_seo_score(result)["breakdown"]["images"] == 100.0


def test_image_seo_category_groups_under_images_theme():
    grouped = get_thematic_issues([_issue(category="Image SEO", severity="High")])
    assert "Images" in grouped
    assert len(grouped["Images"]) == 1
    assert "Other" not in grouped  # must NOT fall into the catch-all bucket


def test_themes_cover_expected_buckets():
    assert "Image SEO" in THEMES["Images"]


def test_top_issues_sorted_by_impact_descending():
    issues = [_issue(impact=3), _issue(impact=9), _issue(impact=6)]
    top = get_top_issues_by_impact(issues, top_n=2)
    assert [i["impact_score"] for i in top] == [9, 6]
