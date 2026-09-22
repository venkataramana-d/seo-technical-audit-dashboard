"""Internal PageRank / Link Score (M3 T3.1).

Screaming Frog's "Link Score" is a 0-100 relative measure of a page's internal
authority: an internal-PageRank over the site's own link graph. A page that many
important pages link to scores high; an orphan-ish page scores low. It's the
single best signal for "which pages is my internal linking actually promoting?"

Pure module - operates on `sitewide.SiteLink` records + the set of crawled page
URLs (same inputs as modules/crawl_graph.py), no DB. Reuses build_link_graph so
the graph definition stays identical to the depth report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from modules.crawl_graph import build_link_graph
from modules.sitewide import SiteLink

DAMPING = 0.85          # standard PageRank damping factor
MAX_ITER = 100
TOLERANCE = 1e-9        # L1 convergence threshold


def compute_pagerank(
    nodes: set[str],
    graph: dict[str, set[str]],
    damping: float = DAMPING,
    max_iter: int = MAX_ITER,
    tolerance: float = TOLERANCE,
) -> dict[str, float]:
    """Iterative PageRank over `nodes` using `graph` (adjacency of internal
    links). Dangling nodes (no out-links) redistribute their rank uniformly, so
    total rank is conserved. Returns raw rank values summing to ~1.0."""
    n = len(nodes)
    if n == 0:
        return {}
    node_list = list(nodes)
    # Only keep out-edges that point at known nodes.
    out: dict[str, list[str]] = {
        u: [t for t in graph.get(u, ()) if t in nodes] for u in node_list
    }
    rank = {u: 1.0 / n for u in node_list}

    for _ in range(max_iter):
        dangling_sum = sum(rank[u] for u in node_list if not out[u])
        base = (1.0 - damping) / n + damping * dangling_sum / n
        new = {u: base for u in node_list}
        for u in node_list:
            outs = out[u]
            if outs:
                share = damping * rank[u] / len(outs)
                for t in outs:
                    new[t] += share
        diff = sum(abs(new[u] - rank[u]) for u in node_list)
        rank = new
        if diff < tolerance:
            break
    return rank


@dataclass
class LinkScoreReport:
    """Per-page internal link authority for a crawl."""
    scores: dict[str, int] = field(default_factory=dict)        # url -> 0-100
    inlinks: dict[str, int] = field(default_factory=dict)       # internal inbound count
    outlinks: dict[str, int] = field(default_factory=dict)      # internal outbound count
    top_pages: list[tuple[str, int]] = field(default_factory=list)   # highest score first
    lowest_pages: list[tuple[str, int]] = field(default_factory=list)  # lowest score first


def build_link_score_report(
    page_urls: set[str],
    links: list[SiteLink],
    root: str | None = None,
    top_n: int = 15,
) -> LinkScoreReport:
    """Compute Link Score (0-100, normalized so the top page = 100) plus internal
    inlink/outlink counts for every crawled page. `root` is included as a graph
    node (the homepage seeds authority) but only crawled `page_urls` are scored.
    """
    nodes = set(page_urls)
    if root:
        nodes.add(root)
    graph = build_link_graph(links, nodes)
    rank = compute_pagerank(nodes, graph)

    inlinks: dict[str, int] = {u: 0 for u in page_urls}
    outlinks: dict[str, int] = {u: 0 for u in page_urls}
    for src, targets in graph.items():
        if src in outlinks:
            outlinks[src] = len(targets)
        for t in targets:
            if t in inlinks:
                inlinks[t] += 1

    # Normalize to 0-100 across crawled pages (highest = 100), matching SF's
    # relative scale. All-zero (single page / no links) collapses to 0.
    raw = {u: rank.get(u, 0.0) for u in page_urls}
    mx = max(raw.values(), default=0.0)
    scores = {u: (round(v / mx * 100) if mx > 0 else 0) for u, v in raw.items()}

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    lowest = sorted(scores.items(), key=lambda kv: (kv[1], kv[0]))
    return LinkScoreReport(
        scores=scores,
        inlinks=inlinks,
        outlinks=outlinks,
        top_pages=ordered[:top_n],
        lowest_pages=lowest[:top_n],
    )
