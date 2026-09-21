"""Tests for modules/heading_auditor.py - in particular that heading
extraction excludes nav/footer/aside content, so a "related posts" widget or
site-wide nav after an article's real conclusion doesn't pollute the
hierarchy tree with unrelated headings."""

from bs4 import BeautifulSoup

from modules.heading_auditor import _extract_headings, analyze_heading_structure


def test_extract_headings_ignores_nav_footer_aside():
    html = """
    <html><body>
        <nav><h2>Site Nav Heading</h2></nav>
        <h1>Article Title</h1>
        <h2>Section One</h2>
        <p>Some content.</p>
        <h2>Conclusion</h2>
        <p>Final thoughts.</p>
        <footer><h3>Related Posts</h3><h3>Another Related Post</h3></footer>
        <aside><h2>Sidebar Widget</h2></aside>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    headings = _extract_headings(soup)
    texts = [h["text"] for h in headings]

    assert texts == ["Article Title", "Section One", "Conclusion"]
    assert "Site Nav Heading" not in texts
    assert "Related Posts" not in texts
    assert "Sidebar Widget" not in texts


def test_extract_headings_preserves_order_and_levels_for_plain_content():
    html = "<html><body><h1>Title</h1><h2>A</h2><h3>B</h3></body></html>"
    soup = BeautifulSoup(html, "lxml")
    headings = _extract_headings(soup)
    assert [(h["level"], h["text"]) for h in headings] == [(1, "Title"), (2, "A"), (3, "B")]


def test_h1_inside_header_is_counted_no_false_missing_h1():
    # The single most common title pattern is <header><h1>Title</h1></header>
    # (and <article><header class="entry-header"><h1>…). Stripping <header> used
    # to remove that H1 and fire a Critical "Missing H1 heading" false positive.
    html = """
    <html><body>
        <nav><a href="/">Home</a></nav>
        <header><h1>The Real Page Title</h1></header>
        <main><h2>Section</h2><p>Body.</p></main>
        <footer><h3>Related</h3></footer>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    result = analyze_heading_structure(soup)
    assert result["counts"]["h1"] == 1
    assert result["h1_text"] == "The Real Page Title"
    assert not any("Missing H1" in i["issue"] for i in result["issues"])


def test_analyze_heading_structure_counts_exclude_nav_footer():
    html = """
    <html><body>
        <nav><h2>Nav</h2></nav>
        <h1>Title</h1>
        <h2>Body</h2>
        <footer><h2>Footer Heading</h2></footer>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    result = analyze_heading_structure(soup)
    assert result["counts"]["h1"] == 1
    assert result["counts"]["h2"] == 1
