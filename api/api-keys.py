"""Phase 5 API-key vault endpoint - GET lists configured providers (masked
only, per worker/api_key_service.py's one hard rule), POST actions
set/delete/test a provider's credential. Same GET+action-dispatch shape as
api/ai.py.

Talks straight to worker/api_key_service.py + worker/db (same DB file the
worker process reads/writes), exactly like api/crawls.py already does for
the crawl platform - no separate network layer.
"""

import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import read_json_body, require_str, send_json  # noqa: E402
from modules.ai_assist import _chat  # noqa: E402
from modules.pagespeed import fetch_pagespeed  # noqa: E402
from worker.auth import AuthError, require_admin, require_authenticated  # noqa: E402
from worker.api_key_service import (  # noqa: E402
    delete_api_key,
    get_api_key,
    get_or_create_default_org,
    list_api_keys,
    set_api_key,
)
from worker.db.session import SessionLocal  # noqa: E402

logger = logging.getLogger(__name__)

# 04-FRONTEND-DESIGN.md asks for a "Test Connection" per provider; only these
# two have existing integration code to validate against today (PSI/Groq are
# the providers this app actually calls) - the rest can be saved/deleted but
# not real-connectivity-tested yet, stated explicitly rather than faked.
_TEST_URL = "https://example.com"


def _test_psi(api_key: str) -> dict:
    result = fetch_pagespeed(_TEST_URL, api_key=api_key)
    if result.get("success", True) is False:
        return {"ok": False, "error": result.get("error", "PageSpeed Insights request failed.")}
    return {"ok": True}


def _test_groq(api_key: str) -> dict:
    try:
        _chat([{"role": "user", "content": "reply with OK"}], api_key, max_tokens=5)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001 - report as a failed test, not a 500
        return {"ok": False, "error": str(e)}


_TESTERS = {"psi": _test_psi, "groq": _test_groq}


def _handle_set(handler, payload):
    try:
        provider = require_str(handler, payload, "provider")
        if provider is None:
            return
        value = require_str(handler, payload, "value")
        if value is None:
            return

        with SessionLocal() as db:
            org = get_or_create_default_org(db)
            set_api_key(db, org.id, provider, value)
        send_json(handler, 200, {"ok": True})
    except ValueError as e:
        send_json(handler, 400, {"ok": False, "error": str(e)})
    except RuntimeError as e:
        # vault.py's clear "VAULT_ENCRYPTION_KEY not set" error - surface it
        # as-is rather than a generic 500, it tells the operator exactly
        # what to fix.
        send_json(handler, 500, {"ok": False, "error": str(e)})
    except Exception:  # noqa: BLE001
        logger.exception("api-keys.py (set) request failed")
        send_json(handler, 500, {"ok": False, "error": "Internal error while saving the key."})


def _handle_delete(handler, payload):
    try:
        provider = require_str(handler, payload, "provider")
        if provider is None:
            return
        with SessionLocal() as db:
            org = get_or_create_default_org(db)
            deleted = delete_api_key(db, org.id, provider)
        send_json(handler, 200, {"ok": True, "deleted": deleted})
    except Exception:  # noqa: BLE001
        logger.exception("api-keys.py (delete) request failed")
        send_json(handler, 500, {"ok": False, "error": "Internal error while deleting the key."})


def _handle_test(handler, payload):
    try:
        provider = require_str(handler, payload, "provider")
        if provider is None:
            return

        tester = _TESTERS.get(provider)
        if tester is None:
            send_json(handler, 200, {
                "ok": False,
                "error": f"Test Connection isn't available for {provider!r} yet - only psi/groq are supported.",
            })
            return

        with SessionLocal() as db:
            org = get_or_create_default_org(db)
            api_key = get_api_key(db, org.id, provider)
        if not api_key:
            send_json(handler, 200, {"ok": False, "error": f"No {provider} key saved yet."})
            return

        send_json(handler, 200, tester(api_key))
    except RuntimeError as e:
        send_json(handler, 500, {"ok": False, "error": str(e)})
    except Exception:  # noqa: BLE001
        logger.exception("api-keys.py (test) request failed")
        send_json(handler, 500, {"ok": False, "error": "Internal error while testing the connection."})


_ACTIONS = {
    "set": _handle_set,
    "delete": _handle_delete,
    "test": _handle_test,
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Lists configured providers + masked previews - sign-in required so it
        # isn't publicly enumerable (audit finding #2).
        try:
            require_authenticated(self)
        except AuthError as e:
            send_json(self, e.status, {"error": e.message})
            return
        try:
            with SessionLocal() as db:
                org = get_or_create_default_org(db)
                keys = list_api_keys(db, org.id)
            send_json(self, 200, {"apiKeys": keys})
        except Exception:  # noqa: BLE001
            logger.exception("api-keys.py (list) request failed")
            send_json(self, 500, {"error": "Internal error while listing API keys."})

    def do_POST(self):
        try:
            payload = read_json_body(self)
        except Exception:  # noqa: BLE001
            logger.exception("api-keys.py request body could not be parsed")
            send_json(self, 500, {"ok": False, "error": "Internal error while processing the request."})
            return

        action = payload.get("action")
        fn = _ACTIONS.get(action)
        if fn is None:
            send_json(self, 400, {"ok": False, "error": f"Unknown or missing action (expected one of {sorted(_ACTIONS)})"})
            return
        # set/delete mutate the shared credential vault -> admin only. 'test'
        # only exercises an already-saved key, so any signed-in user may run it
        # -- but it must NOT be anonymous (it spends the vaulted key), audit #2.
        try:
            if action in ("set", "delete"):
                with SessionLocal() as db:
                    require_admin(self, db)
            else:
                require_authenticated(self)
        except AuthError as e:
            send_json(self, e.status, {"ok": False, "error": e.message})
            return
        fn(self, payload)
