"""Tests for modules/renderer.py - real Playwright, no network dependency:
navigates to a data: URL so results are fully deterministic and offline-safe,
while still proving render_page() executes JavaScript rather than just
parsing raw markup (a data: URL served as-is would never contain the
JS-injected text)."""

from modules.renderer import render_page

_JS_INJECTED_TEXT = "JS-injected content here"

_HTML = (
    '<html><body><p id="target">Static text</p><script>'
    f'document.getElementById("target").insertAdjacentHTML("afterend", "<p>{_JS_INJECTED_TEXT}</p>");'
    "</script></body></html>"
)


def test_render_page_executes_javascript():
    result = render_page("data:text/html," + _HTML, timeout_ms=5000)

    assert result["success"] is True
    assert _JS_INJECTED_TEXT in result["html"]
    assert result["soup"].find(string=lambda t: _JS_INJECTED_TEXT in t) is not None


def test_render_page_returns_fetch_page_compatible_shape():
    result = render_page("data:text/html," + _HTML, timeout_ms=5000)

    for key in ("success", "status_code", "final_url", "redirect_count", "redirect_history",
                "content_type", "soup", "html", "response_time", "http_headers", "page_size_bytes"):
        assert key in result


def test_render_page_returns_failure_shape_on_bad_url():
    result = render_page("not-a-real-url-scheme://nope", timeout_ms=2000)

    assert result["success"] is False
    assert "error" in result
    assert result["status_code"] == 0


def test_render_page_blocks_ssrf_url_without_launching_browser():
    # A private/metadata host is rejected up front (SSRF), before any browser
    # launch or network call. http(s)-only; inert schemes (data:) are unaffected.
    for bad in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1/", "http://localhost/"):
        result = render_page(bad, timeout_ms=2000)
        assert result["success"] is False, bad
        assert result["status_code"] == 0
