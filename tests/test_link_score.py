"""M3 T3.1: internal PageRank / Link Score + granular directive validation."""

from bs4 import BeautifulSoup

from modules.sitewide import SiteLink
from modules.link_score import build_link_score_report, compute_pagerank
from modules.crawl_graph import build_link_graph
from modules.auditor import analyze_indexability


def _links(pairs):
    return [SiteLink(source_url=s, target_url=t, link_type="internal") for s, t in pairs]


def test_link_score_ranks_well_linked_page_highest():
    # home -> a, home -> b, a -> b : b has the most internal authority.
    root = "https://x.com/"
    pages = {root, "https://x.com/a", "https://x.com/b"}
    links = _links([
        (root, "https://x.com/a"),
        (root, "https://x.com/b"),
        ("https://x.com/a", "https://x.com/b"),
    ])
    rep = build_link_score_report(pages, links, root=root)
    # b is linked by both home and a -> should outscore a.
    assert rep.scores["https://x.com/b"] > rep.scores["https://x.com/a"]
    # Top page normalizes to 100.
    assert max(rep.scores.values()) == 100
    # Inlink/outlink counts.
    assert rep.inlinks["https://x.com/b"] == 2
    assert rep.outlinks[root] == 2
    assert rep.inlinks[root] == 0


def test_link_score_orphan_scores_low():
    root = "https://x.com/"
    pages = {root, "https://x.com/hub", "https://x.com/orphan"}
    links = _links([(root, "https://x.com/hub")])  # orphan has no inlinks
    rep = build_link_score_report(pages, links, root=root)
    assert rep.inlinks["https://x.com/orphan"] == 0
    assert rep.scores["https://x.com/orphan"] <= rep.scores["https://x.com/hub"]


def test_pagerank_conserves_total_rank():
    nodes = {"a", "b", "c"}
    graph = build_link_graph(_links([("a", "b"), ("b", "c"), ("c", "a")]), nodes)
    rank = compute_pagerank(nodes, graph)
    assert abs(sum(rank.values()) - 1.0) < 1e-6
    # Symmetric cycle -> roughly equal ranks.
    assert max(rank.values()) - min(rank.values()) < 1e-3


def test_empty_and_single_page():
    assert build_link_score_report(set(), []).scores == {}
    # A lone page is trivially the top (and only) page -> normalizes to 100.
    rep = build_link_score_report({"https://x.com/"}, [])
    assert rep.scores["https://x.com/"] == 100
    assert rep.inlinks["https://x.com/"] == 0


def test_unrecognized_meta_robots_directive_flagged():
    soup = BeautifulSoup(
        '<html><head><meta name="robots" content="no-index, follow"></head><body></body></html>',
        "lxml",
    )
    result = analyze_indexability(soup)
    titles = [i["issue"] for i in result["issues"]]
    assert any("Unrecognized meta robots directive" in t and "no-index" in t for t in titles)
    # A valid directive string must NOT be flagged.
    ok = analyze_indexability(BeautifulSoup(
        '<html><head><meta name="robots" content="noindex, nofollow, max-snippet:-1"></head></html>',
        "lxml",
    ))
    assert not any("Unrecognized" in i["issue"] for i in ok["issues"])
