"""Site-structure / crawl-depth graph - 02-AUDIT-ENGINE.md §2 and the Crawl Path
report from 08-SCREAMING-FROG-TECHNICAL-REFERENCE.md / 00-PLAN Phase 4.5.

Builds a directed graph from the crawl's internal links and reports:
  - clicks-from-home for every page (true shortest path via BFS, not the
    crawler's discovery order - a page can be discovered deep but actually be
    one click from the homepage),
  - max depth, pages per depth level, average depth,
  - pages unreachable from the homepage by internal links,
  - the shortest click-path from the homepage to any URL (Crawl Path report),
  - an actionable finding for pages buried deeper than a recommended click depth.

Pure module - operates on sitewide.SiteLink records + the set of crawled page
URLs, no DB. The crawl-finalization glue supplies the normalized root URL.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from modules.sitewide import SiteIssue, SiteLink

DEFAULT_MAX_RECOMMENDED_DEPTH = 3  # Semrush flags pages > 3 clicks from home


def build_link_graph(links: list[SiteLink], nodes: set[str] | None = None) -> dict[str, set[str]]:
    """Adjacency map of internal links. If `nodes` is given, only edges whose
    target is a known crawled page are kept (links to uncrawled/off-scope URLs
    don't contribute to internal click-depth)."""
    graph: dict[str, set[str]] = {}
    for lk in links:
        if lk.link_type != "internal":
            continue
        if nodes is not None and lk.target_url not in nodes:
            continue
        graph.setdefault(lk.source_url, set()).add(lk.target_url)
    return graph


def bfs_depths(root: str, graph: dict[str, set[str]]) -> dict[str, int]:
    """Shortest click-depth from `root` to every reachable node (root = 0)."""
    depths: dict[str, int] = {root: 0}
    queue: deque[str] = deque([root])
    while queue:
        node = queue.popleft()
        for nbr in graph.get(node, ()):  # deterministic order not required for depth
            if nbr not in depths:
                depths[nbr] = depths[node] + 1
                queue.append(nbr)
    return depths


def shortest_click_path(root: str, target: str, graph: dict[str, set[str]]) -> list[str] | None:
    """The shortest homepage->target click path as a list of URLs (inclusive of
    both ends), or None if the target is unreachable from root. Neighbours are
    visited in sorted order so the returned path is deterministic."""
    if target == root:
        return [root]
    parent: dict[str, str] = {root: root}
    queue: deque[str] = deque([root])
    while queue:
        node = queue.popleft()
        for nbr in sorted(graph.get(node, ())):
            if nbr not in parent:
                parent[nbr] = node
                if nbr == target:
                    path = [nbr]
                    while path[-1] != root:
                        path.append(parent[path[-1]])
                    return list(reversed(path))
                queue.append(nbr)
    return None


@dataclass
class CrawlDepthReport:
    root: str
    max_depth: int = 0
    pages_per_depth: dict[int, int] = field(default_factory=dict)
    avg_depth: float = 0.0
    reachable_count: int = 0
    unreachable_urls: list[str] = field(default_factory=list)
    deepest_pages: list[tuple[str, int]] = field(default_factory=list)  # (url, depth), deepest first


def build_depth_report(
    root: str,
    page_urls: set[str],
    links: list[SiteLink],
    top_n: int = 10,
) -> CrawlDepthReport:
    """Compute the crawl-depth analytics for a crawl. `page_urls` are the
    normalized URLs of crawled pages; `root` is the normalized homepage."""
    nodes = set(page_urls) | {root}
    graph = build_link_graph(links, nodes)
    depths = bfs_depths(root, graph)
    # Only count actual crawled pages (the root may or may not be a crawled row).
    reachable = {u: d for u, d in depths.items() if u in page_urls}

    pages_per_depth: dict[int, int] = {}
    for d in reachable.values():
        pages_per_depth[d] = pages_per_depth.get(d, 0) + 1

    unreachable = sorted(page_urls - set(reachable))
    deepest = sorted(reachable.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]

    return CrawlDepthReport(
        root=root,
        max_depth=max(reachable.values(), default=0),
        pages_per_depth=dict(sorted(pages_per_depth.items())),
        avg_depth=round(sum(reachable.values()) / len(reachable), 2) if reachable else 0.0,
        reachable_count=len(reachable),
        unreachable_urls=unreachable,
        deepest_pages=deepest,
    )


def excessive_depth_issues(
    report: CrawlDepthReport,
    links: list[SiteLink],
    page_urls: set[str],
    max_recommended_depth: int = DEFAULT_MAX_RECOMMENDED_DEPTH,
) -> list[SiteIssue]:
    """One aggregate finding for pages buried deeper than the recommended click
    depth from the homepage. Recomputes depths to enumerate the offenders."""
    nodes = set(page_urls) | {report.root}
    graph = build_link_graph(links, nodes)
    depths = bfs_depths(report.root, graph)
    too_deep = sorted(
        u for u, d in depths.items() if u in page_urls and d > max_recommended_depth
    )
    if not too_deep:
        return []
    return [SiteIssue(
        issue_type="page_excessive_crawl_depth", category="Internal Links", severity="notice",
        impact_score=3, effort_level="medium",
        what=(f"{len(too_deep)} page(s) are more than {max_recommended_depth} clicks from the "
              "homepage."),
        why=("Pages buried deep in the site get less crawl priority and pass less internal link "
             "equity, so they tend to rank worse than shallower pages."),
        root_cause=("The internal linking/navigation requires many clicks to reach these pages "
                    "from the homepage."),
        fix=("Add internal links (navigation, hubs, related-content modules) so important pages "
             f"sit within {max_recommended_depth} clicks of the homepage."),
        affected_urls=too_deep,
    )]
