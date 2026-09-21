# agents.md

## Project overview
SEO Technical Audit Dashboard: Next.js (App Router, TypeScript, Tailwind)
frontend with Python serverless functions (Vercel-style `api/*.py`) as the
audit engine. Merged from
[venkataramana-d/seo-technical-audit-dashboard-main](https://github.com/venkataramana-d/seo-technical-audit-dashboard-main)
(base) with the site-health checks and Groq AI summary from this project's
prior standalone Streamlit SEO audit tool ported in on top.

## Stack
- Frontend: Next.js 16, React 19, Tailwind CSS 4, Recharts
- Backend: Python 3, plain `http.server.BaseHTTPRequestHandler` handlers under
  `api/` (Vercel Python runtime convention: each file exports a `handler`
  class), no framework
- No database: all audit results are computed on-demand per request and
  persisted client-side only (IndexedDB via `lib/state/AuditContext.tsx` +
  `lib/state/idbStore.ts`; see the gotcha below on why it's IndexedDB and not
  localStorage)

## Key directories/files
- `app/`: Next.js pages: dashboard (`/`), **`technical-audit`** (single URL,
  sitemap, crawl, or CSV-paste multi-input audit, formerly `new-audit`),
  results (flat list with a **Type column**), detail (per-URL drill-down), settings.
  (Session 25 removed the old collapsible domain/section hierarchy; the Results
  table is now flat, sorted, with a Type column and Type filter - the category
  comes from `lib/pageCategory.ts::categorizeUrl(url, audit_type)`, keyed off the
  URL path segment (Course/Blog/Topic/Category/Tag/Type/Static/Home) with the
  backend `audit_type` as a fallback. Session 26 merged the CHECKLIST and TOP
  ISSUE columns into one cell and made the URL column show the **full URL**
  (`break-all`); it also added a `.full-bleed` viewport-width utility to make
  the table full-width, later removed (a later session) because `100vw`/`50vw`
  don't account for AppShell's fixed-width sidebar, so the table rendered
  wider than the actual content column and caused page-level horizontal
  scroll. The table now just sits in the normal (wide) content column with
  its own `overflow-x-auto`.)
  The former standalone `links`, `headings`, and `performance` pages are folded
  into `app/detail/page.tsx` as tabs backed by `components/detail/LinksView.tsx`,
  `HeadingsView.tsx`, and `PerformanceView.tsx`. Report export is NOT a page: it
  is an action bar (`components/ExportBar.tsx`) on the Results page. CSV/JSON
  are generated entirely client-side (`lib/reportExport.ts`), no network call,
  no size limit; Excel/PDF still POST to `api/export.py` but the payload is
  trimmed + gzip-compressed first (see the gotcha below, this used to 413).
  The old `app/export/page.tsx` was removed. Nav is 4 items
  (Dashboard, Technical Audit, Results, Settings): results and detail are one
  section (`/detail` highlights "Results", see `resolveActiveHref` in
  `components/Navbar.tsx`); the list routes to the detail via
  `setSelectedUrlIndex` + `router.push("/detail")`.
- **Top navbar, not a sidebar** (`components/Navbar.tsx`, mounted by
  `components/AppShell.tsx`): a horizontal bar (logo, nav links, global
  search, "+ New Audit", a session pill, dark-mode toggle), collapsing into a
  hamburger dropdown below `md`. Replaced the old fixed-width left sidebar
  (`AppShell.tsx` used to render `<aside>` directly; now it's just
  `<Navbar/>` + a centered `<main class="mx-auto max-w-6xl">`. The floating
  `<ChatWidget/>` that used to sit here was removed in Session 24). The old sidebar was always
  dark regardless of light/dark mode; the navbar uses the same
  `--seo-card-bg`/`--seo-border` tokens as the rest of the app so nav chrome
  now follows the theme toggle too (`--seo-sidebar-*` tokens were removed
  from `globals.css`, nothing else referenced them). `components/GlobalSearch.tsx`
  filters `AuditContext`'s in-memory `results` by URL substring (no network
  call, same data the Results page's own search box filters) and jumps to
  `/detail` via `setSelectedUrlIndex` on select. The session pill
  (`# URLs · key status`) and Settings' AI-key-configured badges both read
  `lib/useAiConfigStatus.ts` (one shared `GET /api/ai` call) instead of each
  page re-deriving its own copy.
- **Fix-difficulty labels:** every issue carries an `effort` field (Low/Medium/
  High) from `modules/auditor.py::_issue` (the single shared issue-dict
  builder; `technical_checks.py` imports it rather than redefining it - don't
  reintroduce a second copy there).
  `lib/difficulty.ts` maps that to Easy/Medium/Hard (keyword fallback when
  `effort` is absent); surfaced via `DifficultyBadge` (in `components/ui.tsx`,
  wired into `IssueRow`), a "Fix effort" column on the Results list, and a
  rollup on the detail header. Do NOT re-derive difficulty ad hoc; use
  `fixDifficulty`/`difficultyBreakdown`.
- **Email-DNS checks are informational, not scored.** SPF / DMARC / MX (and the
  `dns_health_check` summary) are email-deliverability records, not SEO ranking
  signals. `check_dns_health` collects the records but emits NO scored issues;
  the checklist reports them with status `"info"` (a 4th `ChecklistStatus`,
  excluded from the pass/warning/fail summary, which now also carries an `info`
  count). Do NOT reintroduce them as warnings/failures or into `all_issues`.
- **Common Issues & Fixes knowledge base:** `lib/commonIssuesKB.ts`
  (`explainCommonIssue`) generalizes the per-image issue explainer pattern
  (`lib/imageAnalysis.ts::explainImageIssue`) to the app's other issue
  categories: a curated top-~20 list of the most common real issue titles
  (matched by regex against the actual strings emitted by
  `modules/{auditor,technical_checks,advanced_checks,link_auditor,
  mobile_auditor,image_auditor}.py`), each with what-is-it / why-it-matters /
  SEO impact / user impact / recommended fix, and a `source` citation for
  facts that change over time (Core Web Vitals thresholds, mobile-first
  indexing) grounded in a 2026 web search (Google Search Central, web.dev).
  `explainCommonIssue` never returns `null` - an issue with no curated match
  falls back to a generic explanation built from the issue's own
  `category`/`severity`/`recommendation` fields, so every issue has
  *something* to show. Wired into `IssueRow` (`components/ui.tsx`): clicking
  an issue row opens a `Modal` (via `HelpSection`'s modal pattern) showing
  `CommonIssueDetail` (the KB/fallback explanation) plus `FixSuggestionButton`
  when `lib/fixSuggestable.ts::detectFixTarget` matches - NOT an inline
  expand-in-place panel. Guarded by `lib/commonIssuesKB.test.ts`.
- **API routes are consolidated into 3 files, dispatched by an `"action"`
  field in the request** (`api/audit-pipeline.py`, `api/ai.py`, plus
  standalone `api/export.py`) - **not** one file per endpoint. This used to
  be 9 separate `api/*.py` files; Vercel's Python builder reinstalls +
  recompiles the entire `requirements.txt` independently for every
  `api/*.py` file at build time (~14s each, not shared), so 9 files added
  ~2 minutes of pure dependency-install time to every deploy for no reason -
  none of them have different dependencies, they all draw from the same
  `requirements.txt`. Consolidating to 3 cut that by ~100s. **When adding a
  new server-side endpoint, add an action to one of these two files (or
  `api/export.py` if it's a binary/file-download response) - do NOT create a
  new top-level `api/*.py` file**, or the build-time regression comes back.
  - `api/audit-pipeline.py` (`maxDuration` 90, the max of any action's need):
    actions `"audit"` (single-URL audit, returns `modules.auditor.audit_url()`
    almost verbatim), `"sitemap"` (resolves a sitemap/bare domain to a URL
    list, see "Sitewide/bulk audit architecture" below), `"crawl"`
    (discovery-only BFS crawl, same `{urls, total_found, capped}` contract
    shape as sitemap; always calls `crawl_site(..., run_full_audit=False)`,
    per-page audits happen client-side via the orchestrator, not here),
    `"site-health"` (domain-level checks only, so a same-domain crawl can
    compute them once and pass them into `"audit"` calls via
    `prefetchedDomainHealth`), `"pagespeed"` (PSI proxy). Each action is its
    own `_handle_*` function with its own try/except preserving the original
    per-endpoint error message; `_ACTIONS` maps `action` string → function.
  - `api/ai.py` (`maxDuration` 30) - **AI layer** (`modules/ai_assist.py`,
    all Groq), actions `"summary"` and `"fix-suggestion"`, plus a
    plain `GET` for config-status (key-presence only, no action needed since
    it's the only `GET` in the group). **The `"chat"` action + the floating
    `ChatWidget` were removed in Session 24** - the AI is now focused on the
    audit summary and personalized per-page fix drafts; do not reintroduce a
    general-purpose chatbot without discussing it. Both POST actions go through
    the shared `_chat()` HTTP helper (3x retry on 429/5xx, optional
    `json_mode=True` requests Groq's `response_format: json_object` with an
    automatic one-retry fallback to plain text if the model/account 400s on
    it).
    - `"summary"` → `explain_audit(all_issues, seo_score, api_key, url="",
      context_label=None)`: plain-English summary + top actions, JSON-mode
      parsed via `_parse_summary_reply` (falls back to the legacy
      numbered-list regex parse if JSON parsing fails). `context_label`
      overrides the default "for {url}" phrasing - the Results page's
      sitewide summary passes "across N audited pages (sitewide)" with the
      aggregated issue list instead of one page's. Rendered via the shared
      `components/AiSummaryCard.tsx` on both Detail (one URL) and Results
      (sitewide rollup), cached per `cacheKey` in `AuditContext`'s
      `aiSummaryCache` (see `lib/aiSummaryCache.ts::fingerprintForSummary`)
      so reopening unchanged data doesn't re-spend an API call.
    - `"fix-suggestion"` → `suggest_fix(issue_title, page_context, api_key)`:
      drafts an actual ready-to-use replacement (e.g. a real meta
      description) for a **well-defined set of issue types** - see
      `_FIX_TARGET_PATTERNS`/`detect_fix_target`: **title, description, H1,
      Open Graph/Twitter tags, and image alt-text** (expanded from the
      original 3 in Session 24 so more issues get a personalized, page-grounded
      draft instead of only the generic KB explanation). **Single-value targets
      (title/description/H1) use JSON mode; multi-line targets (og/alt) use PLAIN
      TEXT** (`_JSON_FIX_TARGETS`) - forcing JSON around a multi-line Open Graph
      block / several alt lines made models nest the payload or return an empty
      `suggestion` ("didn't return a usable suggestion"), the Session 26 fix.
      `suggest_fix` also strips markdown fences (`_strip_code_fence`), de-nests a
      double-wrapped JSON reply object OR array (`_extract_fix_suggestion`), and
      retries once as plain text if the first attempt is empty. Don't put og/alt
      back into JSON mode.
      `lib/fixSuggestable.ts::detectFixTarget` mirrors the same
      patterns client-side so `components/ui.tsx::IssueRow` only shows "✨
      Suggest a fix" for issues it can actually draft for, without a
      round-trip just to find out. Only wired up where a single concrete
      page is in scope (Detail page passes `pageContext` -
      title/description/h1/content snippet from
      `r.metadata`/`r.heading_detail`/`r.content.intro_paragraphs` - into
      every `IssueRow`); there's no sitewide equivalent since a fix draft
      needs one page's real content to ground in, not an aggregate.
  - `tests/test_api_consolidation.py` covers the `_ACTIONS` dispatch tables
    directly (loaded via `importlib` since the filenames have hyphens); the
    thin per-action handler bodies stay untested per this repo's existing
    convention (they just call straight into already-tested `modules/*.py`
    functions).
- `modules/auditor.py`: core fetch + orchestration, SSRF guard (`validate_audit_url`).
  `audit_url(..., prefetched=None)` accepts an already-fetched page (the shape
  `fetch_page()` returns) to avoid a duplicate fetch when a caller (e.g.
  `modules/crawler.py`) already has the page in hand.
- `modules/sitemap_extractor.py`: sitemap/sitemap-index fetch + recursion
  (depth cap 5), gzip support, SSRF-validated, dedup + include/exclude filter
  + URL cap (default 50, max 200). Adapts the parse logic already in
  `technical_checks.py::check_sitemap` (which only counts URLs; this module
  returns them).
- `modules/crawler.py`: `CrawlConfig` + `crawl_site()`: BFS link-discovery
  crawl with seed selection (homepage/sitemap/URL list), scope control
  (domain/subdomain + include/exclude regex + depth/page caps), UA presets
  (default/googlebot/googlebot-mobile/bingbot), and 3 robots.txt modes
  (respect/ignore/ignore_but_report). `run_full_audit=True` will also run
  `audit_url()` per discovered page (useful for CLI/synchronous use, but NOT
  what `api/audit-pipeline.py`'s "crawl" action uses, see above). Adopted from the `venkataramana-work`
  branch of this repo (do not delete that branch: it has a phases.md roadmap
  for a future async job-queue crawl architecture, phases 2-6, not yet built here).
- `modules/technical_checks.py`: domain age (WHOIS), SSL, HTTPS enforcement
  (does http:// redirect to https://?), DNS/SPF/DMARC/MX, robots.txt,
  sitemap.xml, readability, content freshness, canonical-loop detection,
  www-redirect consistency, HTTP/2, aggregated as `site_health`
- `modules/ai_assist.py`: Groq `explain_audit()`
- `modules/technical_audit_checklist.py`: builds the 35-check "Technical SEO
  Audit" checklist (crawlability/on_page/site_health groups, pass/warning/
  fail/**info** per check, the "info" status is the email-DNS checks, see
  below) ported from the standalone tool's use-case definition; it's a
  read-only view derived from an already-computed `audit_url()` result, never
  re-fetches or re-scores. Exposed as `result["technical_audit_checklist"]`.
- **Detail page "Technical" tab** (`app/detail/page.tsx`): a single merged tab
  (previously two separate "Technical Audit" and "Technical" tabs, confusing
  side by side; merged into one). Organized into the checklist's 3 real
  use-case sections, Crawlability / On-Page / Site Health, each pairing the
  pass/warning/fail/info `ChecklistGroupCard` for that group with its matching
  rich detail card(s): Crawlability -> redirect chain + hreflang; On-Page ->
  Schema Audit (structured-data types/errors), Mobile Responsiveness (summary
  card, cross-links to the full mobile audit on the Performance tab instead of
  duplicating it), Social Preview (OG); Site Health -> domain/protocol detail
  + security headers. The old "estimated Core Web Vitals" card was dropped
  (superseded by Performance tab's real PSI-based CWV, keeping it was a
  duplicate). Detail tabs are now 7: Overview, Technical, Issues, Links,
  Headings, Content & Images, Performance. ("Recommendations" was an 8th tab
  that just re-rendered the top-10-by-impact issues through the same
  `IssueRow` the Issues tab already uses; folded into a "Top Issues by
  Impact" card at the top of Issues instead, plus a smaller top-3 version on
  Overview - see `topIssues` in `app/detail/page.tsx`. Both tabs and TabBar
  itself now share `components/ui.tsx::TabBar`/`IssueExplanationGrid`, see
  the "Shared UI components" note below.) The Content & Images tab's Images
  card cross-links to Performance's Image SEO sub-tab (same pattern as the
  Technical tab's Mobile Responsiveness cross-link). Headings' old "H1
  Across Site" sub-tab (a cross-URL report living on a per-URL page) moved
  to the Results page as a collapsible "Sitewide H1 Report" card, next to
  the Sitewide Summary rollup - `HeadingsView` no longer takes an
  `allResults` prop.
- **Shared UI components** (`components/ui.tsx`): `TabBar` (generic tab-bar
  row, used by `LinksView`/`HeadingsView`/`PerformanceView`'s Mobile↔Image
  SEO switch/the Detail page's top-level tabs - previously 4 byte-identical
  copies) and `IssueExplanationGrid` (the What-is-it/Why/SEO-impact/User-
  impact/Recommended-fix grid, used by `HeadingsView`/`PerformanceView`'s
  `ImageIssueDetail`/`LinksView`'s `IssueDetail` - previously 3 independently
  drifted copies, e.g. "why it matters" vs "why is it important?"). Add new
  tab switches or issue-explanation panels through these, don't hand-roll a
  new copy. `ui.tsx`'s own `CommonIssueDetail` (backing the Common Issues KB
  "Learn more" expansion) is intentionally NOT merged into
  `IssueExplanationGrid`: it has a different container (card background) and
  an extra `source` citation field the other three don't have.
- **`api/*.py` shared helpers** (`modules/_http.py`): `send_json`,
  `read_json_body`, `require_str`, `validate_url_or_400`, `validate_pattern`
  consolidate what used to be byte-identical boilerplate copy-pasted into
  every `api/*.py` handler (each is its own isolated Vercel function, but
  they already import freely from `modules/*`, so sharing costs nothing).
  `api/export.py` keeps its own `decode_request_body` (gzip-aware, genuinely
  different from `read_json_body`) but reuses `send_json`. New `api/*.py`
  handlers should use these instead of re-adding a local `_send_json`.
- **SSRF: `modules/auditor.py::safe_get` is now `safe_request(method, url,
  ...)` generalized** (`safe_get` is a thin `requests.get`-bound wrapper over
  it). `link_auditor.py::validate_url` and `image_auditor.py::_fetch_size`
  both now route their HEAD/GET calls through `safe_request` too (previously
  each re-implemented its own manual redirect-loop, and `link_auditor.py`'s
  didn't re-validate redirect hops at all - a real SSRF gap, since a link
  that passed the initial check could still 301 to an internal host).
- `lib/checklistDefs.ts`: the **frontend mirror** of the 35 check ids/labels/
  groups in `modules/technical_audit_checklist.py`, plus a one-sentence
  plain-English `description` per check (used by the explainer card, the
  check-selection panel, and per-check tooltips). Keep in sync with the
  Python module; `lib/checklistDefs.test.ts` guards the 35-total / 12-11-12
  group split but can't catch an id renamed on only one side.
- `lib/useSelectedChecks.ts`: shared localStorage-persisted hook for which
  checks are enabled (default: all 35). Used by `components/CheckSelector.tsx`
  (the "Customize checks" panel on the Technical Audit page) to edit the
  selection, and by the detail page's Technical tab to filter which
  checks are displayed. **Display-only filter**: the backend always computes
  all 35 checks in one `audit_url()` call regardless of selection, since
  they're bundled into a single page fetch and skipping individual checks
  server-side wouldn't meaningfully speed anything up.
- `components/ui.tsx::HelpSection`: reusable "ⓘ" icon button that opens a
  shared `Modal` (`components/ui.tsx::Modal`) with a plain-English
  explanation; used on each Technical Audit input-mode card, each checklist
  group (Crawlability/On-Page/Site Health), and (via the same `Modal`)
  `IssueRow`'s issue detail (see Common Issues KB note above). Supersedes the
  old standalone `components/HelpDialog.tsx` popover (deleted - folded into
  this shared Modal pattern, merged in from `venkataramana-work`).
- `components/ChecklistExplainer.tsx`: the "What Technical SEO checks" card
  (all 35 checks as pills + a "when to use" note), mirroring the reference
  tool's use-case explainer. Rendered on `app/technical-audit/page.tsx` below
  the audit form (not above it), and collapsed by default so it doesn't push
  the form below the fold; expands on click to show the full check list.
- `modules/advanced_checks.py`, `link_auditor.py`, `image_auditor.py`,
  `heading_auditor.py`, `mobile_auditor.py`, `course_auditor.py`,
  `blog_auditor.py`, `pagespeed.py`, `report_generator.py`, `scoring.py`
- **`report_generator.py` CSV/Excel formula-injection guard:** every
  page-controlled string written to a CSV/XLSX cell (title, description,
  canonical URL, anchor text, issue text, checklist detail) goes through
  `_sanitize_row`/`_sanitize_cell`, which prefixes a leading `'` on any value
  starting with `=`/`+`/`-`/`@` so a malicious page can't get Excel/Sheets to
  evaluate a formula when the export is later opened. PDF export is
  unaffected (FPDF draws text, never evaluates formulas). Any new field added
  to `flatten()` or the Excel row-builders in `generate_excel()` MUST be
  wrapped in `_sanitize_row` too, don't build a raw dict and skip it.
- `lib/aggregate.ts`, `lib/scoring.ts`: **must stay in sync** with
  `modules/scoring.py`'s `WEIGHTS`/`THEMES` (duplicated by design, see the
  comment in `lib/aggregate.ts`, to avoid round-tripping to the Python API
  just to group/sort already-computed issues)
- `lib/state/AuditContext.tsx`: client-side persisted state (results, Groq API
  key), backed by `lib/state/idbStore.ts` (IndexedDB, see gotcha below).
  `addResults(results[])` batches N results into one state update/one
  persisted write; use this for bulk audits, not a loop of `addResult`.
  Exposes `storageWarning` (shown as a banner in `AppShell.tsx`) for the rare
  case a save still fails.
- `lib/useTheme.ts`: shared light/dark toggle logic (pub-sub so multiple
  mounted toggles, e.g. the sidebar `ThemeToggle` and the Settings page's
  Appearance card, stay in sync live without a reload).
- `lib/crawl/parseUrlList.ts`: client-side CSV/TSV/paste URL-list parser (no
  upload, no server storage). Detects a url/link header column or scrapes any
  http(s) cell.
- `lib/crawl/orchestrator.ts`: `runCrawl(urls, opts, callbacks)`: bounded-
  concurrency (default 5, max 10) fan-out of single-URL `/api/audit` calls from
  the browser, with progress + incremental-result callbacks and abort support.
  Not capped at any URL count itself; `lib/crawl/chunkedRunner.ts` is what
  chunks a large list before handing each chunk to this.
- `lib/crawl/chunkedRunner.ts`: `runChunked(allUrls, remainingUrls, options,
  label, callbacks, resumeFrom?)`: splits a URL list into `CHUNK_SIZE=200`
  batches, auto-advancing through `runCrawl` per chunk, and persists a
  resumable checkpoint (remaining URLs + cumulative succeeded/failed) to
  IndexedDB after every single result, not just at chunk boundaries. Powers
  the "Interrupted audit found: Resume/Discard" banner on
  `app/technical-audit/page.tsx`. See "Sitewide/bulk audit architecture" below.
- `tests/`: pytest unit tests (pure-logic checklist tests + mocked-network
  tests for `check_https_enforcement`/X-Robots-Tag handling + sitemap
  extractor); see "Testing" below. `lib/crawl/*.test.ts`: Vitest unit tests
  for the URL-list parser and the chunked runner (`vitest.config.ts` adds the
  `@/*` path alias plain Vitest doesn't resolve on its own).

## How to run
```bash
npm install
python -m venv .venv && source .venv/bin/activate  # .venv\Scripts\activate on Windows
pip install -r requirements.txt
npm run dev
```
`/api/*.py` endpoints only run under Vercel's runtime (`vercel dev`); plain
`next dev` 404s on API calls, expected for frontend-only work.

## Sitewide/bulk audit architecture
The Technical Audit page (`app/technical-audit/page.tsx`) supports Single URL,
Sitemap, Crawl-from-URL, and CSV/Paste modes. Because this app is stateless
Vercel serverless (no DB, no long-lived process, no SSE, 60s/function cap,
see `vercel.json`), sitewide audits are **client-orchestrated**, not
server-orchestrated like the reference tools (SEO Suite uses a long-lived
Flask process + daemon thread + SSE + on-disk checkpointing; the
`venkataramana-work` branch's own crawl roadmap plans an async job-queue;
neither model ports here as-is):
1. One of three URL-resolution steps runs first, each bounded and fast enough
   to fit in a single invocation: `api/audit-pipeline.py`'s "sitemap" action
   (sitemap/sitemap-index fetch, `MAX_URL_CAP` in
   `modules/sitemap_extractor.py`), `api/audit-pipeline.py`'s "crawl" action
   (BFS link discovery, `run_full_audit=False`, `MAX_MAX_PAGES` in
   `api/audit-pipeline.py` - a real per-page fetch unlike a sitemap's one
   XML download, so it's the most time-pressured of the three against the
   90s `maxDuration`), or the client-side CSV/paste parser (no network call,
   `MAX_LIMIT` in `app/technical-audit/page.tsx`).
   - **Bulk URL cap: 200 in production, 5000 in local dev** - shared by all
     three via `modules/_http.py::bulk_url_cap()` (backend) and
     `NEXT_PUBLIC_BULK_URL_LIMIT`, baked into the client bundle at build
     time by `next.config.ts` (`process.env.VERCEL ? "200" : "5000"`;
     Vercel sets `VERCEL=1` for every build it runs, production AND
     preview). Was 4000 across the board; that drove real Vercel CPU-usage
     overage, because `runChunked`/`runCrawl` (below) fan a bulk audit out
     to one `POST /api/audit-pipeline` invocation per URL, and each
     invocation runs several `ThreadPoolExecutor`-backed site-health checks
     (WHOIS/DNS/SSL/robots/sitemap/HTTP2, see `technical_checks.py`) - a
     4000-URL crawl could spin up thousands of concurrent invocations, each
     doing real CPU-bound parsing (BeautifulSoup/lxml) on top of the
     network waits. `api/audit-pipeline.py`'s Python handlers only ever run
     on Vercel (plain `next dev` 404s on API calls, see the gotcha below),
     so the backend cap doesn't need a local/prod split - it's always the
     "prod" branch in practice; the frontend's local-vs-prod split exists so
     a developer can still exercise the client-side parsing/chunking logic
     with a large list locally even though there's no live backend to
     actually audit it against. A clear "Up to N URLs per audit" line
     (`BulkLimitNote` in `app/technical-audit/page.tsx`) now sits above
     every bulk-mode URL input (sitemap/crawl/CSV) so this isn't buried in
     just the numeric "URL limit" field below it.
2. `lib/crawl/chunkedRunner.ts::runChunked` splits the resolved list into
   `CHUNK_SIZE=200` batches. Each batch goes through
   `lib/crawl/orchestrator.ts::runCrawl`, which fans out bounded-concurrency
   single-URL `POST /api/audit` calls, one invocation per URL, so nothing
   risks the function timeout. Batches auto-advance with no user action
   needed as long as the tab stays open; a checkpoint (remaining URLs +
   cumulative succeeded/failed) persists to IndexedDB after every result, so
   a closed/crashed tab can resume instead of restarting.
3. Results batch into `AuditContext.addResults()` (one state update per batch,
   not per URL) and the Results page renders a sitewide rollup card
   (avg score, score distribution, top failing checks) when 2+ URLs are present.

Live-verified end-to-end against `https://www.edstellar.com/sitemap.xml`
(2,461 URLs), see `tests/test_sitewide_pipeline_live.py` (opt-in,
`RUN_LIVE_TESTS=1`) and `PROJECT_LOG.md` session history.

## Favicon / metadata / own-site SEO
The app is itself set to `noindex, nofollow` (internal tool, confirmed with
the user) via `metadata.robots` in `app/layout.tsx` + `app/robots.ts`'s
disallow-all - do not flip this without asking, it was an explicit choice,
not an oversight. Icons (`app/icon.tsx`, `app/apple-icon.tsx`) and the Open
Graph card (`app/opengraph-image.tsx`) are generated at build time via
`next/og`'s `ImageResponse` (Satori), drawn as plain CSS shapes (a circle +
rotated bar for the magnifying glass) rather than the 🔍 emoji glyph:
Satori has no bundled emoji font, so an emoji character silently renders as
a blurry fallback glyph instead of the icon. `app/manifest.ts` references
those same icon routes. `metadataBase`/`openGraph.url` in `app/layout.tsx`
default to `https://seo-technical-audit-dashboard-venkat-ramana.vercel.app`
(the live domain), overridable via the `NEXT_PUBLIC_SITE_URL` / `APP_BASE_URL`
env vars if the deploy domain changes. The old default Next.js
`app/favicon.ico` was deleted in favor of `icon.tsx`.

## Design system (modern-SaaS, Session 23)
- **Font:** Inter (UI) + JetBrains Mono (`.font-mono`, for URLs/values), wired via
  `next/font/google` in `app/layout.tsx` (self-hosted at build time - do NOT add a
  CSP allowance or a `<link>` to Google Fonts, and don't reintroduce the Arial
  stack). `--font-sans`/`--font-mono` in `globals.css` point at them.
- **Tokens live in `app/globals.css`** (`--seo-*`, light default + `.dark`
  override): radius scale (`--seo-radius` 10px / `-lg` 14 / `-sm` 8 / `-pill` 6),
  hairline-first elevation (`--seo-shadow-*` are intentionally subtle - a crisp
  border does the work, not a drop shadow), restrained accent (gradients are
  dialed back; `.btn-gradient` is a FLAT accent fill now, not a glow). Use the
  `.tabular-nums` utility on any numeric display (scores/counts/metrics).
- **Icons: `components/icons.tsx`** - Lucide-style inline SVG (`currentColor`,
  1.75 stroke). **Do NOT use emoji as structural icons** (they render per-OS and
  can't be themed); add a new SVG here and import it. `PageHeader` takes an
  `icon` prop; pages pass their SVG, not an emoji in the title string.
- **Shared components** stay in `components/ui.tsx` (Card, Modal, TabBar,
  MetricCard, badges, PageHeader) - restyle THERE, don't hand-roll a variant.
- The favicon/OG generators (`app/icon.tsx`, `apple-icon.tsx`,
  `opengraph-image.tsx`) draw CSS shapes at build time (Satori has no emoji
  font); they are not app UI and were intentionally left as-is.

## Security headers / CSP
`proxy.ts` sets a nonce-based Content-Security-Policy per request
(Next.js's documented pattern: `script-src 'self' 'nonce-<random>'
'strict-dynamic'`) - **not** a static CSP in `next.config.ts`, because App
Router's streaming hydration relies on inline `<script>` tags (RSC flight
data, and the theme-init script in `app/layout.tsx`) that a plain
`script-src 'self'` blocks outright, silently breaking all client
interactivity (buttons/toggles render but do nothing, no console error by
default). `app/layout.tsx` reads the nonce via `next/headers`'s `headers()`
and passes it to the inline theme script; any other inline `<script>` added
to the app needs the same treatment or it will be blocked. In dev, the CSP
also allows `'unsafe-eval'` (React's dev-mode call-stack reconstruction uses
`eval()`; this is stripped in production via `process.env.NODE_ENV` in
`proxy.ts`). `next.config.ts` still sets the static headers that don't
need a nonce (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`,
`Permissions-Policy`).

## Env vars
- `PSI_API_KEY` (optional): PageSpeed Insights quota
- `GROQ_API_KEY` (optional): server-side default for the AI features - the
  audit **summary** and the personalized **fix suggestions** (`api/ai.py`'s
  `"summary"` / `"fix-suggestion"` actions); users can also paste their own key
  in Settings (stored in browser localStorage only)

## Testing
```bash
# Python
source .venv/Scripts/activate  # .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
python -m pytest tests/ -v                       # unit tests, network mocked
RUN_LIVE_TESTS=1 python -m pytest tests/ -v       # + opt-in live network tests

# Frontend (Vitest)
npm run test
```
`conftest.py` at the repo root puts the project root on `sys.path` so
`from modules...` imports resolve without the `api/*.py` Vercel-handler
sys.path shim. Live tests (`test_live_edstellar_sitemap`,
`test_sitewide_pipeline_live.py`) are skipped by default; they hit the real
Edstellar sitemap/pages and take 30+ seconds. Opt in with `RUN_LIVE_TESTS=1`.

## Agent notes / gotchas
- **SSRF: any new outbound fetch of a user/target-influenced URL must go
  through the guards.** The app audits arbitrary user-supplied URLs, so
  `modules/auditor.py::validate_audit_url` (blocks private/reserved IPs and
  hostnames that resolve to them) and `modules/auditor.py::safe_get` (follows
  redirects manually, re-validating every hop, so a public URL that 301s to an
  internal/metadata host is blocked mid-chain) are the guards. `fetch_page`
  uses `safe_get`. Callers that fetch URLs derived from page content or a
  target's robots.txt/sitemap (`link_auditor.validate_url`,
  `crawler._fetch_sitemap_locs`) call `validate_audit_url` first.
  `technical_checks.py`'s robots/sitemap/HTTPS-enforcement/canonical-loop/
  www-redirect checks, `crawler.py`'s sitemap+robots.txt fetches, and
  `image_auditor.py`'s per-image size-check fetches (page-controlled `<img
  src>`, previously completely unguarded) all now route through `safe_get`
  too, closing the redirect-gap and unguarded-fetch findings from the
  2026-07 security audit (see PROJECT_LOG). Covered by `tests/test_ssrf.py`.
- **`all_issues` aggregation excludes the legacy `headings` and `images`
  blocks** (`auditor.py`): `heading_detail`/`image_detail` are the thorough
  versions scoring uses, and including both would double-count. If you add a
  new check module, add its key to that aggregation list AND make sure its
  issue `category` is mapped in `scoring.py`/`aggregate.ts` `THEMES` (an
  unmapped category silently lands in the "Other" bucket, which is how the
  "Image SEO" issues were getting lost).
- **Every source in the `all_issues` list must also be SCORED** in
  `scoring.py::calculate_seo_score`'s breakdown. `mobile_audit` was in
  `all_issues` (shown/counted/fed to the AI) but not in any scoring bucket, so
  mobile issues (missing viewport, intrusive interstitial) moved the score by 0
  (Session 27 fix - folded into the `advanced` bucket). When adding a check
  module, add it to BOTH lists.
- **`auditor._normalize_issues` backfills `impact_score` + `effort`** on every
  `all_issues` entry (severity→default table) - some modules (e.g.
  `blog_auditor`) build issue dicts inline without them, which made a
  High-severity issue sort below a Low (missing impact_score treated as 0) and
  undercounted the fix-effort chips. Don't rely on a module always setting these;
  the normalizer is the safety net, but prefer `_issue()` when writing new checks.
- `lib/crawl/chunkedRunner.ts::runChunked`'s optional `resumeFrom` param
  (`{succeeded, failed, startedAt}`) exists because a prior version reset
  succeeded/failed to 0 on every resume, silently showing only the resumed
  session's count instead of the cumulative total for the whole job. If you
  change this module, keep the checkpoint (not the in-memory counters) as
  the source of truth for cumulative counts across a pause/resume boundary.
- **Persistence is IndexedDB, not localStorage** (`lib/state/idbStore.ts`,
  used by `AuditContext.tsx`). It used to be localStorage, but every state
  change serializes the WHOLE `results` array as one blob, and a bulk audit
  of ~200 URLs (each a full `audit_url()` result, 50-200KB) routinely blew
  past localStorage's ~5-10MB per-origin quota and threw
  `QuotaExceededError`, crashing the app. Don't add a second localStorage
  write path for audit results; `AuditContext` migrates any legacy
  localStorage data on first load, then removes it. If you ever see the
  quota error again, it means something is bypassing `AuditContext`.
- **Export 413 fix (Session 20): CSV/JSON are client-only, Excel/PDF are
  trimmed + gzipped.** POSTing the entire `results` array as one JSON body
  used to 413 past Vercel's ~4.5MB serverless request-body limit on anything
  beyond a handful of audited URLs (each full `audit_url()` result is
  50-200KB, same growth problem the IndexedDB migration above solved
  client-side, but this was server-side). Fixed in `lib/reportExport.ts`:
  - `buildResultsCsvRows`/`downloadResultsJson` generate CSV/JSON entirely in
    the browser (the data's already in memory) and never touch the network,
    so there is no size limit for those two formats at all.
  - `trimResultForServerExport` strips every field
    `modules/report_generator.py` doesn't actually read (`image_detail`,
    `advanced`, `site_health`, `mobile_audit`, paragraph HTML, the checklist's
    `groups` key, ...) before Excel/PDF requests, then `gzipJson` compresses
    the trimmed payload (native `CompressionStream`, no new dependency).
    Measured: two results padded with ~100KB of realistic junk data each
    compressed to **1.8KB** total. `api/export.py::decode_request_body`
    gunzips the body when `Content-Encoding: gzip` is set.
  - A client-side size guard (`MAX_EXPORT_PAYLOAD_BYTES`, 4MB) checks the
    compressed size before sending and shows a clear message ("try CSV/JSON
    instead") rather than letting an unusually large export hit a bare 413.
  - `lib/format.ts::downloadCsv` now sanitizes every cell via
    `sanitizeCsvCell` (mirrors `report_generator.py`'s `_sanitize_cell`),
    which also fixed the same un-sanitized formula-injection gap in the
    Links/Headings/Image-SEO CSV exports (`downloadCsv` is shared by all of
    them), not just the new Results export.
  - If you add a new field to `AuditResult` that Excel/PDF need,
    add it to `trimResultForServerExport` too, or it will silently be
    dropped from those two export formats (CSV/JSON always get the full
    object, only the server-bound payload is trimmed).
- Don't build a second dark-mode toggle from scratch. `lib/useTheme.ts` is
  the single source of truth (pub-sub so every mounted toggle stays in sync
  live); both `components/ThemeToggle.tsx` (sidebar) and the Settings page's
  Appearance card use it.
- Every check module returns `{..., "issues": [...]}` where each issue is
  `{issue, category, severity, recommendation, impact_score, effort}`; follow
  this convention for any new check so it aggregates into `all_issues` and
  `modules/scoring.py`'s category scoring automatically.
- `modules/technical_checks.py`'s checks run concurrently via
  `ThreadPoolExecutor` in `analyze_site_health()`; several hit the domain
  root or DNS independently of the already-fetched page (robots.txt,
  sitemap.xml, WHOIS, SSL socket, DNS, www-alt-domain, HTTP/2). Readability
  and content-freshness reuse the already-parsed page text/soup/headers to
  avoid a duplicate fetch, don't refetch there.
- `python-whois`, `dnspython`, `httpx[http2]`, `textstat` are optional at
  import time in `technical_checks.py`; each check degrades to
  `{"available": False, "issues": []}` if the package is missing, rather than
  crashing the audit.
- Any change to `modules/scoring.py`'s `WEIGHTS`/`THEMES` must be mirrored in
  `lib/scoring.ts`/`lib/aggregate.ts` in the same commit; doc/config drift
  between the two is a bug.
- **`THEMES` matching is substring** (`keyword.lower() in category.lower()`),
  so every keyword must be a substring of the EXACT category strings the check
  modules emit (grep `"category":` across `modules/*.py`). A keyword that
  doesn't match silently dumps that category into the "Other" bucket. Session
  22 fixed 7 such categories (Heading Structure, Security, Responsiveness,
  Usability, Navigation, User Experience, Layout); guarded by
  `tests/test_scoring.py` + `lib/aggregate.test.ts`. Adding a new check
  category means adding a matching `THEMES` keyword in BOTH files.
- **"blocked"/"unknown" are NOT "broken".** `link_auditor.py::link_health` and
  `image_auditor.py::_populate_sizes` only classify 404/410/hard-5xx as a dead
  resource; 401/403 (WAF/bot-challenge), 408/429 (rate-limit), 503, and
  timeout/SSL/connection failures are "blocked"/"unknown" and excluded from the
  broken count. Do NOT re-bucket them as broken - that was the biggest
  broken-link/image false-positive source (a live Cloudflare-protected link
  reported dead). Guarded by `tests/test_link_auditor.py`/`test_image_auditor.py`.
- **Absence of an OPTIONAL signal is not a scored issue.** Content-freshness
  meta, a `<meta charset>` when the HTTP header already declares one, a favicon
  `<link>` when `/favicon.ico` may exist, `alt=""` on a decorative image - these
  degrade to no-issue / Low-advisory, not a scored problem. Same for a check that
  couldn't verify (timeout on the www-alt probe, an unfetchable canonical target):
  emit nothing rather than assert a claim that may be false. This is the core
  "don't show issues on pages that don't have them" principle from the Session 22
  audit.
- **Page-type classification is URL-pattern-only** (`modules/auditor.py::
  detect_page_type`). The per-page-type auditors (`course_auditor`,
  `blog_auditor`) only run when a page is classified `course`/`blog`; getting
  that wrong fires page-type-specific checks ("Missing Course Overview / CTA /
  Schema", "Missing Author") on pages that aren't that type. The old
  content-signal fallback (counting words like "enroll"/"curriculum") was
  measurably backwards on real data (a genuine edstellar `/course/…` page had 2
  course signals; a non-course `/coaching-solutions` service page had 3), so it
  was removed. Classify via URL patterns only; root path is always `general`. Do
  NOT re-add a content-signal fallback.
- **All displayed timestamps are IST** - `lib/format.ts::formatDate` renders in
  `Asia/Kolkata` with a fixed `en-IN` locale. The fixed locale+zone is also what
  keeps it deterministic across the server/client render (avoids a hydration
  mismatch); don't switch it back to the viewer's `toLocaleString()`.
- **Sitewide issue attribution:** `lib/aggregate.ts::issuesByTitle` groups issues
  by title while KEEPING the affected-page URLs, so the Results rollup can list
  which pages each issue hits (not just "N pages"). `modules/ai_assist.py::
  _aggregate_issues` does the server-side equivalent (dedupe by title + page
  count, severity-sorted Critical-first) so the AI summary cites accurate
  per-issue counts and rare-but-severe issues survive the char-budget truncation.
- The Groq API key entered in Settings is sent per-request to
  `/api/ai-summary` and stored only in browser localStorage, never persisted
  server-side. Don't add server-side storage for it without discussing first.
- X-Robots-Tag `noindex` is owned by `modules/advanced_checks.py::analyze_http_headers`
  (it's the one that appends the issue); `modules/auditor.py::analyze_indexability`
  only folds it into `is_indexable` and separately flags `nofollow` (which
  `advanced_checks.py` does not cover). Don't re-add a noindex issue there or
  it'll double-count against the score.
- `modules/technical_audit_checklist.py`'s 35 checks are the SEO Suite
  `technical_seo` use case (`crawlability` + `on_page` + `site_health` only,
  no page-type classifier, duplicate-content, or a11y dependency; those are
  separate use cases in the source tool and were intentionally left out of
  this checklist). If `modules/scoring.py`'s underlying check modules change
  shape, update the corresponding field lookups in `build_technical_audit_checklist()`.
- Do NOT run a whole sitemap crawl inside one `api/*.py` invocation. The
  60s Vercel cap means one invocation equals one URL. Do NOT copy the reference
  tool's server-side SSE/daemon-thread/checkpoint orchestration model;
  there's no long-lived process here, the browser drives the crawl.
- Sitewide/bulk mode intentionally does NOT dedupe domain-level `site_health`
  checks (WHOIS/DNS/SSL/robots/sitemap/HTTP2) across pages of the same
  domain yet; each URL re-runs them independently. This is a known
  optimization opportunity (see PROJECT_LOG.md "PHASE 2"), not a bug, but
  don't be surprised the WHOIS/DNS calls repeat per URL in a sitewide run.
- `vercel dev` needs interactive account linking on first run (hangs in
  non-interactive/CI environments), so browser verification of `/api/*.py`
  routes in this repo's sessions has instead relied on (a) pytest hitting the
  Python modules directly, and (b) mocking `window.fetch` in the browser to
  verify the client-side orchestrator/UI wiring independently.
- `modules/crawler.py::crawl_site()` supports `run_full_audit=True` (runs
  `audit_url()` per discovered page, synchronously, inside the crawl loop),
  but **`api/audit-pipeline.py`'s "crawl" action always passes `run_full_audit=False`**. Don't flip that:
  with the default `max_pages=50` and a real per-page audit taking 1-5s each,
  a synchronous full-audit crawl risks the 60s Vercel cap. Discovery and
  per-page auditing are deliberately two separate steps here (discovery in
  `api/audit-pipeline.py`'s "crawl" action, auditing via the browser's `lib/crawl/orchestrator.ts`),
  unlike the `venkataramana-work` branch's own single-endpoint design.
- `modules/heading_auditor.py`'s heading tree excludes `<nav>`, `<footer>`,
  and `<aside>` elements (boilerplate headings inside nav/site-chrome were
  previously counted as page content headings, skewing the H1/hierarchy
  checks). **`<header>` is deliberately NOT excluded** (Session 22 fix): the
  standard `<header><h1>Title</h1></header>` / `<article><header
  class="entry-header"><h1>…` CMS pattern put the page's real H1 inside
  `<header>`, so stripping it fired a Critical "Missing H1" false positive on
  valid pages. Site-chrome headings live in `<nav>`/`<footer>`, not `<header>`.
  Covered by `tests/test_heading_auditor.py`.
- The `venkataramana-work` branch (`git fetch origin venkataramana-work`) has
  a `phases.md` with a fuller crawl-feature roadmap (async job queue +
  SQLite/Postgres persistence, resumable `crawl_step`, optional Playwright JS
  rendering, site-wide score aggregation, dedicated `/crawl` UI), worth
  reading before extending crawl further. Not merged wholesale; only
  `modules/crawler.py` + its tests + the `auditor.py` `prefetched` param were
  adopted so far. Do not delete this branch.
