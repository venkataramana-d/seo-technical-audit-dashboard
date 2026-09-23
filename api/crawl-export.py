"""M5 T5.2 - server-side crawl export.

Exports a persisted *site crawl* (its Page/Issue/Link rows) as CSV / Excel /
PDF / JSON, reusing the exact report generators the client-side audit export
uses (modules/report_generator.py). The crawl -> report-shape reconstruction
lives in worker/crawl_export.py, so there is no second reporting code path.

Same one-file/action-dispatch convention as api/crawls.py - POST
{"action": "export", "crawlId": <int>, "format": "csv"|"xlsx"|"pdf"|"json"}.
Auth + per-org isolation match api/crawls.py exactly (resolve_org_id +
crawl_for_org); when resolve_org_id returns None (dev/test) scoping is skipped.
"""

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import read_json_body, send_json  # noqa: E402
from modules.report_generator import generate_csv, generate_excel, generate_pdf  # noqa: E402
from worker.access import crawl_for_org, resolve_org_id  # noqa: E402
from worker.auth import AuthError  # noqa: E402
from worker.crawl_export import build_crawl_results, crawl_meta  # noqa: E402
from worker.db.session import SessionLocal  # noqa: E402

logger = logging.getLogger(__name__)

MIME = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "json": "application/json",
}


def _parse_crawl_id(handler, payload) -> int | None:
    try:
        return int(payload.get("crawlId"))
    except (TypeError, ValueError):
        send_json(handler, 400, {"error": "crawlId is required and must be an integer"})
        return None


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            payload = read_json_body(self)
        except ValueError:  # malformed JSON (JSONDecodeError subclasses ValueError)
            send_json(self, 400, {"error": "Request body must be valid JSON."})
            return
        except Exception:  # noqa: BLE001
            logger.exception("crawl-export.py request body could not be parsed")
            send_json(self, 500, {"error": "Internal error while processing the request."})
            return

        action = payload.get("action")
        if action != "export":
            send_json(self, 400, {"error": "Unknown or missing action (expected 'export')"})
            return

        crawl_id = _parse_crawl_id(self, payload)
        if crawl_id is None:
            return

        fmt = payload.get("format", "csv")
        if fmt not in MIME:
            send_json(self, 400, {"error": "format must be csv, xlsx, pdf, or json"})
            return

        try:
            with SessionLocal() as db:
                # Per-org isolation, exactly like api/crawls.py: resolve the org
                # from the session, then verify this crawl belongs to it.
                # resolve_org_id returns None in dev/test (no session, no
                # VERCEL) - scoping is skipped, preserving single-tenant behavior.
                try:
                    org_id = resolve_org_id(self, db)
                except AuthError as e:
                    send_json(self, e.status, {"error": e.message})
                    return
                if org_id is not None and crawl_for_org(db, crawl_id, org_id) is None:
                    send_json(self, 404, {"error": "Crawl not found."})
                    return

                results = build_crawl_results(db, crawl_id)
                if not results:
                    send_json(self, 400, {"error": "This crawl has no pages to export yet."})
                    return
                meta = crawl_meta(db, crawl_id)

            if fmt == "csv":
                data = generate_csv(results)
            elif fmt == "xlsx":
                data = generate_excel(results)
            elif fmt == "json":
                data = json.dumps(results, default=str, indent=2).encode("utf-8")
            else:
                data = generate_pdf(results)

            crawl_ref = meta.get("id") or crawl_id
            self.send_response(200)
            self.send_header("Content-Type", MIME[fmt])
            self.send_header(
                "Content-Disposition", f'attachment; filename="crawl-{crawl_ref}-report.{fmt}"'
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:  # noqa: BLE001
            logger.exception("crawl-export.py request failed for crawl %s", crawl_id)
            send_json(self, 500, {"error": "Internal error while generating the export."})
