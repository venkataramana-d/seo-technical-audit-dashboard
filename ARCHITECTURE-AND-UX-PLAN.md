# 🧠 Architecture, Modules & UX Plan (ideas + design)

> Companion to [`ROADMAP.md`](ROADMAP.md). This doc is the "how we build it well"
> layer: new product paths, the logical architecture, a module decomposition,
> and a full UI/UX plan. Local planning doc - not a commitment, a menu to choose
> from.

---

## 0. The one idea everything hangs on - the Check Registry

A **single source of truth** for every check, so detection, grid columns, issue
drill-down, explanations, scoring, and AI-fixes stay consistent and extensible.

```py
# modules/checks/registry.py  (each check is a plugin)
Check(
  id="duplicate_title",
  category="On-Page", severity="warning", impact=6, effort="easy",
  detect=fn(page, crawl) -> list[Finding],      # returns affected pages
  captures=["title_text", "colliding_urls"],     # the "WHERE" (offending value)
  columns=[Col("Title", "title_text"), Col("Also on", "colliding_urls")],
  explain=Explanation(what=..., why=..., how=..., example_html=...),
  fix=fn(page, finding) -> Patch | None,         # AI/deterministic fix
  locator=fn(page, finding) -> ElementRef,       # for jump-to-element
)
```

Add a check once → it shows up in the crawl, the issues list, the affected-URL
table (with the right columns), the drill-down explanation, the score, and the
one-click fix. No other place needs editing. **This is the backbone.**

---

## 1. New paths / product directions (pick a north star)

We're currently an "audit tool." Bigger positioning options - not mutually
exclusive, but they change what we optimize for:

1. **AI-native SEO copilot** *(recommended headline)* - audit → **explain →
   fix → open a PR**. "Don't just tell me, fix it." Ask-your-crawl in natural
   language. This is the moat SF/SEMrush don't have.
2. **Continuous SEO monitoring platform** - scheduled re-crawls, regression
   alerts (score drop, new 5xx, noindex added), SSL/robots/uptime watch. Turns a
   one-shot tool into a always-on service (recurring value).
3. **Agency / multi-client platform** - workspaces per client, white-label
   PDF/branded reports, shareable links, roles. Monetizable.
4. **Dev-integrated SEO (shift-left)** - a **GitHub App** that audits preview
   deploys and **comments SEO issues on PRs**, a pre-deploy gate, CI checks.
   Unique dev-first angle.
5. **Content & on-page optimization** - beyond technical: content briefs,
   intent, internal-link recommendations, AI rewrites.
6. **AI-search readiness (GEO/AEO)** - llms.txt, semantic HTML, structured data
   for LLM answer engines. Timely; SEMrush already added "AI Search Health."

> Suggestion: **north star = #1 (AI copilot)**, with **#2 (monitoring)** as the
> retention engine and **#4 (GitHub App)** as the wedge that fits our stack.

---

## 2. Logical architecture (how it works, end to end)

Six layers, each swappable:

```
 Ingestion ─▶ Processing ─▶ Storage ─▶ Intelligence ─▶ Presentation ─▶ Delivery
  (crawl)     (checks +      (Neon)     (AI: explain,     (Next.js UI)   (reports,
              analysis)                  fix, ask)                        API, alerts)
```

**Crawl pipeline (per URL):**
`discover → fetch → (render JS?) → parse → run Check Registry → persist page/
links/findings → stream progress`
then once the crawl finishes:
`Crawl Analysis pass → link graph → Link Score, orphans, near-dup clusters,
duplicate groups, redirect maps → site health score → notify`

**Orchestration:** a durable **job queue** (already in `worker/`) on an
always-on worker; jobs = `crawl.start`, `crawl.analyze`, `page.audit`,
`ai.fix`, `report.generate`, `schedule.tick`. Progress streamed to the UI
(poll → later SSE/WebSocket).

**Data model (evolve current):**
`Project → Crawl → Page → {Link, Finding}` + `IssueType` registry table +
`Segment`, `FixTask`, `Alert`, `IntegrationAccount (GSC/GA)`. A **Finding** row
carries `check_id`, `page_id`, and a JSON `captures` blob (the offending value)
+ a `locator` (how to jump to the element) + `status` (new/fixed/ignored).

**Key logical rules (from the benchmark research):**
- Severity = Error / Warning / Notice; **score weights Errors > Warnings,
  Notices excluded**; frequency-weighted (fixing all instances of one check
  moves the needle most).
- Issues are **issue-centric** ("N pages have X"), page list is the drill-down.
- "Fixed" is **detected by re-crawl** (diff against previous), not a manual flag.

---

## 3. Module decomposition

### Backend (`modules/`, `worker/`, `api/`)
| Module | Responsibility |
|---|---|
| `crawler` | BFS discovery, frontier, scope/robots, rate limit, wall-clock budget |
| `fetcher` / `renderer` | HTTP fetch (SSRF-guarded) + optional Playwright render |
| `checks/registry` + `checks/*` | **the Check Registry** - one plugin per check |
| `scoring` | severity weighting, frequency-weighted health score, trends |
| `linkgraph` | build internal graph → **Link Score** (PageRank), orphans |
| `dedup` | exact + near-duplicate clustering (embeddings optional) |
| `explain` | KB + AI-generated what/where/why/how, cached per check_id |
| `fix` | deterministic + AI patch generation; GitHub PR opener |
| `integrations` | GSC, GA4, Google Sheets, Slack, Jira/Zapier |
| `export` | CSV/Excel/PDF/JSON + white-label report builder |
| `notifier` | alerts (regression, SSL, robots), digests |
| `scheduler` | recurring crawls (exists) |

### Frontend (`app/`, `components/`, `lib/`)
| Module | Responsibility |
|---|---|
| `crawl-runner` | new-crawl wizard, live progress, mode switch |
| `results-grid` | multi-tab sortable/filterable workbench + saved views |
| `issue-intelligence` | issue list → affected-URLs → **jump-to-element** |
| `fix-workflow` | AI fix modal, diff, PR button, mark-fixed/ignore/send-to-task |
| `dashboards` | overview, trends, deltas |
| `viz` | crawl tree, force-directed graph, depth distribution |
| `reports` | export + white-label + shareable links |
| `ask` | natural-language "ask-your-crawl" panel |
| `settings/admin` | projects, integrations, roles, segments |
| `lib/registry` | client mirror of check metadata (columns, labels, severity) |

---

## 4. UI/UX - total plan

### 4.1 Information architecture (navigation)
```
Dashboard · Projects ─▶ Project ─▶ Crawls ─▶ Crawl
                                              ├─ Overview (health, deltas, top issues)
                                              ├─ Issues  ⭐ (workbench)
                                              ├─ Pages   (the data grid, ~tabs)
                                              ├─ Structure (viz: tree / graph / depth)
                                              ├─ Compare (crawl diff)
                                              └─ Reports / Export
Ask (NL query)  ·  Monitoring/Alerts  ·  Settings  ·  Admin
```

### 4.2 The core screens & flows

**(a) New Crawl wizard** - mode (Spider/List/Sitemap), URL, scope
(include/exclude, subdomains), depth/limits, JS-render toggle, integrations,
schedule. Sensible defaults; "advanced" collapsed.

**(b) Live crawl** - progress bar, URLs/sec, counts filling in **real time**,
"pause/resume", partial results usable mid-crawl.

**(c) Crawl Overview** - Site Health gauge **+ delta vs last crawl**, E/W/N
counters with trend arrows, crawled-pages breakdown, thematic cards
(Crawlability/Content/Links/Technical/…), **Top Issues** ("fix these first").

**(d) Issues workbench ⭐ (the heart - your priority)**
- Left: category/severity filters + New·Fixed·Still·Regressed.
- Center: **issue rows** "N pages have <problem>" + severity chip + **trend
  sparkline** + impact.
- Click a row → **affected-URLs table** with the **offending value column(s)**
  (broken target+status, colliding title text, image src…).
- Click a URL → **page detail opens with the exact element highlighted +
  auto-scrolled**; buttons: *Open live page (deep-linked)*, *View source line*,
  *AI Fix*.
- Right rail: **"What / Where / Why / How"** panel + example + **[Fix with AI]**,
  [Mark fixed] [Ignore] [Send to Jira/Slack].

**(e) Pages grid** - Screaming-Frog-style analyst workbench: pick a tab (Titles,
Meta, Response Codes, Canonicals, Links…), sortable/filterable columns, saved
views, bulk select → export/action, per-row drill to page detail.

**(f) Page detail** - tabs (Overview/Content/Links/Headings/Images/Perf/Source/
Rendered/Headers) with issues for that page highlighted in place.

**(g) Structure viz** - crawl-tree, force-directed link graph (node size = Link
Score / inlinks), depth distribution.

**(h) Compare crawls** - Fixed / New / Regressed columns + progress line graph.

**(i) Ask-your-crawl** - a search/chat box: "pages with thin content and no
internal links" → answered table.

### 4.3 Design system & interaction
- **Tokens** (we have `--seo-*`, light/dark) - extend with a **severity palette**
  (Error/Warning/Notice) used everywhere consistently.
- **Grid workbench patterns:** sticky header, column chooser, saved views,
  virtualized rows (for big crawls), inline sparklines, delta badges.
- **Jump-to-element:** highlight overlay + smooth scroll; `#:~:text=` deep link
  to the live page; source-line highlight.
- **Command palette** (⌘K): jump to any project/crawl/issue/page.
- **States:** first-class empty / loading / streaming / error states everywhere.
- **Responsive + a11y + dark mode** (already strong) - keep as a rule, not an
  afterthought; the tool itself should pass its own Accessibility checks.
- **Micro-UX:** priority badges, "since last crawl" deltas, keyboard nav in the
  grid, optimistic fix actions.

### 4.4 Progressive disclosure principle
Default view = **decisions** (health, top issues, "fix these first"). Power view
= **the grid** (every URL, every column). Both read the same data; users graduate
from summary → workbench as needed. This is how we beat SF's intimidating grid
*and* SEMrush's shallow-but-pretty report at the same time.

---

## 5. How this maps to the phased roadmap
- The **Check Registry (§0)** is a P0/P1 refactor that makes P1–P5 cheap.
- **Issues workbench (§4.2d)** = ROADMAP P1.
- **Pages grid + viz (§4.2e,g)** = ROADMAP P5.
- **AI fix/PR + Ask (§4.2i, fix-workflow)** = ROADMAP P3.
- **Monitoring/Compare (§4.2h)** = ROADMAP P6 / already have `crawl_diff`.

---

## 6. Suggested build order (revised with this design)
1. **Check Registry refactor** - reshape existing checks into the plugin format
   (unlocks everything cleanly).
2. **P0 scalable crawl** on an always-on worker → Neon → live progress.
3. **Issues workbench** with jump-to-element for the top ~10 checks.
4. **AI explanation coverage** (fill what/where/why/how for every check).
5. Then layer AI-fix→PR, integrations (GSC/GA), grid + viz, monitoring.
