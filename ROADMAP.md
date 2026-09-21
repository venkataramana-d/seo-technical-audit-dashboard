# 🗺️ Product Roadmap - toward a Screaming-Frog / SEMrush-class audit tool

**Goal:** turn our per-URL / small-crawl auditor into a tool that produces
**correct, complete, and *actionable* results** at real site scale - where every
issue tells you **what** is wrong, **where** exactly (deep-link to the page and
the offending element), **why** it matters, and **how** to fix it (ideally
one-click).

This doc benchmarks against **Screaming Frog SEO Spider** (data depth + crawl
scale) and **SEMrush Site Audit** (issue presentation + fix workflow), maps our
current baseline, and lays out a phased plan. Companion docs:
[`README.md`](README.md), [`agents.md`](agents.md),
[`SEMRUSH-AHREFS-TECHNICAL-REFERENCE.md`](SEMRUSH-AHREFS-TECHNICAL-REFERENCE.md).

---

## 1. How the benchmarks work (condensed)

### Screaming Frog SEO Spider
- **Desktop app** using local hardware. BFS crawl (like Googlebot); raw HTML by
  default, optional **headless Chromium** for JS.
- **Scale:** RAM mode (~10k–150k URLs) or **disk/database mode** (millions).
- **Live streaming grid:** ~25 tabs (Internal, Response Codes, Titles, Meta,
  H1/H2, Content, Images, Canonicals, Pagination, Directives, Hreflang,
  Structured Data, Links, AMP, Security, Accessibility, PageSpeed…), each
  sortable/filterable, with a per-URL lower pane (inlinks, outlinks, rendered
  screenshot, headers, view-source).
- **"Crawl Analysis"** = a distinct post-crawl step that computes whole-graph
  metrics: **Link Score** (internal PageRank), **orphan pages**, near-duplicates.
- **Outputs:** ~300 issue checks, reports, bulk exports, XML sitemap generation,
  visualizations (crawl tree, force-directed graph), scheduling, CLI/headless.

### SEMrush Site Audit - the issue-presentation model (what we mirror)
- **Overview:** Site Health 0–100 (Errors weigh > Warnings; Notices don't count;
  score is relative to *checks*, and **fixing all instances of one check lifts
  it most** - frequency-weighted), Errors/Warnings/Notices counters with trend,
  crawled-pages breakdown, thematic report cards, **Top Issues** (priority ×
  affected-page count).
- **Issue list is issue-centric:** one row per issue type phrased as **"N pages
  have <problem>"**, with an **inline trend sparkline**, grouped under
  Error/Warning/Notice tabs, searchable + advanced filters.
- **"Why and how to fix it" panel** per issue: **what it is → why it matters →
  how to fix**, plus Share / Learn-more.
- **Drill-down → per-URL table** listing every affected page, and crucially
  **the actual offending value per row** (broken target URL + status code;
  colliding duplicate titles; the specific hreflang/markup value). Columns adapt
  per check type.
- **Workflow:** Hide/Restore (ignore an issue or page - also removes it from the
  score), **Send to Trello / Zapier (Jira/Asana/…)**. "Fixed" is detected
  automatically by **re-crawl** (Compare Crawls shows Fixed/New columns +
  a Progress graph).
- **~140+ checks**; signature checks: crawl depth >3, orphan pages, hreflang
  conflicts, invalid structured data, redirect chains/loops, mixed content,
  duplicate titles/content, 4xx/5xx, DNS/SSL.

---

## 2. Our baseline today (from the code)

**Have (🟢):** per-URL audit (metadata, headings tree, canonical, indexability,
URL structure, content, images/alt, redirects, advanced = SERP/social/schema/
mobile/hreflang/Twitter/favicon/charset, site health = WHOIS/SSL/DNS/robots/
sitemap/readability/freshness/HTTP2/canonical-loop/www, internal+external links,
course/blog audits, PageSpeed); 35-item checklist; **custom extraction
(CSS/XPath/regex)**; optional **JS rendering (Playwright)**; BFS crawler with
robots modes + wall-clock guard; crawl persistence to Neon; site-wide dup/orphan/
broken-link/redirect/near-duplicate + **crawl diff**; 0–100 weighted score with
Error/Warning/Notice + impact/effort; CSV/Excel/PDF/JSON export; a curated ~20-
issue explanation KB (what/why/SEO impact/user impact/fix) + AI fix suggestions.

**Partial (🟡):** issue explanations (only ~20 curated types); Link Score (only a
crawl graph, no score); orphans (sitemap-only); structured data (detect, not
validate); crawl scheduling (in `worker/`, not wired); analyst grid (results
list + detail tabs, not a full multi-tab grid).

**Missing (🔴):** scalable server-side crawl (currently browser-driven, ~5k cap,
tab must stay open); real-time streaming; **issue → affected-URLs list**;
**jump-to-exact-element**; pagination/AMP/Security/Directives-tab/Accessibility/
spelling checks; GSC/GA4 integration; visualizations; XML sitemap generation;
fix workflow (mark-fixed/ignore/send-to-task).

---

## 3. ⭐ Issue Intelligence & Navigation (the priority workstream)

This is the "make issues actionable" requirement - modeled on SEMrush's
drill-down and better where we can be. Target UX:

**3.1 Issue-centric list** (per crawl)
- One row per issue type: **"N pages have <problem>"** + severity chip +
  **trend sparkline** across crawls + impact score.
- Grouped by Error / Warning / Notice; searchable; filter by category / folder /
  New·Still-present·Fixed·Regressed.
- **Top Issues / "Fix these first"** ranked by severity × affected-page count ×
  (1/effort).

**3.2 Click an issue → affected-URLs table**
- One row per affected page, with **the actual offending value shown** (columns
  adapt per check): e.g. broken link → source page + broken target + HTTP status;
  duplicate title → the colliding title text + the group of URLs; missing alt →
  the image `src`; redirect chain → the hop sequence.
- Sortable/filterable; bulk select → export / open / send-to-task.

**3.3 Click an affected URL → jump to the exact problem**
- Open that URL's detail with the relevant tab active **and the offending
  element highlighted + auto-scrolled into view** (the `<title>`, the broken
  anchor in the body preview, the alt-less `<img>`, the meta tag).
- **"Open live page"** deep-linked to the element (`#:~:text=` fragment where
  possible) in a new tab; **"View source line"** (raw + rendered HTML) with the
  line highlighted; **"View rendered element."**

**3.4 "What / Where / Why / How" panel for _every_ issue**
- Upgrade the curated ~20-item KB to **full coverage**: for any issue type,
  show **what it is → where (the element/value) → why it matters (SEO + user
  impact) → how to fix**, with a **copy-paste code example** and a
  **before → after**. Fill gaps with the AI layer (Groq/Anthropic) and cache.

**3.5 Fix workflow**
- **One-click AI fix** → generate corrected HTML/meta, show a diff. ➜ optional
  **"Open a GitHub PR"** with the change (headline differentiator).
- **Mark fixed** (verified on next re-crawl), **Ignore/Hide + Restore** (removes
  from score like SEMrush), **Snooze**.
- **Send to Jira / Trello / Slack / webhook** with a back-link.
- **Cross-crawl diff** surfaced here: Fixed / New / Still-present / Regressed.

---

## 4. Expanded feature backlog (more options / functionality)

**Crawl & scale:** server-side engine (no tab/timeout) + live streaming;
Spider / List / Sitemap / Compare / re-crawl-changed modes; **segments**
(folder/type/regex); saved crawl configs; scheduled recurring crawls;
per-crawl JS-render toggle; rate-limit/user-agent/robots controls.

**Checks / data depth (toward ~300):** Pagination (rel next/prev), AMP,
**Security** (mixed content/HSTS/insecure forms), **Directives** granularity,
**structured-data validation** (Google Rich Results), **Accessibility (axe/
WCAG)**, spelling/grammar, n-grams, response-header & cookie views, **rendered
screenshot + view-source (raw vs rendered)**, crawl depth, **Link Score
(internal PageRank)**, orphan pages.

**AI (our differentiator):** one-click fix → **GitHub PR**; **ask-your-crawl**
(natural-language queries over the crawl DB); bulk AI title/meta/alt rewrites;
AI content-gap & intent; AI executive summary per crawl.

**Integrations:** **Google Search Console** (clicks/impressions/position + URL
Inspection) & **GA4** → real orphan detection + "issues on your highest-traffic
pages first"; **Google Sheets / Looker** export; Slack/Teams alerts;
Jira/Trello/Asana + Zapier/webhooks; optional Moz/Majestic backlinks.

**Analysis & insight:** Site Health **trend over time**; crawl comparison/change
detection (surface `crawl_diff`); **visualizations** (crawl tree, force-directed
link graph, depth distribution); duplicate/near-duplicate clustering;
internal-link opportunities; **competitor side-by-side audit**.

**Reporting & collaboration:** white-label **PDF** reports + scheduled delivery;
shareable read-only report links; multi-workspace / client projects; richer
roles/permissions; per-issue comments/assignments; audit history timeline.

**UX / workbench:** analyst grid (multi-tab, sortable/filterable, saved views,
bulk actions); command palette; overview dashboard with deltas; keyboard nav.

**Monitoring & automation (SaaS advantage over desktop SF):** continuous
re-crawl + **regression alerts** (new 5xx, score drop, noindex added, new broken
link); SSL-expiry / robots-change / uptime alerts; **deploy-hook auto-audit**;
public API; CLI/headless.

---

## 5. Where we win vs SF / SEMrush

| Differentiator | Why it wins |
|---|---|
| **AI fix → GitHub PR** | They *tell* you; we *fix* it |
| **Ask-your-crawl (NL query)** | Analyst answers without learning filters |
| **Jump-to-exact-element** | Faster triage than a spreadsheet grid |
| **Continuous monitoring + alerts** | Desktop SF can't; SaaS advantage |
| **Client-ready white-label reports** | Agencies pay for this |

---

## 6. Phased roadmap

Ordered so each phase unlocks the next; **P0 + P1 deliver the core of your ask.**

| Phase | Theme | Delivers | Depends on |
|---|---|---|---|
| **P0** | **Scalable crawl engine** | Server-side BFS (no tab/timeout) persisting every page/link/issue to Neon, with live progress. Full-site crawls of 10k+ URLs. | Crawl-host decision (§7) |
| **P1** | **Issue Intelligence & Navigation** (§3) | Issue-centric list → affected-URL table (offending value) → **jump-to-element**; full what/where/why/how coverage; fix workflow (mark-fixed / ignore / send-to-task); cross-crawl Fixed/New. | P0 |
| **P2** | **Data completeness** | Pagination, AMP, Security, Directives, structured-data validation, Accessibility, headers/cookies, rendered screenshot, view-source, **Link Score**, orphans. | P0 |
| **P3** | **AI power features** | One-click fix → **GitHub PR**; ask-your-crawl; bulk rewrites. | P1 |
| **P4** | **Integrations** | GSC + GA4 joins (traffic-weighted issues, real orphans); Google Sheets export; Slack/Jira. | P0/P2 |
| **P5** | **Analyst workbench + visualizations** | Multi-tab grid, saved views, crawl-tree / force-directed graphs, XML sitemap generator. | P0/P2 |
| **P6** | **Monitoring & automation** | Scheduled crawls, regression alerts, white-label reports, public API. | P0 |

---

## 7. The one decision to unblock P0 - where the crawler runs

Vercel functions cap at 60–90s and can't run a long crawl. Options (cheapest → most robust):

1. **GitHub Actions batch runner** - free, no timeout, repo already endorses it;
   great for on-demand / scheduled large crawls. No always-on server, no live stream.
2. **Always-on worker** (Railway / Render / Fly ≈ free–$5/mo) - enables
   real-time streaming + scheduled crawls. **Recommended.**
3. **Oracle Cloud Free VM** - truly free 24/7, more setup.

**Recommendation:** P0 on an **always-on worker (Railway)** for real-time
crawling, with **GitHub Actions** as the fallback for very large batch crawls.

---

## 8. Suggested first sprint

1. **Decide the crawl host** (Railway recommended).
2. **P0:** wire `worker/` to run server-side crawls end-to-end → Neon, with a
   live-progress UI (poll or SSE).
3. **P1 slice:** issue-centric list → affected-URLs → **jump-to-element** for the
   top 10 highest-value checks (broken links, duplicate titles/meta, missing
   H1, noindex, redirect chains, missing alt, thin content, mixed content).
4. **AI coverage:** auto-generate the what/where/why/how for any issue type not
   in the curated KB, and cache it.

This gets you a real full-site crawl with click-through-to-the-exact-problem
issues - the heart of the "correct, detailed, actionable results" goal - before
layering on data depth, AI-PR fixes, integrations, and the analyst workbench.
