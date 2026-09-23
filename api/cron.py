"""M5 T5.4 - Vercel Cron entry point (GET /api/cron).

Vercel invokes cron jobs with a GET request on the schedule declared in
``vercel.json`` (owned separately - this handler doesn't define the schedule).
Each hit does two things:

  1. **Always** fires due schedules via ``worker.scheduler.enqueue_due_crawls()``
     - this just creates a queued Crawl (+ Job) for every CrawlConfig whose
       cron is due and advances its next_run_at. Cheap, no crawling.
  2. **Only if opted in** (env ``CRON_EXECUTE_CRAWLS`` truthy) runs a bounded,
     resumable chunk of server-side crawling via
     ``worker.cron_runner.process_due()``. This is OFF by default: on Vercel
     there's no always-on worker, so unattended execution is deliberately
     opt-in and time-boxed to fit the function limit (large crawls finish
     across ticks via resume).

Env flags
---------
  * ``CRON_SECRET``          - when set, the request must carry
    ``Authorization: Bearer <CRON_SECRET>`` or it's rejected 401. Vercel sends
    this header automatically for its cron invocations. When unset (local/dev)
    the endpoint is open so it can be hit by hand.
  * ``CRON_EXECUTE_CRAWLS``  - ``"1"``/``"true"``/``"yes"``/``"on"`` enables
    step 2 above. Anything else (or unset) leaves execution disabled; the JSON
    response says so.
"""

import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import send_json  # noqa: E402
from worker import cron_runner  # noqa: E402
from worker.scheduler import enqueue_due_crawls  # noqa: E402

logger = logging.getLogger(__name__)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            # Auth: enforce the shared secret when one is configured; open in dev.
            secret = (os.environ.get("CRON_SECRET") or "").strip()
            if secret:
                auth = self.headers.get("Authorization") or ""
                if auth != f"Bearer {secret}":
                    send_json(self, 401, {"error": "unauthorized"})
                    return

            # Step 1: always fire due schedules (creates queued crawls).
            enqueued = enqueue_due_crawls()

            execute = _truthy(os.environ.get("CRON_EXECUTE_CRAWLS"))
            summary = {"enqueued": enqueued, "execution_enabled": execute}

            # Step 2: opt-in server-side execution only.
            if execute:
                run_summary = cron_runner.process_due()
                summary["ran"] = run_summary.get("ran", [])
                summary["finished"] = run_summary.get("finished", [])
            else:
                summary["note"] = (
                    "server-side crawl execution is disabled; "
                    "set CRON_EXECUTE_CRAWLS=1 to enable it"
                )

            send_json(self, 200, summary)
        except Exception:  # noqa: BLE001
            logger.exception("cron.py request failed")
            send_json(self, 500, {"error": "Internal error while running cron."})
