"""Tests for modules/accessibility.py — the static WCAG audit engine.

Focus: each check fires on a genuinely broken control, and — critically — a
clean, accessible page produces ZERO Accessibility issues (no false positives).
"""

from bs4 import BeautifulSoup

from modules.accessibility import analyze_accessibility


def _issues(html):
    soup = BeautifulSoup(html, "lxml")
    return analyze_accessibility(soup, "https://example.com/").get("issues", [])


def _titles(issues):
    return [i["issue"] for i in issues]


def _find(issues, needle):
    return [i for i in issues if needle.lower() in i["issue"].lower()]


# ── 1. Form control labels ───────────────────────────────────────────────────

def test_unlabeled_input_is_flagged():
    issues = _issues("<html><body><form><input type='text' name='email'></form></body></html>")
    hits = _find(issues, "Accessible Label")
    assert hits, "expected an unlabeled input to be flagged"
    assert hits[0]["severity"] == "High"
    assert hits[0]["category"] == "Accessibility"
    assert hits[0]["affected"], "issue should list the offending control"


def test_label_for_input_is_not_flagged():
    html = ("<html><body><form>"
            "<label for='email'>Email</label>"
            "<input type='text' id='email' name='email'>"
            "</form></body></html>")
    assert not _find(_issues(html), "Accessible Label")


def test_aria_label_input_is_not_flagged():
    html = "<html><body><form><input type='text' name='q' aria-label='Search'></form></body></html>"
    assert not _find(_issues(html), "Accessible Label")


def test_wrapping_label_input_is_not_flagged():
    html = "<html><body><form><label>Email <input type='text' name='email'></label></form></body></html>"
    assert not _find(_issues(html), "Accessible Label")


def test_hidden_and_submit_inputs_are_ignored():
    html = ("<html><body><form>"
            "<input type='hidden' name='csrf'>"
            "<input type='submit' value='Go'>"
            "</form></body></html>")
    assert not _find(_issues(html), "Accessible Label")


# ── 2 & 3. Link / button names ────────────────────────────────────────────────

def test_empty_link_is_flagged():
    issues = _issues("<html><body><a href='/page'></a></body></html>")
    hits = _find(issues, "No Discernible Text")
    assert hits and hits[0]["severity"] == "High"


def test_link_with_text_is_not_flagged():
    assert not _find(_issues("<html><body><a href='/p'>Read more</a></body></html>"), "Discernible")


def test_link_with_aria_label_is_not_flagged():
    html = "<html><body><a href='/p' aria-label='Read the article'></a></body></html>"
    assert not _find(_issues(html), "Discernible")


def test_empty_button_is_flagged():
    assert _find(_issues("<html><body><button></button></body></html>"), "No Discernible Text")


def test_image_link_without_alt_is_flagged():
    html = "<html><body><a href='/home'><img src='logo.png' alt=''></a></body></html>"
    hits = _find(_issues(html), "Image Link")
    assert hits and hits[0]["severity"] == "High"


def test_image_link_with_alt_is_not_flagged():
    html = "<html><body><a href='/home'><img src='logo.png' alt='Home'></a></body></html>"
    issues = _issues(html)
    assert not _find(issues, "Image Link")
    assert not _find(issues, "Discernible")


# ── 4. Duplicate ids ──────────────────────────────────────────────────────────

def test_duplicate_ids_flagged():
    html = "<html><body><div id='dup'>a</div><span id='dup'>b</span></body></html>"
    hits = _find(_issues(html), "Duplicate id")
    assert hits and hits[0]["severity"] == "Medium"
    assert hits[0]["affected"][0]["value"] == "dup"


def test_unique_ids_not_flagged():
    html = "<html><body><div id='a'>x</div><span id='b'>y</span></body></html>"
    assert not _find(_issues(html), "Duplicate id")


# ── 5. Positive tabindex ──────────────────────────────────────────────────────

def test_positive_tabindex_flagged():
    hits = _find(_issues("<html><body><div tabindex='3'>x</div></body></html>"), "tabindex")
    assert hits and hits[0]["severity"] == "Medium"


def test_zero_and_negative_tabindex_not_flagged():
    html = "<html><body><div tabindex='0'>a</div><div tabindex='-1'>b</div></body></html>"
    assert not _find(_issues(html), "tabindex")


# ── 7-9. Advisory checks ──────────────────────────────────────────────────────

def test_data_table_without_th_is_flagged_notice():
    html = ("<html><body><table>"
            "<tr><td>a</td><td>b</td></tr>"
            "<tr><td>c</td><td>d</td></tr>"
            "</table></body></html>")
    hits = _find(_issues(html), "Header Cells")
    assert hits and hits[0]["severity"] == "Notice"


def test_table_with_th_is_not_flagged():
    html = ("<html><body><table>"
            "<tr><th scope='col'>H1</th><th scope='col'>H2</th></tr>"
            "<tr><td>c</td><td>d</td></tr>"
            "</table></body></html>")
    assert not _find(_issues(html), "Header Cells")


def test_placeholder_link_flagged_notice():
    hits = _find(_issues("<html><body><a href='#'>Toggle</a></body></html>"), "Placeholder Link")
    assert hits and hits[0]["severity"] == "Notice"


def test_invalid_role_flagged_notice():
    hits = _find(_issues("<html><body><div role='notarole'>x</div></body></html>"), "Invalid ARIA")
    assert hits and hits[0]["severity"] == "Notice"


def test_valid_role_not_flagged():
    assert not _find(_issues("<html><body><div role='navigation'>x</div></body></html>"), "Invalid ARIA")


# ── Clean page: zero false positives ──────────────────────────────────────────

def test_clean_accessible_page_has_no_issues():
    html = """
    <html lang="en"><head><title>Clean Page</title></head>
    <body>
      <nav aria-label="Main"><a href="/">Home</a> <a href="/about">About</a></nav>
      <main>
        <h1>Welcome</h1>
        <form>
          <label for="email">Email</label>
          <input type="text" id="email" name="email">
          <button type="submit">Subscribe</button>
        </form>
        <a href="/logo"><img src="logo.png" alt="Company logo"></a>
        <table>
          <tr><th scope="col">Name</th><th scope="col">Role</th></tr>
          <tr><td>Ada</td><td>Engineer</td></tr>
        </table>
        <div role="region" aria-label="Notes" tabindex="0">Notes</div>
      </main>
    </body></html>
    """
    issues = _issues(html)
    assert issues == [], f"expected zero issues on a clean page, got: {_titles(issues)}"


def test_malformed_dom_does_not_raise():
    # Deeply broken / partial markup must never raise.
    for bad in ("<html><body><a href", "<table><tr><td>", "", "<<>>"):
        soup = BeautifulSoup(bad, "lxml")
        assert isinstance(analyze_accessibility(soup).get("issues"), list)
    # None soup is tolerated too.
    assert analyze_accessibility(None).get("issues") == []
