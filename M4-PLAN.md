# 🔵 M4 — AI power

> Built on the existing Groq plumbing (`api/ai.py` + `modules/ai_assist.py`,
> `llama-3.3-70b-versatile`, `GROQ_API_KEY` live in prod). Companion to
> [BUILD-PLAN.md](BUILD-PLAN.md).

## Scope decisions (research-driven)
The audited site is a **Webflow blog with no git repo**, so the roadmap's
"AI fix → GitHub PR" (T4.1) doesn't fit — there's nothing to open a PR against.
Reframed accordingly:

- **T4.2 Ask-your-crawl** — ✅ **DONE.** New `answer_crawl_question` in
  `ai_assist.py` + `"ask"` action in `api/ai.py` + `AskCrawlard` component on the
  Results page. A single grounded Groq call answers plain-English questions using
  ONLY the audit's issue digest (strict no-hallucination prompt). No migration,
  no schema change, works on the free browser audits.
- **T4.3 Bulk AI rewrites** + **T4.1 reframed as a "Fix Pack" export** — NEXT.
  One bulk-drafting engine (titles/metas; alt where client data has it) delivered
  as copy-paste-ready snippets via `api/export.py`, mapping to a Webflow editing
  workflow. Must be **chunked through the `Job` queue** (30s function limit; prior
  CPU-overage incident) and cached/deduped by content fingerprint. Reuses the
  existing `suggest_fix` / `content_agent` drafters.
- **T4.1 as a literal GitHub PR** — SKIPPED (no repo). If one-click *apply* is
  ever wanted, target the Webflow API/MCP, not GitHub.
- Optional premium: route rewrites through the Anthropic/Opus path
  (`modules/llm.py` + `content_agent`) when a vaulted `anthropic` key exists.

## Status
T4.2 shipped. T4.3 shipped as a **client-orchestrated AI Fix Pack** (no worker
needed): `components/AiFixPackCard.tsx` bulk-drafts title/description/H1
replacements for affected pages via the existing `fix-suggestion` endpoint
(concurrency 3, 100-page cap) and exports CSV for Webflow paste. Direct
Webflow-MCP write-back remains a future option.
