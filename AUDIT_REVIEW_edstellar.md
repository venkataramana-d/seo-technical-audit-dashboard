# Deep Audit: How Results & Top Issues Are Produced - Content Quality, Logic, AI

**Scope:** How the dashboard produces Results, Top Issues, scores, content-quality findings, and AI output.
**Method:** Ran the *real* Python audit engine live against `https://www.edstellar.com/sitemap.xml` (2,461 URLs found), auditing a representative sample - homepage, a `/course/` page, a `/blog/` page - with PageSpeed API and the live Groq AI summary + fix-suggestion path enabled. Findings below are backed by actual output, not code-reading alone.
**Date:** 2026-07-15 · **AI model in use:** Groq `llama-3.1-8b-instant`

> ⚠️ **Security note:** The Groq and PageSpeed API keys were shared in plaintext chat to run this test. Rotate both now (Groq console → revoke key; Google Cloud → regenerate PSI key). They were used in-memory only and never written to any file or commit.

---

## ✅ Resolution (Session 26 - all findings addressed)

| Finding | Status | What changed |
|---|---|---|
| **BUG #1** mobile_audit not scored | ✅ Fixed | `mobile_audit` issues folded into the scored `advanced` bucket in `scoring.py`; regression test asserts a Critical mobile issue lowers the score. |
| **BUG #2** blog issues missing impact/effort | ✅ Fixed | `auditor._normalize_issues` backfills `impact_score` + `effort` (from severity) on every `all_issues` entry; also closes BUG #6's None-holes. Test added. |
| **BUG #3** non-reproducible TTFB score | ✅ Mitigated | Bands widened (High only >1200ms), relabeled "estimated ~Xms", so ordinary jitter no longer flips severity/score. (Authoritative PSI TTFB noted as future.) Test added. |
| **BUG #4** suggest_fix alt-text dead-ends | ✅ Fixed | og/alt now use plain-text output (not JSON mode); fences stripped, JSON de-nested, plain-text retry. Live-verified all 4 fix types return clean output. |
| **BUG #5/#6** dual orderings / impact semantics | ◑ Partly | BUG #6 None-holes fixed via the normalizer + a severity→impact default table. Unifying the two "top issue" orderings left as a documented Low. |
| **§3.3** noopener overweighted/miscategorized | ✅ Fixed | Downgraded to Low (impact 2), recommendation notes browsers imply noopener since ~2021. |
| **§3.4** readability false positive (Grade 22) | ✅ Fixed | FK grades >19 (implausible = non-prose artifact) are no longer flagged. |
| **§5** AI reach-hallucination + weak model | ✅ Fixed | Explicit single-page "do not claim multi-page reach" guard in the prompt (verified no reach language); default model upgraded to `llama-3.3-70b-versatile`. |
| **§3.1** score hides severity / **§3.2** schema underweighted | ◑ Noted | Severity IS surfaced (Issues-by-Severity chart + AI stats). Score-capping and schema/alt rebalancing left as product decisions (changing scoring semantics site-wide is higher-risk). |

*Below is the original audit, unchanged, for the record.*

---

## 1. Data flow - how a Result and its Top Issues are built

```
sitemap_extractor.extract_sitemap_urls()      → list of URLs (deduped, filtered, capped)
        │
        ▼  (per URL)
auditor.audit_url()
   ├─ fetch_page()                              → HTML, status, response_time, headers, size
   ├─ analyze_metadata / headings / canonical / indexability / url_structure / content
   ├─ heading_auditor.analyze_heading_structure (heading_detail)
   ├─ image_auditor.analyze_images_advanced      (image_detail)
   ├─ advanced_checks.analyze_advanced           (Performance, Security, Schema, Social…)
   ├─ mobile_auditor.analyze_mobile              (mobile_audit)  ← see BUG #1
   ├─ technical_checks.analyze_site_health       (SSL, DNS, robots, sitemap, readability…)
   └─ course_auditor / blog_auditor              (page-type specific)
        │
        ▼
   all_issues = concat(issues from each block)   ← every issue = {issue, category, severity,
   scoring.calculate_seo_score(result)                             recommendation, impact_score, effort}
        │
        ▼  (frontend)
   lib/aggregate.ts
     ├─ issuesByTitle()      → "Top failing checks (site-wide)"  (severity-first, then page count)
     ├─ worstIssue()         → per-row "Top issue" column         (impact_score-first)
     └─ getThematicIssues()  → SEMrush-style category grouping
   ai_assist.explain_audit() → AI narrative + "top_actions"
```

**Two different "top issue" rankings coexist:**
- Sitewide "Top failing checks" = `issuesByTitle()` → sorted by **severity** (Critical→Low), then page count.
- Per-row "Top issue" column = `worstIssue()` → sorted by **impact_score**.

These can disagree for the same page (a High-severity issue with a low/absent impact_score ranks top in one view, bottom in the other). See BUG #2.

---

## 2. What the audit actually produced (live evidence)

| Page | Type | Score | Issues | Notable |
|---|---|---|---|---|
| `/` (home) | general | **93.1 → 93.8 → 94.0** across 3 runs | 14 | Score not reproducible (BUG #3) |
| `/course/energy-efficiency-training` | course | 92.6 | 15 | `Missing CTA / Enrol Section` (High) |
| `/blog/change-management-frameworks` | blog | 92.2 | 16 | Blog issues have **impact=None** (BUG #2) |

Real top issues, homepage:
```
[Medium ] impact=6 Performance: 38 image(s) missing width/height dimensions
[Warning] impact=6 User Experience: Intrusive popup/modal patterns detected
[Warning] impact=5 Metadata: Meta Description Too Long (182 chars)
[Warning] impact=5 Performance: Missing Cache-Control Header
[Medium ] impact=5 External Links: External Links Missing rel='noopener' (5)
[Low    ] impact=4 Structured Data: No Structured Data Found
[Warning] impact=4 Content: Difficult Readability (Grade 22.2)
```
Real blog page tail (note the `impact=None`):
```
[High   ] impact=None Blog Content: Missing Author Information
[Low    ] impact=None Blog Content: Missing Table of Contents
```

---

## 3. Review as an SEO Manager (content quality & correctness)

### 3.1 The headline score hides severity - everything scores 91–94
Every sampled page - including one with 2 High-severity issues - landed in the 91–94 "green" band. Cause: `scoring.py` computes each category as `100 − Σ penalties`, then **weights** categories heavily toward metadata/content/links. Performance/mobile/security issues live in low-weight buckets (`advanced` = 0.08) or an **unscored** bucket (mobile, BUG #1), so a page can accumulate 14–16 findings and still read "94/100 = excellent." A client reading the number will conclude the site is basically fine when the issue list says otherwise. **Recommend:** surface a severity-weighted "issues" headline (e.g. "2 High, 4 Medium") next to the score, or cap the score when any High/Critical exists.

### 3.2 Severity vs. real SEO impact is inconsistent
- `Missing alt text on 1 image(s)` → **High, impact 7**. One decorative-adjacent image driving a "High" is overweighted.
- `No Structured Data Found` → **Low, impact 4**. For a training company, missing `Course`, `Organization`, `FAQ`, `BreadcrumbList` schema is a major rich-results miss - this is arguably the single biggest *organic-visibility* opportunity on the site and it's rated Low. This is backwards.
- `Multiple H1` → High, but modern Google tolerates multiple H1s; overweighted.

### 3.3 `rel="noopener"` findings are miscategorized and dated
`External/Internal Links Missing rel='noopener'` shows up on every page (Medium, impact 4–5) and is grouped under the **Links** SEO themes. But: (a) it's a **security/perf** micro-nit, **not an SEO ranking factor**, and (b) since 2021 all major browsers imply `noopener` for `target="_blank"` automatically. It inflates the issue count and the "Links" theme with a near-non-issue. **Recommend:** downgrade to Low, recategorize as "Best Practices / Security," or drop.

### 3.4 Readability grade is implausible → false signal
Homepage flagged `Difficult Readability (Grade 22.2)`. The Flesch-Kincaid grade scale tops out realistically around 18 (post-graduate). A 22 means the calculator (`textstat.flesch_kincaid_grade`) is being fed **non-prose** - nav labels, button text, fragmented marketing phrases without sentence punctuation - which explodes average sentence length. On a landing/nav-heavy page this metric is noise, not a real content problem. **Recommend:** only run readability on pages with a genuine article body (blog/course description), and gate on a minimum sentence/word count.

### 3.5 Page-type detection is URL-pattern only - correct call, with a gap
`detect_page_type` classifies by URL substring (`/course/`, `/blog/`). On edstellar this worked (course→course, blog→blog, home→general). But any course/blog page that doesn't match those slugs silently becomes "general" and **skips all page-type checks** (CTA, schema, author, ToC). For a site with a clean URL scheme this is fine; flag it as a known limitation for sites without slug conventions.

### 3.6 What it gets right (credit where due)
- Course page correctly caught `Missing CTA / Enrol Section` - a genuine, high-value conversion finding.
- Blog page correctly caught `Missing Author Information` (E-E-A-T) and `Missing Table of Contents`.
- Duplicate-alt-text, missing width/height (CLS), and TTFB detection are all real, actionable issues.
- Per-issue attribution (which pages tripped an issue) in the sitewide rollup is genuinely useful.

---

## 4. Review as a Developer (logic issues)

### 🔴 BUG #1 - `mobile_audit` issues are shown & counted but **never scored** (High)
`scoring.calculate_seo_score()` builds its breakdown from: metadata, heading_detail, canonical, indexability, url_structure, content, image_detail, `advanced`(+redirect), site_health, course/blog. **`result["mobile_audit"]` is not in that list** (`grep mobile scoring.py` → no matches). Yet `audit_url` appends `mobile_audit` issues to `all_issues`, so they appear in the UI, the counts, the themes, and the AI prompt.

Consequence: every `mobile_auditor` finding contributes **zero** to the SEO score - including `Missing viewport meta tag` (severity **Critical**, impact 9) and `Intrusive popup/modal patterns` (Google mobile-interstitial penalty territory). A fully mobile-broken page can still score 90+. This also makes the score/issue-list inconsistent: the popup finding is literally shown as a top issue on the homepage but moves the score by 0.

### 🔴 BUG #2 - Blog-content issues ship without `impact_score` / `effort` (High)
`blog_auditor.audit_blog_page` builds most issue dicts inline with only `{issue, category, severity, recommendation}` - **no `impact_score`, no `effort`** (contrast the `_issue()` helper used everywhere else). Live proof: `Missing Author Information` (High) and `Missing Table of Contents` came back with `impact=None`.

Consequences:
- `worstIssue()` (`impact_score ?? 0`) sorts a **High-severity** author-missing issue to **impact 0** → it can never be the per-row "Top issue," beaten by any Low with impact 2.
- Missing `effort` → `difficulty.ts` falls back to a keyword guess → these land in "Medium" regardless of reality; the Fix-effort chips undercount.
- `scoring.get_top_issues_by_impact` uses `.get("impact_score", 0)` so it doesn't crash - but only because the key is fully absent. Any code path that reads a present-but-`None` value and compares it would `TypeError`.
Same defect pattern exists for the non-schema/OG blog issues (Published Date, Introduction, Conclusion, etc.).

### 🟠 BUG #3 - Score is not reproducible (TTFB one-shot measurement) (Medium)
The homepage scored **93.1, 93.8, 94.0** on three consecutive runs. Root cause: `advanced_checks` derives TTFB from a single live `response_time` (`resp.elapsed`). Thresholds are hard step-functions: `>500ms → Poor (High, impact 8, −25 perf)`, `>200ms → Needs Improvement (Warning, impact 5, −10 perf)`. Run-to-run network jitter flips the homepage across the 500ms boundary (observed 739ms then 395ms then 359ms), changing severity, the issue text, the top-issue ordering, **and the score**. Two audits of the same unchanged page produce different reports. **Recommend:** take the PageSpeed API TTFB when available (it's already fetched), or median-of-N requests; never a single `elapsed`.

### 🟠 BUG #4 - `suggest_fix` fails live on alt-text (Medium, user-facing)
On the course page, `detect_fix_target("Missing alt text on 1 image(s)")` matches → the UI shows "Suggest a fix" → the call returned `{"ok": false, "error": "The assistant didn't return a usable suggestion."}`. The alt-text instruction asks the model for **multiple lines** ("2–3 example alt-text strings … each on its own line") while `_chat` runs in `json_mode`, and the parse/unwrap path returns empty. So the button is offered but the feature dead-ends. (The `description` fix, by contrast, worked and returned a clean grounded 117-char draft.) **Recommend:** either return the alt examples as a JSON array field, or exclude multi-line targets from the JSON-mode path.

### 🟡 BUG #5 - Two conflicting "top issue" orderings (Low, UX consistency)
As noted in §1, sitewide uses severity-first and per-row uses impact_score-first. Combined with BUG #2's missing impact scores, the same page can show issue A as its "Top issue" while the sitewide panel ranks issue B first. Pick one canonical ranking (severity, then impact, then reach) and reuse it.

### 🟡 BUG #6 - `impact_score` semantics never validated (Low)
Impact scores are hand-assigned integers scattered across 8 modules (0–10), with no central table, no test asserting severity↔impact consistency, and - per BUG #2 - no guarantee the field exists. A single source-of-truth mapping (severity + category → impact) would remove the drift and the None holes.

---

## 5. AI usage review

**Where AI is used (two grounded tasks only - the old chatbot was removed):**
1. `explain_audit` → plain-English summary + prioritized `top_actions`. Issues are deduped by title and annotated with affected-page counts before prompting (`_aggregate_issues`), truncated to 8,000 chars, severity-sorted. Good design - prevents duplicate-flooding and preserves rare-but-severe issues.
2. `suggest_fix` → drafts a concrete replacement (title/description/H1/OG/alt) grounded in the page's real content. Gated by `detect_fix_target` regex.

**Quality observations (from live output):**
- ✅ **Grounded and specific.** The summary referenced real counts ("38 images", "19 links = 14 internal + 5 external"), and the description draft was on-topic and correctly sized. The system prompt's "do not invent" instruction is doing real work.
- 🔴 **Hallucinates multi-page reach on single-page audits.** Both single-page summaries said the issues *"affect multiple pages"* / *"not only affecting one page"* - even though `is_sitewide` was false and `scope_hint` explicitly told the model *"This is a single-page audit."* The 8B model ignores that constraint. Misleading for a per-page report.
- 🟠 **Boilerplate openers.** Every summary starts "Your website has a good overall technical SEO health score of XX/100, but…". Fine once, repetitive across a multi-page report.
- 🟠 **Model tier.** `llama-3.1-8b-instant` is the weakest reasonable choice and is the likely cause of both the reach-hallucination and the alt-text `suggest_fix` failure. For a client-facing narrative, `llama-3.3-70b-versatile` (still free on Groq) or a small paid model would materially improve reliability. (Ties back to your earlier model question - the summarization task is exactly where a stronger model pays off.)
- 🟡 **AI inherits the scoring blind spot.** Because the prompt is built from `all_issues`, and the mobile issues aren't scored (BUG #1), the AI will happily tell the owner to "fix the intrusive popup" while the score says 94 - reinforcing the score/reality gap to the reader.

---

## 6. Prioritized fix list

| # | Severity | Issue | Fix |
|---|---|---|---|
| 1 | 🔴 High | `mobile_audit` not scored (BUG #1) | Add `mobile_audit` (and confirm every `all_issues` source) to `calculate_seo_score` breakdown; add a test asserting `all_issues` sources ⊆ scored sources. |
| 2 | 🔴 High | Blog issues missing `impact_score`/`effort` (BUG #2) | Route all `blog_auditor` (and any inline) issues through the shared `_issue()` helper. |
| 3 | 🟠 Med | Non-reproducible score via TTFB (BUG #3) | Use PSI TTFB when present, else median-of-N; consider smoothing the step thresholds. |
| 4 | 🟠 Med | `suggest_fix` alt-text dead-ends (BUG #4) | Return alt examples as a JSON array field, or drop multi-line targets from json_mode. |
| 5 | 🟠 Med | Score hides severity (§3.1) | Show "N High / N Medium" beside the score; cap score when High/Critical present. |
| 6 | 🟡 Low | Schema underweighted, noopener overweighted (§3.2–3.3) | Rebalance impact/severity; recategorize noopener out of SEO themes. |
| 7 | 🟡 Low | Readability false positives (§3.4) | Gate readability on real article bodies only. |
| 8 | 🟡 Low | AI single-page reach hallucination + weak model (§5) | Add an explicit "these findings are for ONE page; do not claim multi-page reach" guard; upgrade to a 70B-class model for summaries. |
| 9 | 🟡 Low | Dual top-issue orderings (BUG #5/#6) | One canonical ranking; central severity→impact table. |

---

*No code was changed. All findings reproduced against live edstellar.com output on the sampled URLs.*
