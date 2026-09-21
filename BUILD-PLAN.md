# 🏗️ Build Plan - from "basic results" to "advanced results"

> The execution plan. Consolidates [`ROADMAP.md`](ROADMAP.md) (phases),
> [`ARCHITECTURE-AND-UX-PLAN.md`](ARCHITECTURE-AND-UX-PLAN.md) (design), and the
> grounded audit of the real **2,423-URL edstellar** run into concrete,
> ready-to-build tickets. Local planning doc - nothing here is pushed/deployed
> until explicitly approved.

## The gap in one line
**Today:** a flat per-URL summary (score, counts, title/H1/word-count) - "basic."
**Target:** per-issue intelligence - *what · where (exact element + offending
value) · why · how to fix*, full affected-page tables, severity/thematic/priority
triage, whole-crawl analysis (Link Score, orphans, dupes), at reliable scale -
"advanced."

## What "advanced results" means concretely (acceptance definition)
1. Every issue shows the **exact offending value** ("where"): the image `src`,
   the broken target URL + status, the duplicate title text, the bad hreflang.
2. Click an issue → **full filterable table of affected pages** → click a page →
   **detail opens scrolled to + highlighting the exact element**.
3. Issues are **triaged**: Error/Warning/Notice tabs, thematic groups, and a
   **"Fix these first"** priority (severity × page-reach × 1/effort).
4. **Whole-crawl analysis**: Link Score (internal PageRank), orphan pages,
   near-duplicate clusters, redirect maps.
5. **Deeper checks** toward SF's depth (pagination, AMP, security, directives,
   accessibility, structured-data validation, crawl depth).
6. Crawl runs **server-side** (persistent, shareable, resumable, streaming) - not
   tab-bound; UI stays fast at 2,000+ URLs.
7. **Rich exports** (All-Issues, per-tab) + one-click **AI fix**.

---

## The Screaming Frog mental model (what "show where" means)
In SF, **every issue is backed by a real table of the exact offending elements**,
each with its attributes and the page it's on - the rows *are* the answer to
"where." "Missing Alt Text" isn't a number, it's a list of *these images on
these pages*; "Broken Links" is *this anchor on this page → this target (404)*.
Our tool must do the same: **no issue without its offending elements.**

✅ **Backbone proven (this session):** each issue now carries an `affected` list
of the exact elements - verified live: `Missing Alt → /logo.png (no alt)`,
`Generic Alt → /pic.png (alt="image.png")`. The rest of the plan builds the full
catalog + the UI on top of this.

## 🎯 The "Where Catalog" - per-check offending value + jump target
For each check, the result must show *this exact value* and let you jump to it.
This is the concrete definition of "show where correctly."

| Issue / check | The "where" we capture (offending value) | Jump-to target |
|---|---|---|
| Missing / empty / generic alt | image `src` + current alt | highlight the `<img>`; row in Images tab |
| Broken internal / external link | source page + **target URL + HTTP status + anchor text** | the `<a>` in the body; Links tab |
| Weak / duplicate anchor text | the anchor text + its target | the `<a>` |
| Duplicate title / description / H1 | the **exact colliding text + the group of URLs** | the `<title>` / `<meta>` / `<h1>` |
| Title / description too long / short | the text + measured length | the tag |
| Missing / multiple H1 | the H1 text(s) | the `<h1>`(s) |
| Missing / wrong canonical | the canonical value + expected self-URL | `<link rel=canonical>` |
| Noindex / nofollow active | the exact robots directive value | `<meta robots>` / `X-Robots-Tag` |
| Redirect chain / loop | the **hop sequence** url→url→url + statuses | - |
| Thin / low content | word count + content-ratio | the content region |
| Large image (>200KB) | image `src` + size | Images tab |
| Slow response | measured ms | - |
| Mixed content *(new)* | the insecure `http://` asset URL | the asset reference |
| Structured-data invalid *(new)* | schema type + the validation error | the JSON-LD block |
| Missing CTA / Author *(page-specific)* | which required section is absent | the region |

Every row above = a `Finding` with `captures` (the value) + `locator` (how to
highlight it) - populated by that check in the **Check Registry** (M0).

## Milestones (ordered; M0+M1 deliver visible "advanced" fastest)

### 🟢 M0 - Data backbone: Check Registry + rich Finding model  *(enabler)*
Turns "basic" data into "advanced-capable" data. No new UI yet, but everything
after this gets cheap.
- **T0.1** Introduce a **Check Registry** (`modules/checks/registry.py`): each
  check declares `id, category, severity, impact, effort, detect(), captures[],
  columns[], explain, fix(), locator()`. Migrate existing checks into it
  incrementally (start with the top ~15 by frequency in the edstellar run).
- **T0.2** Extend the **Finding** shape to carry `check_id`, `captures` (the
  offending value/"where"), and `locator` (selector/xpath/snippet for
  jump-to-element). Persist it (Neon `findings` + client `AuditResult`).
- **T0.3** **Normalize issue titles** in `lib/aggregate.ts::issuesByTitle`
  (strip trailing "(N …)") so "Missing alt text on 1/2/5 image(s)" aggregate
  into one row with a count distribution. *(Fixes the fragmentation seen in the
  real run.)*
- *Acceptance:* a finding for "missing alt" includes the image `src`; a broken
  link includes target + status; aggregated top-issues are clean.

### 🟢 M1 - Advanced Issue Intelligence UI  *(the headline)*
Built on M0; works on the existing browser-crawl data (no scale change needed
first).
- **T1.1** **Issues workbench**: Error/Warning/Notice tabs + thematic groups +
  **"Fix these first"** priority list. Search + filters (category/folder/status).
- **T1.2** **Affected-pages table per issue** (all pages, not top-8): filterable,
  sortable, with the **offending-value column(s)** per check type; bulk
  select → export/open.
- **T1.3** **Jump-to-element**: clicking an affected page opens `/detail`
  **scrolled to + highlighting** the exact element; buttons: *Open live page
  (deep-linked `#:~:text=`)*, *View source line*.
- **T1.4** **"What / Where / Why / How" panel for every issue** - expand the
  ~20-item KB to full coverage by AI-generating + caching explanations for any
  uncovered `check_id`.
- **T1.5** **All-Issues export** (CSV/Excel): one row per issue -
  URL · issue · severity · category · offending value · recommendation.
- **T1.6** Fix workflow v1: **mark fixed / ignore (hide+restore) / snooze**;
  cross-crawl New·Fixed·Still·Regressed (reuse `crawl_diff`).
- *Acceptance:* from an issue you can reach every affected page and land on the
  exact element, with a full explanation and an export of all instances.

### 🟡 M2 - Scale foundation: server-side crawl + fast UI
- **T2.1** Run `worker/` crawls **server-side** on an always-on host (Railway
  rec.) → persist pages/links/findings to Neon; **live progress** (poll→SSE).
- **T2.2** **Paginate/virtualize** the Results + report tables (fixes the
  all-2,423-rows render).
- **T2.3** Shareable/persistent crawls (not tab-bound); resume.
- *Acceptance:* a 2,000+ crawl runs without the tab open, is shareable, and the
  Results page stays fast.

### 🟡 M3 - Advanced analysis + deeper checks
- **T3.1** **Crawl Analysis pass**: **Link Score** (internal PageRank over the
  link graph), **orphan pages**, **near-duplicate clusters**, redirect map.
- **T3.2** New checks toward SF depth: **pagination (rel next/prev), AMP,
  Security (mixed content/HSTS), Directives granularity, structured-data
  validation (Rich Results), Accessibility (axe), crawl depth**.
- **T3.3** Site Health with **trend over time** + thematic scores.

### 🔵 M4 - AI power (differentiator)
- **T4.1** One-click **AI fix → diff → open GitHub PR**.
- **T4.2** **Ask-your-crawl** (NL query over the crawl DB).
- **T4.3** Bulk AI rewrites (titles/metas/alt).

### 🔵 M5 - Integrations, reporting, monitoring
- **T5.1** **GSC + GA4** joins (traffic-weighted issues, real orphan detection).
- **T5.2** **White-label PDF** + Google Sheets export + shareable report links.
- **T5.3** **Visualizations** (crawl tree, force-directed link graph).
- **T5.4** **Monitoring**: scheduled re-crawls + regression alerts.

---

## Suggested first sprint (all local, no deploy)
1. **M0** - Check Registry + Finding `captures`/`locator` for the **top 15
   edstellar issues** + title normalization (T0.1–T0.3).
2. **M1** - Issues workbench + affected-pages table + **jump-to-element** for
   those 15 (T1.1–T1.3), plus All-Issues export (T1.5).

That alone converts the current "basic" report into an **advanced, actionable**
one on your existing data - before we invest in server-side scale (M2).

## Effort snapshot (rough)
| Milestone | Effort | Delivers |
|---|---|---|
| M0 | S–M | advanced-capable data (offending value + locator) |
| M1 | M–L | the advanced issue UX (the headline) |
| M2 | M | reliable scale + fast UI |
| M3 | L | Link Score, orphans, deeper checks |
| M4 | M | AI fix→PR, ask-your-crawl |
| M5 | L | integrations, reports, monitoring |
