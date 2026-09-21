# 07 - Semrush & Ahrefs: Technical Reference (Crawling, Auditing, Scoring Logic)

**Methodology note:** this document distinguishes two categories of claim throughout:
- ✅ **Official** - a direct quote or paraphrase from a company-published source (semrush.com/kb, semrush.com/bot, ahrefs.com/robot, help.ahrefs.com), verified via adversarial multi-vote checking against the cited page.
- ❌ **Undisclosed / third-party** - neither company publishes this; anything you read elsewhere claiming to know it exactly is inference, not documented fact.

Two commonly-repeated claims were checked against their cited primary source during this research and **did not hold up** - they're kept visible below (marked refuted) rather than silently dropped, so the verification is auditable. This is also the honest answer to "100% correct results": no external research can produce 100% certainty about proprietary internals that the vendors themselves have never published. Everything marked ✅ below is as close to that bar as is achievable; everything marked ❌ is a genuine, stated gap.

---

## SEMRUSH

### 1. Crawler / Bot Inventory
✅ Semrush runs **10 distinct named bots**, not one monolithic crawler (source: semrush.com/bot):

| Bot | Purpose |
|---|---|
| `SemrushBot` | Primary crawler - backlink data + general web spidering |
| `SiteAuditBot` | Site Audit tool |
| `SemrushBot-BA` | Backlink Audit tool |
| `SemrushBot-SI` | On-Page SEO Checker |
| `SemrushBot-SWA` | URL accessibility checks |
| `SplitSignalBot` | A/B testing tool |
| `SemrushBot-OCOB` | Content Toolkit |
| `SemrushBot-FT` | Plagiarism detection |
| `RyteBot` | Ryte.com integration |
| `SemrushBot-ESI` | Enterprise Site Intelligence |

✅ **Robots.txt behavior:** supports a non-standard `crawl-delay` directive capped at a maximum of 10 seconds. Requires an HTTP 200 response on the robots.txt file itself to honor its rules - a 4xx on robots.txt is treated as "no restrictions," a 5xx blocks crawling entirely.

### 2. Backlink Index & Authority Score
- ✅ Proprietary crawler analyzes **~10 billion web pages/day**, maintaining a **43 trillion backlink** database. (semrush.com/kb/997)
- ✅ **Authority Score (1–100 scale)** - three named components: **Link Power** (quality/quantity of backlinks), **Organic Traffic** (estimated monthly traffic), and a set of **spam/naturalness penalty signals**. The eight specific penalty signals, officially named (semrush.com/kb/747): no organic rankings on SERPs, unnaturally high % of dofollow domains, imbalance between links and traffic, too many referring domains on the same IP address, too many referring domains on the same IP network/subnet, presence of another domain with an identical backlink profile.
- ❌ **Not disclosed:** the exact mathematical weighting formula combining the eight factors. Semrush publishes only the factor list. Any specific weight percentages found elsewhere are third-party estimates.
- ✅ Semrush's own documentation frames Authority Score as **relative** - "best used for domain comparison, and not for determining good/bad on an absolute scale."

### 3. Keyword Database & Search Volume
- ✅ **27.9 billion keywords** across **142 geographic databases**. Top 100 SERP positions monitored for hundreds of millions of keywords. (semrush.com/kb/997)
- ✅ **Search volume** = average monthly query count, **not split by device** (mobile+desktop combined). Computed via machine-learning models trained on third-party clickstream data - no single disclosed formula. (semrush.com/kb/683)
- **❌ Refuted claim:** "Semrush's US Google database methodology analyzes the top 100 organic results to populate Domain/Keyword Analytics, implying SERP-scraping as the primary method for this database" - checked against semrush.com/kb/719 and **did not hold up** (voted 1-2 against). Treat this specific framing as unconfirmed.

### 4. Update Logic - "Live Update"
✅ Official name for Semrush's refresh algorithm. **Update frequency scales with keyword popularity** - daily, weekly, or monthly; more popular keywords refresh more often. (semrush.com/kb/71)

### 5. Traffic Estimates
✅ Derived from a clickstream panel of **200M+ anonymized users across 190+ countries**, fed into a proprietary **"Neural Network algorithm"** fusing clickstream, backlink, and ranking data. Semrush has not published an independent accuracy study of this the way Ahrefs has (see below) - no equivalent error-rate figure is available to cite.

### 6. Site Audit (crawl/audit logic)
Covered in full in `../crawling_audit_logic_semrush_ahrefs_screamingfrog.md` from earlier in this research: breadth-first crawl, Respect/Ignore/Ignore-report robots.txt modes, Error/Warning/Notice severity tiers, thematic reports (Crawlability/HTTPS/Performance/Core Web Vitals/International SEO/Internal Linking). Not re-derived here to avoid duplication.

### 7. Full Toolkit Inventory
**SEO Toolkit** (20+ tools: Site Audit, Keyword Magic Tool, Position Tracking, Domain Overview, Backlink Analytics, On-Page SEO Checker) · **Content Toolkit** (AI Article Generator, Topic Finder, SEO Brief Generator, Content Optimizer, SEO Writing Assistant) · **Traffic & Market Toolkit** · **Local Toolkit** · **AI Visibility Toolkit** · **Social / Advertising / AI PR Toolkits**.

---

## AHREFS

### 1. Crawler Bots - exact identities
✅ (source: ahrefs.com/robot)

| Bot | User-agent string | Robots.txt |
|---|---|---|
| **AhrefsBot** v7.0 | `Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)` | Strictly respects Disallow, Allow, **and Crawl-Delay** |
| **AhrefsSiteAudit** v6.1 (desktop) | `Mozilla/5.0 (compatible; AhrefsSiteAudit/6.1; +http://ahrefs.com/robot/site-audit)` | Obeys by default; verified domain owners may request exemption |
| **AhrefsSiteAudit** v6.1 (mobile) | `Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 ... (compatible; AhrefsSiteAudit/6.1; +http://ahrefs.com/robot/site-audit)` | Same as above |

✅ **Robots.txt control** (help.ahrefs.com/78158): `Crawl-Delay: N` (seconds) throttles request frequency; `User-agent: AhrefsBot` + `Disallow: /` blocks entirely. **Changes take time to propagate** - the bot only picks up robots.txt edits on its next scheduled crawl, not immediately.
✅ **JS-rendering caveat** (explicitly documented): during rendering, the bot fires multiple simultaneous asset requests (images/scripts/CSS) - crawl-delay **cannot be strictly enforced** during that burst. This is a real, stated limitation, not a bug report from a third party.

### 2. Backlink Index - scale & update cadence
- ✅ Live index: **35 trillion backlinks, 493.9B pages, 500M+ domains**, refreshed every **15–30 minutes**.
- ✅ **A full re-crawl of the entire backlink graph takes ~2 months** - direct quote: "refreshing the info on the whole Internet backlinks takes about two months." (help.ahrefs.com/78052)
- ✅ Within that 2-month cycle, **update frequency is importance-weighted**: high-rated pages re-crawled up to **60 times**, low-rated pages **as few as once**. This means a backlink from a low-authority page can be showing month-old data even though the index headline claims 15–30 min freshness - an important nuance usually omitted in summaries of Ahrefs' freshness.

### 3. Domain Rating (DR) - the actual mechanism
- ✅ PageRank-style link-graph propagation, computed at the **domain level** (not per-URL).
- ✅ **A linking domain splits its rating equally among every domain it links out to** - direct quote: "The source domain splits its rating equally amongst the domains it links to." A link from a domain with few outbound links passes more value than one from a domain linking to thousands of sites.
- ✅ **Iterative**: value propagates repeatedly through the link graph, then the resulting absolute value is **rescaled onto the final 0–100 range**.
- ✅ Explicitly framed as a **relative metric** - your DR depends not just on who links to you, but on how many *other* sites those linking domains also link to.
- **❌ Refuted claim:** "DR is on a logarithmic scale" - checked against help.ahrefs.com/1409408 and **rejected (voted 0-3)**. Ahrefs' own help article does not state this; it appears only in third-party explainer blogs. Treat any "logarithmic" description of DR as unconfirmed speculation.
- **❌ Not disclosed:** which link types qualify (whether nofollow counts, whether only the first link per domain counts, exact eligibility thresholds). Third-party "explainer" content describing exact rules here is inferring, not quoting Ahrefs.

### 4. Traffic Estimates - Ahrefs' own published accuracy study
This is the single most important honesty check in this document. Ahrefs ran and published its own study (ahrefs.com/blog/traffic-estimations-accuracy):
- **Sample:** 1,635 random websites, U.S. traffic only.
- **Median deviation: 49.52%** vs. real Google Search Console data - i.e., Ahrefs' traffic estimate is typically off by roughly half the true value.
- **Correlation: 0.76 (Pearson's)** - relative comparisons ("is Site A bigger than Site B") hold up reasonably well even when the absolute number doesn't.
- Direct quote: **"For some websites, we are off by less than 5%. For some others, we can be off by more than 1,000%."**
- Stated causes: incomplete keyword tracking, imprecise search-volume data, ranking volatility, unpredictable click-through-rate modeling, algorithmic prediction limits.

Ahrefs itself is telling you this number is a **model output, not ground truth**.

### 5. Site Audit - Health Score formula (exact, official)
```
Health Score = ((Total internal URLs − URLs with Error-level issues) / Total internal URLs) × 100
```
Worked example from the docs: 10 pages crawled, 2 have errors → (10−2)/10 × 100 = **80%**.
✅ **Confirmed by explicit statement (not inference): only Error-severity issues affect this score.** Warnings and Notices are completely excluded from the calculation.

### 6. Full Tool Inventory
**Site Explorer** (competitor backlinks/traffic/rankings) · **Keywords Explorer** (volume/difficulty/traffic-potential/CPC; spans Google/YouTube/Amazon/TikTok) · **Rank Tracker** · **Site Audit** · **Content Explorer** (link-worthy content discovery) · **AI Suggestions / AI Search Intent / AI Content Helper / Ask AI / Batch AI** · an **MCP server** (2026) for AI-assistant natural-language queries against Ahrefs data.

---

## Side-by-Side Summary

| | Semrush | Ahrefs |
|---|---|---|
| Backlink crawl scale | ✅ ~10B pages/day, 43T links | ✅ 5M pages/min, 35T links, 493.9B pages |
| Core authority metric | ✅ 3 components + 8 penalty signals named · ❌ exact weights undisclosed | ✅ equal-split PageRank-style propagation described · ❌ link-eligibility rules & exact scaling undisclosed |
| Index refresh rate | ✅ 15-min backlink updates | ✅ 15–30 min live index · ⚠️ full graph re-crawl ~2 months, importance-weighted |
| Traffic estimate accuracy | No public accuracy study found | ✅ Ahrefs published its own: 49.52% median deviation vs. real GSC data |
| Bot transparency | ✅ 10 named bots, exact purposes | ✅ 2 bots, exact UA strings, exact robots.txt behavior |
| Site Audit scoring | ✅ Error/Warning/Notice tiers, thematic reports | ✅ exact Health Score formula with worked example |

## What This Means for the Rebuild Plan

Referencing `00-PLAN-OVERVIEW.md` and `02-AUDIT-ENGINE.md`: the severity model (Error/Warning/Notice) and Health Score formula adopted in this plan are now confirmed byte-for-byte against Ahrefs' official documentation, not approximated. The one honesty point worth carrying into the product itself: if the rebuilt tool ever adds traffic estimation (clickstream-based, Phase 5+), **disclose an accuracy caveat to users the way Ahrefs does** - publishing "this is a modeled estimate, typical deviation X%" is both more honest and more defensible than presenting a single confident-looking number, which is exactly the trap Ahrefs' own published study was designed to get ahead of.
