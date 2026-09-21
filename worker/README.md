# Worker service

The site-wide crawling platform's backend (see
[SEMRUSH-AHREFS-TECHNICAL-REFERENCE.md](../SEMRUSH-AHREFS-TECHNICAL-REFERENCE.md)
for the competitive/technical grounding behind it): DB schema, a local job
queue, the BFS crawler, site-wide audit intelligence, recurring scheduling,
optional JS rendering, and an encrypted API-key vault. Nothing in
`modules/`, `api/`, `app/`, or `components/`'s existing per-page audit logic
was rewritten - this wraps and persists it.

Local dev needs no external services: SQLite substitutes for Postgres, a
DB-backed queue (`worker/queue.py`) substitutes for Redis+Celery/arq, and an
env-var-keyed `Fernet` substitutes for a real KMS. All three are designed to
swap to the real thing later - see "Going to production" below.

## Run it locally

```bash
pip install -r requirements.txt   # sqlalchemy, alembic, croniter, playwright, cryptography, etc.
playwright install chromium       # one-time: downloads the browser Playwright drives (~300MB)
python -m alembic upgrade head    # creates worker/dev.db with all tables
python -m worker                  # starts the worker loop (Ctrl+C to stop)
```

If you'll use the API-key vault (`worker/vault.py`), also set an encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export VAULT_ENCRYPTION_KEY=<paste the output>   # or set it in your OS/shell profile
```

Enqueue a job from another shell/script while the worker is running:

```python
from worker.queue import enqueue
enqueue("audit.page", {"url": "https://example.com", "check_links": True})
```

The worker picks it up within `poll_interval` seconds (default 2s), runs the
existing `modules.auditor.audit_url()` unchanged, and writes the result back
to the `jobs` table's `result_json` column. `crawl.start` jobs (created via
`worker/crawl_service.py::create_crawl()`) run the full BFS crawl instead -
see `app/site-crawls/` for the UI, or `api/crawls.py` for the API.

## What's here

| File | Purpose |
|---|---|
| `db/models.py` | SQLAlchemy models - tenancy, projects, crawls, pages, links, issues, the `jobs` queue table, and the `api_keys` vault table |
| `db/session.py` | Engine/session factory, reads `DATABASE_URL` (defaults to local SQLite) |
| `queue.py` | `enqueue()` + `Worker.run_once()` - atomic claim via `UPDATE ... RETURNING`, works unchanged on SQLite 3.35+ and Postgres |
| `tasks.py` | Job handler registry - `audit.page` (single-URL audit) and `crawl.start` (full BFS crawl) |
| `crawl_service.py` | Creates crawls, adapts DB rows to `modules.crawler`'s config, persists each page/link/issue as a crawl runs |
| `site_audit.py` | Post-crawl aggregation: duplicate content/titles, orphan pages, broken links, redirect chains, thematic report |
| `scheduler.py` | `enqueue_due_crawls()` - the recurring-crawl tick, interleaved into `__main__.py`'s loop |
| `crawl_diff.py` | Compares two crawls of the same project: new/fixed issues, regressed/improved pages, score trend |
| `vault.py` | Encryption primitives (`Fernet`, keyed by `VAULT_ENCRYPTION_KEY`) for the API-key vault |
| `api_key_service.py` | Vault service layer - set/get/list/delete a provider's key; `list_api_keys()` never returns a decrypted value |
| `__main__.py` | `python -m worker` entry point |

## Going to production

Set `DATABASE_URL=postgresql://...` - no code changes needed; `db/models.py`
uses dialect-neutral types throughout. The queue itself (`queue.py`) is the
one piece expected to be *replaced*, not reconfigured, when real concurrent
worker replicas are needed: swap it for Celery/arq + Redis behind the same
`enqueue()` call sites. The vault's `VAULT_ENCRYPTION_KEY` env var should
become a KMS-managed key per `05-INFRASTRUCTURE-AND-OPS.md`, rather than a
plain environment variable, once a KMS is available.

## Not built yet

A real login flow (the `users`/`organizations`/`memberships` tables exist
for foreign-key completeness only - everything currently collapses to one
"Local Dev" org), GSC/GA4 integration, and LLM-generated (rather than
static) fix suggestions.
