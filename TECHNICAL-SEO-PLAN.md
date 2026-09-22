# 🔧 Technical SEO — Correctness Plan

> Focus: **technical SEO only.** Goal: stop flagging correctly-built pages
> (false positives), fix a few real detection bugs, add the high-value checks
> that a best-in-class auditor (Screaming Frog / current Google guidance) has,
> and make every technical issue jump to the exact place. Local planning doc —
> **nothing is built or deployed until each phase is approved.**
> Companion to [BUILD-PLAN.md](BUILD-PLAN.md), [SCREAMING-FROG-WORKFLOW.md](SCREAMING-FROG-WORKFLOW.md).

## Root cause (why "everything I built correctly still shows issues")
Research confirmed the `rel`/`target`/`nofollow` **parser is correct** — a single
robust parser ([link_auditor.py:285-306](modules/link_auditor.py:285)) used by every
path (single-URL, bulk, live worker crawl). Your attributes are read right.

The problem is the **rules**: many checks are legacy/opinionated and fire on
markup that is actually correct by 2024-2026 Google standards. Google treats most
on-page signals as **hints, not directives**, and rewards user value over
mechanical compliance. Our auditor currently emits advisory/obsolete concerns at
Warning/High/Medium severity, so a clean blog looks "broken."

## The design rule that fixes it (applies to every check)
Every check gets three things:
1. **Severity tier that matches Google's own model**
   - **Critical** = breaks indexing/ranking/rendering (noindex on a money page,
     canonical→404, redirect loop, active mixed content).
   - **Warning** = a real hint worth acting on (duplicate titles, broken links,
     missing required schema property).
   - **Notice** = advisory/cosmetic/security-hardening (noopener, length
     overages, HSTS, multiple H1). Shown, but never counted as a "problem" that
     drags the score or alarms the user.
2. **Intent awareness** — don't flag `noindex`/`disallow`/short-content/no-H2 on
   page types where it's correct (thank-you, cart, search, filter, landing).
3. **Rendering awareness (later, M2/M3)** — render JS before declaring orphan
   pages / missing links / missing content, the largest false-positive class.

---

# Phase 1 — Kill the false positives + recalibrate severity  *(the headline fix; small effort; works on existing data)*
Directly fixes "correct pages still show technical issues." No new crawling.

### 1A. Link checks ([modules/link_auditor.py](modules/link_auditor.py), [lib/linkAnalysis.ts](lib/linkAnalysis.ts))
| Check | Where | Change |
|---|---|---|
| **"Very High Dofollow External Link Count (>50)"** | [link_auditor.py:934](modules/link_auditor.py:934) | **Remove.** Dofollow links are normal/desirable; advising `nofollow` on editorial links is wrong. |
| **"Opening in New Tab Without rel='noopener'"** (internal + external) | [link_auditor.py:860](modules/link_auditor.py:860), [:926](modules/link_auditor.py:926) | **Downgrade to Notice** (or drop). Browser-implied since 2021; not SEO. |
| **Frontend security-gap requires `noreferrer` too** | [linkAnalysis.ts:186](lib/linkAnalysis.ts:186), [:347](lib/linkAnalysis.ts:347) | Require only `noopener`; make it a **Notice**, not Medium. A correct `noopener`-only link must not be flagged. |
| **Severity mismatch** (Low in Py, Medium in TS) | [link_auditor.py:866](modules/link_auditor.py:866) vs [linkAnalysis.ts:351](lib/linkAnalysis.ts:351) | Make both **Notice**; single source of truth. |
| **NEW real check: paid/affiliate link missing `sponsored`** | link_auditor | **Add (Warning).** Follow links to obvious ad/affiliate destinations without `rel="sponsored"` is a genuine Google-policy risk. |

### 1B. Metadata / URL / content ([modules/auditor.py](modules/auditor.py))
| Check | Where | Change |
|---|---|---|
| Meta description "too short (<150)" | [auditor.py:273](modules/auditor.py:273) (`MIN_DESC_LEN=150`) | **Remove the minimum.** No Google minimum. Keep only "missing" (→ Warning, not Critical) and duplicate. |
| Meta title "too short (<30)" | [auditor.py:253](modules/auditor.py:253) | **Notice/advisory** only. |
| Title/description over length | [auditor.py:258](modules/auditor.py:258) | **Notice**, pixel-based "may truncate," never error. |
| Missing meta description severity | [auditor.py:269](modules/auditor.py:269) (Critical) | **Downgrade to Warning.** |
| Missing canonical | [auditor.py:365](modules/auditor.py:365) (Warning) | **Notice.** Google self-canonicalizes; not an error. |
| **Canonical points to different URL** | [auditor.py:389](modules/auditor.py:389) | **Rewrite** (see Phase 3): only flag canonical→redirect/4xx/5xx/noindex/blocked, not legitimate cross-URL canonicals. |
| Thin content <300 = High; <600 = Warning | [auditor.py:506](modules/auditor.py:506) | **Downgrade** to Warning/Notice; exempt utility page types; word count isn't a ranking factor. |
| Low content-to-HTML ratio (<10%) | [auditor.py:517](modules/auditor.py:517) | **Remove.** Legacy, non-signal. |
| URL contains query params | [auditor.py:470](modules/auditor.py:470) | **Remove** (or Notice). Query strings are normal/crawlable. |
| URL too long (>115) / uppercase | [auditor.py:458](modules/auditor.py:458), [:464](modules/auditor.py:464) | **Notice.** Not ranking factors. |
| Missing Open Graph | [auditor.py:293](modules/auditor.py:293) (Medium) | **Notice.** Social only, no ranking impact. |
| Page set to noindex | [auditor.py:414](modules/auditor.py:414) (Critical) | Keep surfacing, but **add intent hedge** ("verify intentional") like the sibling nofollow check; only Critical when on a canonical/linked page (Phase 3 intent classifier). |

### 1C. Headings ([modules/heading_auditor.py](modules/heading_auditor.py))
| Check | Where | Change |
|---|---|---|
| Multiple H1 = High | [heading_auditor.py:225](modules/heading_auditor.py:225) | **Notice.** Valid in HTML5; Google is fine with it. |
| No H2 despite H1 | [heading_auditor.py:339](modules/heading_auditor.py:339) | **Notice.** Short valid pages are fine. |
| Duplicate H2/H3 | [heading_auditor.py:311](modules/heading_auditor.py:311) | **Notice.** Repeated section subheads are normal. |
| Skipped heading levels (one issue per skip) | [heading_auditor.py:270](modules/heading_auditor.py:270) | **Notice**, and **group into one** finding (a11y, not SEO). |

### 1D. Advanced / mobile / technical
| Check | Where | Change |
|---|---|---|
| "Intrusive popup/modal detected" | [mobile_auditor.py:301](modules/mobile_auditor.py:301), [:622](modules/mobile_auditor.py:622) | **Remove or Notice.** Static markup matches every Bootstrap modal / cookie banner; can't detect true on-load interstitials. |
| Missing Cache-Control | [advanced_checks.py:40](modules/advanced_checks.py:40) | **Notice/remove.** Dynamic HTML correctly omits it. |
| Missing Twitter Card | [advanced_checks.py:592](modules/advanced_checks.py:592) | **Notice.** Falls back to OG. |
| Missing HSTS / X-Frame-Options | [advanced_checks.py:77](modules/advanced_checks.py:77), [:95](modules/advanced_checks.py:95) | **Notice.** Security hygiene, not SEO. |
| Missing BreadcrumbList schema | [advanced_checks.py:665](modules/advanced_checks.py:665) | **Notice/context.** Not every page has a breadcrumb trail. |
| Page contains iframe(s) | [advanced_checks.py:240](modules/advanced_checks.py:240) | **Remove/Notice.** Embeds are normal. |
| Content "N months old" | [technical_checks.py:484](modules/technical_checks.py:484) | **Notice.** Evergreen content is fine; Last-Modified unreliable. |
| Readability grade >14 | [technical_checks.py:418](modules/technical_checks.py:418) | **Notice.** B2B/technical copy is legitimately higher; not a ranking factor. |
| Image missing lazy loading | [image_auditor.py:555](modules/image_auditor.py:555) | **Notice**, and **never** flag the above-the-fold/LCP image (lazy-loading it hurts LCP). |
| Image >200KB = High | [image_auditor.py:610](modules/image_auditor.py:610) | **Warning**, raise threshold (e.g. 300KB) or scale by role. |
| Image missing width/height | [image_auditor.py:566](modules/image_auditor.py:566) | Keep (**Warning** — real CLS), accept CSS `aspect-ratio` as satisfying it. |

### 1E. Align the checklist mirrors ([modules/technical_audit_checklist.py](modules/technical_audit_checklist.py), [lib/checklistDefs.ts](lib/checklistDefs.ts))
Update the pass/warn/fail thresholds so they don't re-assert the old opinions:
title 30-60, desc 150-160, "self-referencing canonical required", word-count
fail<300, "exactly one H1", favicon-required. These compound Phase 1A-1D.

### Phase 1 acceptance
A correctly-built blog (new-tab links with rel, correct dofollow/nofollow,
reasonable-length title/desc, one topical H1, decorative `alt=""`) produces
**zero Warning/Critical technical issues** — only Notices where relevant. Every
downgrade is justified by a cited source (Google/SF).

---

# Phase 2 — Fix real detection bugs  *(small)*
- **`ugc` counted as dofollow**: [link_auditor.py:306](modules/link_auditor.py:306)
  `is_dofollow = not (is_nofollow or is_sponsored)` — add `is_ugc`.
- **One severity source of truth**: make the frontend derive severity from the
  backend `severity` field instead of re-hardcoding it ([linkAnalysis.ts:347](lib/linkAnalysis.ts:347)).
- Add regression tests for each recalibrated check (assert severity/absence on a
  correct-markup fixture) so false positives can't creep back.

---

# Phase 3 — Add the high-value checks that actually matter  *(the correctness upgrade)*
These are the checks a best-in-class technical auditor has; ordered by impact.
(3.1-3.4 work per-page now; 3.5-3.7 need whole-crawl context → align with M2/M3.)

> **Status (shipped):** the self-contained, per-page parts are DONE —
> ✅ noindex+disallow conflict, ✅ noindex+cross-canonical contradiction,
> ✅ redirect chains **and loops** (single http→https 301 correctly not flagged),
> ✅ hreflang validity (invalid codes, non-absolute URLs, hreflang/canonical
> conflict; missing x-default → Notice), ✅ structured-data required-vs-recommended
> (+ `@graph` detection fix), ✅ Core Web Vitals **field/CrUX** data (LCP/INP/CLS at
> p75; INP not FID) returned by the backend.
> **Deferred to M2/M3 (need the whole-crawl graph or a network fetch of other
> URLs):** canonical/hreflang **target status** (→404/redirect/noindex), hreflang
> **reciprocity**, orphan pages, crawl depth, Link Score, sitemap↔index alignment.
> **Follow-up (UI):** surface the new field-CWV data in the Performance view.

1. **Canonical integrity** — flag canonical → redirect / 4xx / 5xx / `noindex` /
   robots-blocked URL, and **canonical + noindex on the same page** (contradiction).
   Replaces the naive "different URL" check.
2. **`noindex` + `disallow` conflict** — page is `noindex` but also robots-blocked,
   so Google never sees the noindex → stays indexed. Silent, high-impact.
3. **Redirect health** — redirect **chains** (2+ hops) and **loops**, redirect →
   404/410, internal links pointing at a redirecting URL. **Never** flag a clean
   single http→https or non-www 301 (current false positive to avoid).
4. **Structured-data: required vs recommended** — split Rich Results **errors**
   (missing required property → not eligible) from **warnings** (recommended).
   Only errors are Warning+; warnings are Notice.
5. **Hreflang correctness** (multiregional) — missing return/reciprocal tags,
   invalid language/region codes, hreflang → redirect/404/noindex, and
   **hreflang/canonical conflict** (the cluster-breaker).
6. **Core Web Vitals from field data** — LCP ≤2.5s, **INP ≤200ms** (INP replaced
   FID in 2024 — drop FID), CLS ≤0.1, at p75 from CrUX/PSI; surface the specific
   culprit (lazy LCP, missing dims, render-blocking JS). Use **field**, not lab
   score, as the verdict.
7. **Sitemap ↔ index alignment & internal equity** *(whole-crawl, M3)* — sitemap
   URLs that are non-200/noindex/canonicalized-away/redirected; **orphan pages**;
   excessive **crawl depth**; **Link Score** (internal PageRank 0-100).

---

# Phase 4 — "Jump to exactly where" for technical issues  *(UX; builds on existing LOCATE)*

> **Status (shipped):** ✅ Every issue's `affected` element now carries the issue
> CATEGORY through the Locate control, so a technical finding routes to the
> **Technical** tab (not misrouted to Links by value shape) and rings the exact
> row via `data-locate-value` anchors on the canonical, meta-robots, hreflang
> (lang + url), redirect-hop and schema-type elements — including non-URL values
> like a hreflang code. Added an **Indexability & Canonical** card to the
> Technical tab. ✅ The Performance view now renders the **field (CrUX) CWV**
> card (LCP/INP/CLS/FCP/TTFB p75 with good/needs-improvement/poor) from the
> Phase-3 backend data. Redirect **hop statuses** remain future work (we capture
> the hop URLs, not each hop's status code).
Extend the affected-element + LOCATE pattern (already live for images/links) to
every technical issue so clicking a finding lands on the exact place:
- Canonical / robots / hreflang issues → jump to the **Technical** tab and
  highlight the offending `<link>` / `<meta>` / header value.
- Redirect chain → show the **hop sequence** url→url→url with statuses.
- Structured-data error → show the **JSON-LD block** + the failing property.
- Each finding: **"Open live page"** (deep-linked) + **"View source line."**

---

## What is already correct — do NOT touch
Research confirmed these were previously hardened and are right: empty `alt=""`
as Low/"verify decorative"; charset via HTTP header; mixed-content ignoring
`rel=canonical`; HTTPS via `final_url`; favicon "not declared" (Low);
canonical-chain verified-hop guard; SPF/DMARC/MX informational-only; no
meta-keywords check; `<picture>`/`<source>` exemption from alt/dims/lazy;
readability >19 artifact guard; www-redirect graceful degrade.

## Suggested order
**Phase 1 first** (fixes your complaint immediately, small, existing data) →
**Phase 2** (bug fixes + tests) → **Phase 3.1-3.4** (per-page correctness upgrade)
→ **Phase 4** (jump-to-where) → **Phase 3.5-3.7** with M2/M3 (whole-crawl).

## Effort snapshot
| Phase | Effort | Delivers |
|---|---|---|
| 1 | S–M | correct blog = clean report (false positives gone) |
| 2 | S | detection bugs fixed + regression tests |
| 3.1–3.4 | M | canonical/robots/redirect/schema/hreflang/CWV correctness |
| 4 | M | jump-to-exact-element for every technical issue |
| 3.5–3.7 | L | orphans, crawl depth, Link Score, sitemap alignment (needs M2/M3) |
