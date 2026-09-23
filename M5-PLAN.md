# 🔵 M5 — Integrations, reporting & monitoring

> Companion to [BUILD-PLAN.md](BUILD-PLAN.md). Constraint: no always-on worker
> (Railway skipped); crawls are browser-orchestrated + persisted to Neon.

## Build order (research-driven)

### Tier 1 — buildable now, no infra, no new dependency
1. **T5.3 Internal link-graph visualization** — ✅ **DONE.** New `link-graph`
   action in `api/analyze.py` returns the top-60 pages by Link Score + the
   internal edges among them (from persisted `Page.depth/link_score/inlinks/
   outlinks`). `components/detail/CrawlGraph.tsx` renders a dependency-free SVG
   node-link graph laid out in columns by crawl depth, nodes sized/colored by
   Link Score, in the Site-wide tab. (A force-directed layout would need a new
   frontend dep — the depth-layered "crawl tree" gives the same insight without one.)
2. **T5.2 Site-crawl PDF/Excel export** — the generators exist
   (`report_generator.py`); needs an export path reading crawl rows from Neon.
3. **T5.2 Shareable report links** — add a `Crawl.share_token` (+migration), a
   public token-gated read endpoint bypassing org-scope, and a public route in
   AppShell.
4. **T5.4 (scheduling half)** — a Vercel Cron hitting an enqueue endpoint can
   FIRE due schedules, but see the blocker below.

### Needs a new frontend dependency
5. **T5.3 force-directed graph** — `d3-force`/`react-force-graph` (or a hand-rolled
   SVG force sim) for a physics layout, on top of the `link-graph` endpoint.

### Blocked / needs infra
6. **T5.4 scheduled re-crawls + regression alerts** — the scheduler
   (`enqueue_due_crawls`), diff engine (`compare_crawls`) and email
   (`email_send`) all exist, but unattended crawls have **no executor** without
   the always-on worker (browser-orchestrated only). A cron can enqueue; running
   the crawl needs the worker (skipped) or a server-side executor.
7. **T5.1 GSC + GA4 joins** / Google Sheets export — need user-provisioned Google
   Cloud + OAuth 2.0 (consent screen, refresh tokens) and a vault schema for
   OAuth token sets. Highest external cost; user-infra heavy.

## Status
T5.3 shipped. T5.2 (crawl export + share links) is the next no-infra work.
T5.4/T5.1 need the worker / Google OAuth respectively.
