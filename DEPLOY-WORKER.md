# Deploying the always-on crawl worker (M2 T2.1)

This stands up the background worker so site crawls run **server-side** — they
keep going after you close the tab, and scheduled crawls fire. The crawl engine,
job queue, and per-page persistence already exist in `worker/`; this is purely
about running that process on an always-on host (Railway) pointed at your Neon
database.

The Next.js frontend keeps deploying to **Vercel** exactly as today. This worker
is a **separate** service on **Railway**. They share only the Neon database.

---

## What you'll do (≈15 min)

1. **Apply the DB schema to Neon** (once, and after any migration).
   From your machine with the Neon URL exported:
   ```bash
   # Use the DIRECT (unpooled) Neon connection string here.
   export DATABASE_URL="postgresql://USER:PASSWORD@HOST/DB?sslmode=require"
   .venv/Scripts/python -m alembic upgrade head   # Windows: .venv\Scripts\python
   ```
   This creates the tables and the new M2 indexes.

2. **Create the Railway service.**
   - Railway → New Project → **Deploy from GitHub repo** → pick this repo.
   - Railway reads [`railway.toml`](railway.toml) and builds the worker image
     from [`worker/Dockerfile`](worker/Dockerfile) (it will NOT try to build the
     Next.js app). First build takes a few minutes (it installs Chromium).

3. **Set the worker's environment variables** (Railway → service → Variables):
   - `DATABASE_URL` — your Neon connection string. **Prefer the DIRECT/unpooled
     URL** for a long-running worker (the pooled pgbouncer URL can break prepared
     statements). Required.
   - `AUTH_SECRET` — optional; only needed if you later have the worker do
     auth-scoped work. The crawl worker itself does not require it.
   - (Optional, per-feature) `RESEND_API_KEY` / SMTP_* are for password-reset
     email and are NOT needed by the worker.

4. **Deploy and verify the worker is alive.**
   - Railway → Deployments → Logs. You should see the worker loop start and idle
     (polling for jobs), with no traceback. Leave it running.

5. **Flip the frontend to server-side crawls.**
   - Vercel → Project → Settings → Environment Variables → add
     `NEXT_PUBLIC_SERVER_CRAWL=1` → redeploy.
   - With the flag set, starting a Site Crawl just **enqueues** the job (the
     `create` API already does this); the Railway worker picks it up and runs it,
     and the crawl detail page shows live progress from the DB. Without the flag
     (today's default), the browser tab drives the crawl as before — so nothing
     breaks if the worker isn't up yet.

---

## Verify it works end-to-end

1. Start a Site Crawl of a small site.
2. **Close the tab.**
3. Reopen `/site-crawls/<id>` — `pages_crawled` should keep climbing and the
   crawl should reach `completed` without the tab open. That's the whole point of
   T2.1.
4. A crawl with a schedule (e.g. weekly) will now re-run automatically — the
   worker's scheduler tick (`enqueue_due_crawls`) runs every 60s.

---

## Notes / limits

- **One replica.** The DB-backed queue (`worker/queue.py`) claims jobs with an
  atomic `UPDATE ... RETURNING`, which is safe for a single worker. If you scale
  to multiple replicas, move to Redis + arq/Celery (flagged in
  `worker/README.md`). Keep `numReplicas = 1` in `railway.toml` for now.
- **Migrations are manual** (step 1). Vercel does not run them, and neither does
  the worker on boot (deliberate, to keep multi-deploy safe). Re-run
  `alembic upgrade head` against Neon whenever you add a migration.
- **Rolling back** the frontend to browser-mode is instant: remove
  `NEXT_PUBLIC_SERVER_CRAWL` (or set it to `0`) in Vercel and redeploy.
- **Resume after interruption** is not built yet (M2 T2.3): a worker restart
  mid-crawl marks the in-flight crawl failed rather than resuming from the
  frontier. Streaming persistence means already-crawled pages are kept.
