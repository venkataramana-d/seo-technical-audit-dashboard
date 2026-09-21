# How Screaming Frog Does a Technical Audit - The Workflow Plan

A step-by-step reference for how the Screaming Frog SEO Spider runs a technical
audit, so we can mirror its process. Sourced from screamingfrog.co.uk (product,
user guide, tutorials). Companion to [`ROADMAP.md`](ROADMAP.md),
[`BUILD-PLAN.md`](BUILD-PLAN.md).

---

## The mental model
Screaming Frog is a **desktop crawler** that behaves like Googlebot: it fetches a
URL, parses the HTML (optionally renders JS), extracts every data point and link,
queues the new links, and repeats - streaming a **spreadsheet-style grid of every
URL** into ~25 tabs in real time. A technical audit in SF is: **configure -> crawl
-> (post-crawl) analyze -> triage issues -> drill to the exact URL/element ->
export/report.** The rows ARE the audit; every issue is a filter over real data.

---

## Step 1 - Configure the crawl (before starting)
- **Storage mode:** Memory (fast, ~10k-150k URLs) or **Database mode** (disk-backed,
  millions of URLs). Set for the site size.
- **Crawl config** (`Config > Spider`):
  - *Crawl tab:* what to crawl/store - images, CSS, JS, canonicals, pagination,
    hreflang, AMP, external links, subdomains, linked XML sitemaps.
  - *Limits tab:* total URL limit, **crawl depth**, max URLs per level, query
    strings, redirects to follow, URL length.
  - *Rendering tab:* Text-only vs **JavaScript rendering** (headless Chromium).
  - *Advanced:* respect/ignore noindex, canonicals, robots.txt.
- **Scope:** Include/Exclude by regex, User-Agent, speed (threads + URLs/sec).
- **Connect APIs** (optional): Google Search Console, GA4, PageSpeed Insights.

## Step 2 - Pick a crawl mode
- **Spider** (default): enter a start URL -> crawls outward via internal links.
- **List:** paste/upload a fixed set of URLs (migrations, redirect checks).
- **SERP:** paste titles/descriptions to preview pixel widths (no crawl).
- **Compare:** diff two saved crawls to track progress.

## Step 3 - Run the crawl (real-time)
Click Start. Results **stream live** into the tabs as pages are fetched; the
right-hand **Overview** panel tallies issues in real time. You can pause/resume,
filter, and inspect mid-crawl.

## Step 4 - What it captures per URL (the data)
Every URL becomes a row with dozens of columns, organized into master tabs:
- **Internal / External** - status, indexability, content type, size, **word
  count**, **crawl depth**, inlinks/outlinks, response time.
- **Response Codes** - No response / 2xx / 3xx / 4xx / 5xx / blocked by robots.
- **Page Titles / Meta Description** - missing, duplicate, over/under length
  (pixels + chars), multiple, same-as-H1.
- **H1 / H2** - missing, duplicate, multiple, length.
- **Content** - word count, **readability**, **spelling & grammar**, **near-
  duplicates / exact duplicates**, low content.
- **Images** - missing alt, alt too long, >100KB, missing dimensions.
- **Canonicals** - missing, canonicalised, self-referencing, chains.
- **Pagination** - rel next/prev sequences, non-indexable pagination.
- **Directives** - noindex, nofollow, meta refresh, X-Robots-Tag.
- **Hreflang** - missing/incorrect return links, non-canonical, non-200.
- **Structured Data** - JSON-LD/Microdata/RDFa + schema.org & Rich Results
  validation errors.
- **Security** - HTTP/HTTPS, mixed content, HSTS, insecure forms.
- **AMP, Sitemaps, PageSpeed, Mobile, Accessibility, Links, JavaScript,
  Custom Extraction/Search, Validation.**

## Step 5 - Run "Crawl Analysis" (the post-crawl step)
Some metrics need the whole crawl first. `Crawl Analysis > Start` computes:
- **Link Score** - an internal PageRank-style 0-100 score from the link graph.
- **Orphan URLs** - pages in GA/GSC/sitemap but not found by the crawl.
- **Near-duplicate content** clusters, plus pagination/hreflang/AMP/sitemap
  filters that need full-crawl context.

## Step 6 - Triage the issues
- **Overview panel:** running counts per tab/filter with % and issue tallies.
- **Issues tab:** aggregated issues with severity/priority.
- Use filters to jump to a problem set: Response Codes -> 4xx (broken), Page
  Titles -> Duplicate, Directives -> Noindex, Canonicals -> Missing, etc.

## Step 7 - Drill to the exact URL and element
Click any row -> the **lower pane** shows that URL's detail: **Inlinks / Outlinks**
(who links to it + anchor text), Image details, Duplicate details, SERP snippet,
**rendered screenshot**, **View Source (raw + rendered)**, HTTP headers, cookies,
structured-data details. This is how you get from "issue" to "exactly where."

## Step 8 - Visualize structure
Crawl-tree graph, **force-directed link diagram**, directory tree, depth
distribution - nodes sized/colored by Link Score, inlinks, word count, etc.

## Step 9 - Report & export
- **Reports:** Crawl Overview, redirect chains, canonical chains, hreflang,
  orphan pages, insecure content, structured data, etc.
- **Bulk Export:** all inlinks/outlinks/anchor text/images grouped by issue.
- **XML Sitemap generation** (with lastmod/priority/changefreq).
- Export to CSV/Excel or push to **Google Sheets / Looker**.

## Step 10 - Automate
**Scheduling** (recurring crawls with preset config + auto-export) and
**command-line / headless** mode for CI and repeatable audits.

---

## What a "technical audit" covers in SF (checklist by theme)
- **Crawlability & indexability:** robots.txt, noindex/nofollow, canonicals,
  crawl depth, orphan pages, redirect chains/loops, status codes.
- **On-page:** titles, meta descriptions, H1/H2, duplicates, word count.
- **Content:** thin/duplicate/near-duplicate, readability, spelling.
- **Links:** broken internal/external, anchor text, inlinks/outlinks, Link Score.
- **Media:** image alt/size/dimensions.
- **International:** hreflang correctness.
- **Structured data:** schema validation / Rich Results.
- **Security:** HTTPS, mixed content, HSTS.
- **Performance:** PageSpeed/CWV, page size, response times.
- **Mobile & accessibility.**

---

## How our tool maps to this (gap view)
| SF step | Us today | Gap |
|---|---|---|
| Configure + crawl at scale | browser-driven, ~5k, tab-bound | server-side engine (M2) |
| Real-time streaming grid | batch; **Explorer grid built** | live streaming |
| Per-URL data + tabs | strong per-URL checks | pagination/AMP/security/directives/a11y/structured-data-validation |
| **Crawl Analysis** (Link Score, orphans, near-dup) | near-dup only | Link Score + orphans (M3) |
| Triage (Overview/Issues, filters) | **Explorer + clear filters built** | issue-type tabs |
| **Drill to exact element** | **"show where" + LOCATE built** | inlinks/outlinks-per-URL, view-source |
| Visualize | none | crawl-tree / force-directed graph |
| Report/export | CSV/Excel/PDF/JSON | filtered + All-Issues export, sitemap gen, Sheets |
| Automate | worker scheduler (unwired) | wire scheduling + alerts |

**Read this next to [`BUILD-PLAN.md`](BUILD-PLAN.md)** - the milestones there
(M0 show-where [done], M1 issue UI [done], M2 scale, M3 Link Score/orphans/deeper
checks, M4 AI, M5 integrations) are exactly the path from our current state to
this SF workflow.
