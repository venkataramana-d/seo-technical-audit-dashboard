# 🔍 SEO Technical Audit Dashboard

An enterprise-grade SEO technical auditing tool — inspired by SEMrush, Ahrefs,
Ubersuggest, and "SEO Meta in 1 Click" — built as a **Next.js** frontend with
**Python serverless functions** on **Vercel**, backed by a **Neon Postgres**
database, with **accounts, roles, and password reset** (email via Gmail SMTP).

> **Live app:** https://seo-technical-audit-dashboard-venkat-ramana.vercel.app
> **Repo:** https://github.com/venkataramana-d/seo-technical-audit-dashboard

This README is intended to be **self-contained**: reading only this file should
be enough to understand what the project is, how it is built, what external
services it uses, how to run it locally, how it deploys, and everything that has
changed. For deep engineering notes see [`agents.md`](agents.md); for the
session-by-session history see [`PROJECT_LOG.md`](PROJECT_LOG.md).

---

## 1. What it is

You enter a URL (or a sitemap / a crawl seed / a list of URLs) and the app runs
a full technical SEO audit — metadata, headings, canonical, indexability, links,
content, images, structured data, mobile-friendliness, site health (SSL, DNS,
robots, sitemap), performance (PageSpeed), and page-type-specific checks
(course / blog) — then scores it 0–100 across weighted categories and gives
prioritized, explained fixes. Results can be exported to CSV / Excel / PDF / JSON.

It is a **multi-user tool**: a single shared workspace with an **admin** and any
number of **user** accounts, login/logout, self-service password change, and
**password reset** (emailed reset links, with an admin-resolved fallback).

---

## 2. Architecture

```
                        ┌──────────────────────────────────────────┐
   Browser (user)  ───► │  Next.js frontend (App Router, React 19)  │  ← Vercel
                        │  app/*, components/*, lib/*               │
                        └───────────────┬──────────────────────────┘
                                        │  fetch /api/*  (same origin)
                        ┌───────────────▼──────────────────────────┐
                        │  Python serverless functions  api/*.py    │  ← Vercel
                        │  (audit engine, auth, export, AI, crawls) │
                        └───────┬───────────────────────┬──────────┘
                                │                        │
                     ┌──────────▼─────────┐   ┌──────────▼──────────┐
                     │  Neon Postgres     │   │  External services  │
                     │  (users, orgs,     │   │  PageSpeed, Groq,   │
                     │   crawls, vault…)  │   │  Gmail SMTP, WHOIS  │
                     └────────────────────┘   └─────────────────────┘
```

- **Frontend** and **Python `/api` functions** are both hosted on **Vercel**.
  The Python functions follow Vercel's runtime convention (each file exports a
  `handler`); dispatch is by an `"action"` field in the JSON body.
- **Bulk / site-wide audits are browser-orchestrated**: the browser tab fans out
  one serverless invocation per URL (chunks of 200) so no single function
  exceeds Vercel's timeout, with resumable IndexedDB checkpoints. (A separate
  always-on **worker** + GitHub Actions batch runner exist in `worker/` for true
  large-scale server-side crawling — see [§9](#9-large-scale-crawling-worker).)
- **Auth** is stateless: scrypt password hashing + HMAC-signed session cookies
  (no external auth library). Every org-scoped query derives its `org_id` from
  the session, never from the client.

---

## 3. Tech stack & external services ("site tools")

### Hosting / infrastructure
| Service | Purpose | Required? |
|---|---|---|
| **Vercel** | Hosts the Next.js frontend **and** the Python `/api` functions; auto-deploys on every push to GitHub `main` | ✅ Yes |
| **Neon** (Postgres) | Database: users, orgs, memberships, projects, crawls, pages, links, issues, job queue, encrypted API-key vault, password-reset requests | ✅ Yes (connected via the Vercel↔Neon integration) |
| **GitHub** | Source of truth; push to `main` triggers the Vercel deploy | ✅ Yes |

### External APIs (all optional, all keyed)
| Service | Used for | Env var | Cost |
|---|---|---|---|
| **Gmail SMTP** | Sends password-reset emails | `SMTP_USER`, `SMTP_PASSWORD` (Google **App Password**), `SMTP_FROM` | Free |
| **Groq** | AI health summary + fix suggestions (`llama-3.x`) | `GROQ_API_KEY` | Free tier |
| **Google PageSpeed Insights** | Performance / Core Web Vitals | `PSI_API_KEY` (works on anonymous quota without it) | Free |
| **Anthropic** | AI content drafts ("ask your crawl" / content agent) | vaulted `anthropic` key (optional) | Paid |
| **Resend** | Alternative email backend (if not using Gmail SMTP) | `RESEND_API_KEY`, `RESEND_FROM` | Free tier |
| **WHOIS / DNS / SSL / HTTP2** | Domain age, SPF/DMARC/MX, cert expiry, protocol | none (network calls) | Free |

### Key libraries
| Library | Purpose |
|---|---|
| [Next.js 16](https://nextjs.org) (App Router, TypeScript, Tailwind v4) | Frontend |
| [React 19](https://react.dev) | UI |
| [Recharts](https://recharts.org) | Charts (dashboard, radar) |
| [DOMPurify](https://github.com/cure53/DOMPurify) | Sanitizes scraped HTML in the body-content preview (XSS defense) |
| [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) + [lxml](https://lxml.de) + [cssselect](https://pypi.org/project/cssselect/) | HTML/XML parsing + CSS-selector extraction |
| [Requests](https://requests.readthedocs.io) / [httpx](https://www.python-httpx.org) | HTTP crawling / HTTP/2 detection |
| [Pandas](https://pandas.pydata.org), [XlsxWriter](https://xlsxwriter.readthedocs.io), [fpdf2](https://pyfpdf.github.io/fpdf2/) | Data processing + Excel/PDF export |
| [textstat](https://github.com/textstat/textstat) | Readability scoring |
| [python-whois](https://github.com/richardpenman/whois), [dnspython](https://www.dnspython.org) | Domain age, SPF/DMARC/MX |
| [SQLAlchemy 2](https://www.sqlalchemy.org) + [Alembic](https://alembic.sqlalchemy.org) | ORM + migrations |
| [psycopg2](https://www.psycopg.org) | Postgres driver (Neon in prod; SQLite locally) |
| [Playwright](https://playwright.dev) | Optional JS rendering of pages |
| [cryptography (Fernet)](https://cryptography.io) | Encrypts the API-key vault |

---

## 4. Features

### Core audit checks
Metadata (title/description/OG), Heading hierarchy (H1–H6), Canonical,
Indexability (noindex/robots), URL structure, Content quality (word count, thin
content, ratio), Image SEO (alt text), Redirect chains.

### Link auditing
Unified internal + external link table (type, follow, health, HTTP status, DOM
location), per-link issue explanations (what/why/impact/fix), priority scoring,
duplicate-anchor detection, missing `noopener`/`noreferrer` detection, bulk
export/copy/open. The **body-content preview** renders a page's real paragraphs
with links highlighted — **sanitized with DOMPurify** before rendering.

### Advanced technical checks
SERP preview, social-card preview, JSON-LD schema detection, mobile-friendliness
(viewport), charset, hreflang, Twitter cards, favicon, cross-URL duplicate
title/description/H1 detection.

### Site health
Domain age (WHOIS), SSL expiry, DNS (SPF/DMARC/MX — informational only),
robots.txt, sitemap.xml validity, readability, content freshness, canonical-loop
detection, www/non-www consistency, HTTP/2 support.

### Page-type specific
Course-page audit (required sections, conversion elements, Course schema),
blog-page audit (author/date/category/Article schema/OG), auto-detection.

### Scoring & recommendations
Weighted 0–100 SEO Health Score across 12 categories, per-issue impact (1–10)
and fix difficulty (Easy/Medium/Hard), top issues by impact, SEMrush-style
thematic grouping, radar chart, curated "common issues" knowledge base with
"Learn more" expansions.

### Export
CSV & JSON in-browser; Excel & PDF server-side (gzip-compressed request to stay
under Vercel's body limit). Export requires sign-in.

### 🔐 Accounts, roles & password reset (multi-user)
- **Single shared workspace.** The designated `ADMIN_EMAIL` is the **admin**
  (owns the workspace); every other signup joins as a **user**.
- **Login / signup / logout**, self-service **Change password** (Settings).
- **Show/hide password toggle** on every password field.
- **Forgot password**:
  - If email is configured (Gmail SMTP / Resend) → the user gets an **emailed
    reset link** (`/reset?token=…`, 1-hour expiry, single-use, hashed token).
  - Otherwise → the request goes to the **admin portal**, where the admin issues
    a one-time temporary password (no email service needed).
- **Admin portal** (`/admin`, admin-only): list users + roles, reset any user's
  password, and view/resolve pending reset requests.
- The header shows a **display name derived from the email** (e.g.
  `venkat.r@edstellar.com` → "Venkat R"), not the raw email.

---

## 5. Environment variables

Set these in **Vercel → Settings → Environment Variables** (production) and in a
local **`.env.local`** (gitignored) for `vercel dev`.

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | ✅ (prod) | Neon Postgres connection string. Provided automatically by the Vercel↔Neon integration. Local dev falls back to a SQLite file if unset. |
| `AUTH_SECRET` | ✅ (prod) | ≥16-char secret for signing session cookies. **The app fails closed without it on any real deploy.** |
| `VAULT_ENCRYPTION_KEY` | ⚠️ if using the in-app API-key vault | Fernet key encrypting provider keys stored via Settings. Must be identical across environments that share the same DB. |
| `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | ⚠️ for emailed resets | Gmail address + a Google **App Password** (2-Step Verification required; no spaces) + the "from" address. `SMTP_HOST` (`smtp.gmail.com`) / `SMTP_PORT` (`587`) are optional overrides. |
| `APP_BASE_URL` | ⚠️ recommended | Public origin used to build reset links (else derived from request headers). |
| `GROQ_API_KEY` | optional | Enables AI summary / fix suggestions by default. Users can also paste their own key in Settings. |
| `PSI_API_KEY` | optional | Raises the PageSpeed quota (works anonymously without it). |
| `RESEND_API_KEY` / `RESEND_FROM` | optional | Alternative email backend if not using Gmail SMTP. |
| `ADMIN_EMAIL` | optional | Overrides the workspace admin email (default is a hard-coded company address). Set this explicitly in production. |
| `NEXT_PUBLIC_SITE_URL` | optional | Overrides the canonical site URL used in page metadata / OpenGraph. |

> 🔒 **Never commit secrets.** `.env.local`, `API Keys.txt`, and `*.secrets`
> are gitignored. If a key is ever exposed, rotate it.

---

## 6. Running locally

```bash
git clone https://github.com/venkataramana-d/seo-technical-audit-dashboard.git
cd seo-technical-audit-dashboard

# Frontend deps
npm install

# Python backend (3.11–3.14) in a venv
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
pip install -r requirements-dev.txt   # for tests
python -m playwright install chromium # one-time, only if you use JS rendering
```

### Option A — Frontend only (`next dev`)
```bash
npm run dev            # http://localhost:3000
```
The Python `/api/*.py` functions **do not run** under plain `next dev` (they need
Vercel's runtime), so API calls return 404 — expected for UI-only work. The app
stays usable (auth treats the backend as "unavailable" and doesn't gate).

### Option B — Full stack (`vercel dev`)
```bash
npx vercel login              # one-time, interactive (browser)
npx vercel link               # link this folder to the Vercel project
npx vercel env pull .env.local # pull DATABASE_URL + keys locally
npx vercel dev                # runs frontend + Python /api together
```

### Backend tests
```bash
.venv\Scripts\python -m pytest -q     # ~380 tests
```

### Database migrations
```bash
# Local SQLite (default when DATABASE_URL is unset):
python -m alembic upgrade head
# Against Neon (use the UNPOOLED/direct connection string):
DATABASE_URL="postgresql://…neon.tech/neondb?sslmode=require" python -m alembic upgrade head
```
> Migrations are **not** run by the Vercel build — run `alembic upgrade head`
> against Neon manually after adding a migration.

---

## 7. Deployment

1. Push to GitHub `main` → **Vercel auto-deploys** (frontend + Python functions).
2. Ensure the env vars in [§5](#5-environment-variables) are set in Vercel.
3. Run any new Alembic migrations against Neon (see above).
4. **Deployment Protection** (Vercel → Settings → Deployment Protection) is
   **off** so the site is publicly reachable.

CI: [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs frontend
(`tsc`, `eslint`, `vitest`, `next build`) and backend (`pytest`) checks on push/PR.

---

## 8. Project structure

```
├── app/                     # Next.js App Router pages
│   ├── login/  reset/        #   auth + emailed-reset landing
│   ├── admin/                #   admin portal (users, resets) — admin only
│   ├── settings/             #   change password, theme, API-key vault
│   ├── technical-audit/      #   run single / sitemap / crawl / CSV audits
│   ├── results/  detail/     #   results list + per-URL drill-down
│   └── site-crawls/          #   saved server-side crawls
├── api/                     # Vercel Python serverless functions
│   ├── auth.py               #   signup/login/logout/me + password reset + admin actions
│   ├── audit-pipeline.py     #   audit / sitemap / crawl / site-health / pagespeed (auth-gated)
│   ├── ai.py                 #   AI summary / fix / content draft (auth-gated)
│   ├── analyze.py            #   sitewide / crawl-graph / near-duplicates
│   ├── crawls.py             #   persisted crawls (org-scoped)
│   ├── export.py             #   CSV / Excel / PDF export (auth-gated)
│   └── api-keys.py           #   encrypted key vault (admin to write)
├── modules/                 # Audit engine (auditor, crawler, scoring, link_auditor, …)
├── worker/                  # Platform layer: DB models, auth, vault, queue, crawl service, email
│   ├── auth.py               #   scrypt + HMAC sessions, roles, password reset
│   ├── email_send.py         #   Gmail SMTP / Resend sender
│   └── db/                   #   SQLAlchemy models + session
├── alembic/                 # DB migrations
├── lib/                     # Client state, aggregation, formatting
├── requirements.txt         # Python deps (prod)  ·  requirements-dev.txt (tests)
├── agents.md                # Deep engineering notes
└── PROJECT_LOG.md           # Session-by-session history
```

---

## 9. Large-scale crawling (worker)

Vercel serverless caps each function at 60–90s, so it cannot run a long
site-wide crawl in one request. Two paths exist for scale:
- **Browser-orchestrated** bulk audits (implemented) — the tab drives one
  invocation per URL, resumable via IndexedDB.
- **`worker/`** — an always-on background worker + DB job queue + BFS crawler,
  designed to run on a host like Railway/Render or as a **GitHub Actions batch
  runner** (no Vercel timeout), writing results to Neon. See
  [`worker/README.md`](worker/README.md).

---

## 10. Security notes

- Session cookies: `HttpOnly; SameSite=Lax; Secure` (prod); stateless HMAC.
- Auth **fails closed**: no `AUTH_SECRET` → refuses to sign sessions on any real
  deploy; org/admin fallbacks only relax in explicit local dev/test.
- Credential-consuming endpoints (audit, AI, export, key-vault) require sign-in
  (prevents anonymous quota abuse / SSRF-proxy misuse).
- All outbound URL fetches are SSRF-guarded (private-IP / redirect-hop
  revalidation) with timeouts.
- Scraped third-party HTML is sanitized (DOMPurify) before rendering.
- API keys in the vault are encrypted at rest (Fernet); never returned in plaintext.

**Known deferred hardening** (documented, low risk for the current single
workspace): per-user session-version invalidation on password change, and
org-scoping the API-key vault for a future multi-org setup.

---

## 11. Changelog — what was done

### This round (Sept 2026) — auth, email reset, security & UX hardening
- **Local + cloud setup:** Python venv, dependencies, connected **Neon** via the
  Vercel integration, ran all migrations; verified the full stack on the live URL.
- **Fixed a missing dependency:** added `cssselect` (the CSS-selector extraction
  feature failed at runtime without it).
- **Accounts, roles & admin portal:** `/admin` with user management and password
  resets; admin-resolved reset flow; self-service **Change password** in Settings.
- **Email-based password reset:** emailed reset links via **Gmail SMTP**
  (`worker/email_send.py`), with a Resend alternative and admin-resolved
  fallback; `/reset` landing page; hashed, expiring, single-use tokens.
- **Security audit + fixes** (three-dimension review): sanitized a **stored XSS**
  in the body-content preview; **auth-gated** previously-open endpoints
  (audit/AI/export/api-keys); made auth **fail closed** instead of keying off the
  `VERCEL` env var; least-privilege default role.
- **Backend fixes:** correct `normalized_url` persistence (broken-link/duplicate
  detection), a **wall-clock guard** on crawls (no Vercel timeout overruns),
  400-not-500 on malformed JSON, thematic-grouping order fix, two broken imports,
  `maxPages` clamping, dead-code removal.
- **Frontend / UX fixes:** stopped a **runaway polling loop**; fixed undefined
  CSS tokens (transparent panels) and dark-mode native controls; wrapped
  `useSearchParams` in Suspense; **show/hide password toggle**; header shows a
  **display name** (not the email) and no longer wraps "Log out"; fixed a React
  hydration warning; working single-URL Cancel; accessibility + clipboard guards.
- **Housekeeping:** replaced the dead site URL with the live domain (metadata /
  OpenGraph), env-overridable; removed duplicate password-field markup; ran a
  full design pass (desktop + mobile + dark) against real audit data.
- **Quality gate:** ~380 backend tests, `tsc`, `eslint`, and `next build` all green.

### Earlier
- Merged a Next.js SEO-audit dashboard (base UI + audit engine) with a
  standalone Streamlit tool's site-health checks and Groq AI summary.
- Migrated result persistence from `localStorage` to **IndexedDB** (fixed
  quota-exceeded crashes on large bulk audits).
- Full visual redesign (modern-SaaS: Inter/JetBrains Mono, SVG icon set,
  token-based light/dark theme).
- Added GitHub Actions CI (build + test gate).
- Platform foundations (`worker/`): DB schema, job queue, BFS crawler,
  scheduling, encrypted API-key vault.
- Per-org data isolation on crawl/analyze endpoints; browser-driven crawl
  persistence (Vercel-only); admin/user account roles with a single shared
  workspace.

---

## 12. For AI agents / new contributors

If an AI or a new developer is picking this up: **this README is the map.** Then:
1. Read [`agents.md`](agents.md) for architecture invariants and gotchas (the
   client-orchestrated crawl model, the auth `dev_mode()` behavior, the
   Vercel-function consolidation, etc.).
2. Read [`PROJECT_LOG.md`](PROJECT_LOG.md) for the full session history.
3. Run `pytest -q` and `npm run build` before changing anything — they are the
   safety net. The Python `/api` only runs under `vercel dev` or on Vercel;
   tests exercise the modules directly.

---

## License

MIT
