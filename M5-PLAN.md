# 🔵 M5 — Integrations, reporting & monitoring

> Companion to [BUILD-PLAN.md](BUILD-PLAN.md). Constraint: no always-on worker
> (Railway skipped); crawls are browser-orchestrated + persisted to Neon.

## Status: COMPLETE (all tasks shipped or code-complete)

| Task | State | Notes |
|------|-------|-------|
| T5.3 Internal link-graph viz | ✅ Done | `link-graph` action + `CrawlGraph.tsx` |
| T5.2 Crawl PDF/Excel/CSV/JSON export | ✅ Done | server-side, from Neon rows |
| T5.2 Shareable report links | ✅ Done | public `/share/<token>` |
| T5.4 Scheduled re-crawls (firing) | ✅ Done | `/api/cron` → `enqueue_due_crawls` (ping externally; Vercel built-in cron is plan-gated so it was dropped) |
| T5.4 Regression-alert emails | ✅ Done | on finalize, best-effort |
| T5.4 Unattended crawl execution | ✅ Done (opt-in) | `CRON_EXECUTE_CRAWLS`, resumable chunks |
| T5.1 GSC + GA4 | ✅ Code-complete | activates when user connects Google |

## What shipped

### T5.2 — Crawl export (`api/crawl-export.py`, `worker/crawl_export.py`)
`worker/crawl_export.py` reconstructs the per-page `AuditResult`-shaped dicts
that `modules/report_generator.py` already consumes, from the crawl's persisted
Page/Issue/Link rows — so the exact same CSV/Excel/PDF generators produce a
crawl report (no second reporting path). `api/crawl-export.py` is org-scoped
(same gate as `api/crawls.py`). UI: an **Export ▾** dropdown (CSV/Excel/PDF/JSON)
in the crawl-detail header, gated on a completed crawl.

### T5.2 — Shareable links (`api/share.py`, `Crawl.share_token`)
`setShare`/`revokeShare` actions on `api/crawls.py` mint/clear an unguessable
`Crawl.share_token` (migration `b1c2d3e4f5a6`). `api/share.py` is a **public,
no-auth** read path — the token is the only capability, resolved bypassing
org-scope; supports summary/pages/issues/export. `/share/[token]` is a
read-only page (added to `AppShell` public routes). A **Share report** popover
in the crawl header creates/copies/revokes the link.

### T5.4 — Scheduling, executor, alerts (`api/cron.py`, `worker/cron_runner.py`)
- **On by default:** `GET /api/cron` calls `enqueue_due_crawls()` to FIRE due
  schedules. (Vercel's built-in cron is plan-gated and blocked the build, so the
  `crons` entry was removed — trigger `/api/cron` from any external scheduler,
  e.g. cron-job.org or GitHub Actions, guarded by `CRON_SECRET`.) Plus
  regression-alert emails after
  any crawl finalizes completed (`send_regression_alert` in `finalize_crawl`,
  best-effort, needs an email backend + a prior completed crawl to diff).
- **Opt-in:** `CRON_EXECUTE_CRAWLS=1` enables `cron_runner.process_due()` — a
  resumable, time-bounded server-side executor that runs scheduled crawls in
  chunks across ticks (via `load_resume_state` + `CrawlConfig.max_seconds`).
  Anti-hijack: only claims crawls with `schedule_cron` set, `status=='queued'`,
  `started_at IS NULL` (atomic conditional UPDATE) or `paused` scheduled crawls
  — never browser-orchestrated running crawls. Env: `CRON_SECRET` guards the
  endpoint. This is the no-Railway substitute for the skipped always-on worker;
  cadence/size are bounded by Vercel's cron frequency + function time limit.

### T5.1 — GSC + GA4 (`modules/google_integrations.py`, `api/insights.py`)
Service-account model (no OAuth consent screen), using only `requests` +
`cryptography` (RS256 JWT-bearer token exchange) — no new dependency. GSC
Search Analytics + GA4 `runReport`, joined onto crawl page URLs. Credentials
stored in the existing encrypted vault (providers `gsc`/`ga4`) as a wrapper
JSON `{service_account, site_url|property_id}`, saved from two **Settings**
connect cards. `api/insights.py` (`status`/`gsc`/`ga4`) returns a friendly
not-connected state until configured.

**The one external step (user-provided infra):** create a Google Cloud service
account, enable the Search Console API / Analytics Data API, grant its
`client_email` Viewer access to the GSC property / GA4 property, and paste the
JSON key + site URL / property ID into Settings. Until then GSC/GA4 return the
not-connected state; all other M5 features work with no external setup.

## Deploy notes
- Migration `b1c2d3e4f5a6` (crawls.share_token) must be applied to Neon
  **before** deploying the code that reads it (share links).
- New env (all optional): `CRON_SECRET`, `CRON_EXECUTE_CRAWLS`. GSC/GA4 need
  no env — they use the vault (`VAULT_ENCRYPTION_KEY` already set).
