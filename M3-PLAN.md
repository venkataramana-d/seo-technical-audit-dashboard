# 🟡 M3 — Advanced whole-crawl analysis + deeper checks

> Runs on the persisted crawl graph in Neon. **No paid infra needed** — the
> analysis runs at crawl finalize / on-demand `api/analyze.py`, which the current
> free browser-orchestrated Site Crawls already trigger. Companion to
> [BUILD-PLAN.md](BUILD-PLAN.md).

## What already exists (don't rebuild)
Research found most M3 primitives are already built as pure modules and rendered
in the **Site-wide** / **Compare** tabs: crawl **depth** (graph-derived, with a
depth chart + deepest-pages + excessive-depth issue), **orphan/unreachable
pages**, **exact-duplicate** clusters, **redirect** loops/chains, **broken
internal links**, **hreflang** reciprocity, score **trend over time**, and
crawl-to-crawl **compare**. `modules/near_duplicate.py` (MinHash/LSH) and
`modules/directives.py` are complete but **not wired in**.

## The real gaps → build order

### Tier A — cheap wins (data already persisted; compute + display)
1. **Link Score (internal PageRank)** — the headline M3 metric; nowhere today.
   New `modules/link_score.py` (iterative PageRank over the existing internal
   link graph) + an `api/analyze.py` action + Site-wide UI. No schema change.
2. **Wire `modules/directives.py`** — complete but orphaned; import into the
   per-page audit for granular meta-robots / X-Robots directive checks.
3. **Thematic scores per crawl** — derive per-theme numeric scores from existing
   Issue rows (display now; per-theme trend needs Tier B).

### Tier B — needs a migration (persist for server-side sort/trend)
4. Persist Link Score + inlink/outlink counts on `Page` (sortable Pages grid).
5. Persist per-crawl thematic scores (theme-level trend lines).
6. Near-duplicate at scale: `Page.content_signature_json`, switch
   `_handle_near_duplicates` to `near_duplicate_from_signatures`.

### Tier C — needs crawler changes / new engine
7. Per-page crawl depth captured at crawl time (`Page.depth`) — today depth is
   graph-derived on demand, which is fine for M3 display.
8. Accessibility (axe/WCAG) — genuinely new engine; largest build.

## This iteration
Tier A #1 (Link Score) end-to-end + Tier A #2 (wire directives). Both are pure,
tested, need no migration, and populate on the free browser crawls.
