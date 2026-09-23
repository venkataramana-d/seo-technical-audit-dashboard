"""Insights endpoint - surfaces Google Search Console (GSC) + Google Analytics 4
(GA4) metrics for the audit dashboard, keyed off an encrypted service-account
credential stored in the existing API-key vault (providers 'gsc'/'ga4').

Same one-file/action-dispatch shape as api/ai.py + api/api-keys.py: POST
{"action": "status"|"gsc"|"ga4", ...}. Reuses the vault exactly like api/ai.py
does (get_or_create_default_org + get_api_key), and the same sign-in gate
(require_authenticated) since these actions spend a stored credential.

The stored vault VALUE for each provider is a JSON-string wrapper (never the
bare Google key), set from Settings -> the GSC/GA4 connect forms:
  gsc: {"service_account": {<full Google SA JSON>}, "site_url": "https://example.com/"}
  ga4: {"service_account": {<full Google SA JSON>}, "property_id": "123456789"}

It ACTIVATES once the user connects Google via a service account (their one
external step). Until then, gsc/ga4 return a friendly {"configured": false}
state - a not-yet-connected signal, not an error.
"""

import json
import logging
import os
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules._http import read_json_body, send_json  # noqa: E402
from modules.google_integrations import (  # noqa: E402
    GoogleIntegrationError,
    fetch_ga4,
    fetch_gsc,
    join_ga4_to_pages,
    join_gsc_to_pages,
)
from worker.api_key_service import get_api_key, get_or_create_default_org  # noqa: E402
from worker.auth import AuthError, require_authenticated  # noqa: E402
from worker.db.session import SessionLocal  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 28


def _read_vault_wrapper(provider: str) -> dict | None:
    """Load and JSON-parse the vault wrapper for 'gsc'/'ga4', or None if the
    provider isn't configured. Raises ValueError if a value exists but isn't the
    expected JSON wrapper (surfaced to the caller as a 400)."""
    with SessionLocal() as db:
        org = get_or_create_default_org(db)
        raw = get_api_key(db, org.id, provider)
    if not raw:
        return None
    try:
        wrapper = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"The saved {provider} credential is not valid JSON - re-connect it "
            "in Settings by pasting the full service-account JSON."
        ) from e
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("service_account"), dict):
        raise ValueError(
            f"The saved {provider} credential is missing its service_account - "
            "re-connect it in Settings."
        )
    return wrapper


def _date_range(payload) -> tuple[str, str]:
    """Resolve the report window from the payload, defaulting to the last 28
    days. Accepts ISO 'YYYY-MM-DD' startDate/endDate."""
    end = (payload.get("endDate") or "").strip()
    start = (payload.get("startDate") or "").strip()
    if not end:
        end = date.today().isoformat()
    if not start:
        # Derive from end when possible, else today - lookback.
        try:
            end_d = date.fromisoformat(end)
        except ValueError:
            end_d = date.today()
            end = end_d.isoformat()
        start = (end_d - timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
    return start, end


def _page_urls_for_crawl(crawl_id) -> list[str]:
    """Optional/guarded: the crawl's page URLs, for joining metrics onto pages.
    Any failure (bad id, DB unreachable, empty crawl) yields [] so the raw
    metrics still return."""
    try:
        cid = int(crawl_id)
    except (TypeError, ValueError):
        return []
    try:
        from sqlalchemy import select
        from worker.db.models import Page

        with SessionLocal() as db:
            rows = db.execute(select(Page.url).where(Page.crawl_id == cid)).scalars().all()
        return [u for u in rows if u]
    except Exception:  # noqa: BLE001 - joining is a best-effort enhancement
        logger.warning("insights.py could not load page URLs for crawl %s", crawl_id, exc_info=True)
        return []


def _site_origin(site_url: str, page_urls: list[str]) -> str:
    """Origin to prefix GA4 pagePaths with. Prefer the configured GSC site_url's
    origin; else infer from the first page URL."""
    for candidate in (site_url, page_urls[0] if page_urls else ""):
        if not candidate:
            continue
        parts = urlsplit(candidate)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return ""


def _not_configured(provider_label: str) -> dict:
    return {
        "configured": False,
        "message": f"Connect Google {provider_label} in Settings to see this data.",
    }


def _handle_status(handler, payload):
    """Report which providers are configured - booleans only, never the secret."""
    try:
        with SessionLocal() as db:
            org = get_or_create_default_org(db)
            gsc_set = bool(get_api_key(db, org.id, "gsc"))
            ga4_set = bool(get_api_key(db, org.id, "ga4"))
        send_json(handler, 200, {"gsc": gsc_set, "ga4": ga4_set})
    except Exception:  # noqa: BLE001
        logger.exception("insights.py (status) request failed")
        send_json(handler, 500, {"error": "Internal error while reading integration status."})


def _handle_gsc(handler, payload):
    try:
        try:
            wrapper = _read_vault_wrapper("gsc")
        except ValueError as e:
            send_json(handler, 400, {"error": str(e)})
            return
        if wrapper is None:
            send_json(handler, 200, _not_configured("Search Console"))
            return

        site_url = (wrapper.get("site_url") or "").strip()
        if not site_url:
            send_json(handler, 400, {
                "error": "The saved GSC credential has no site_url - re-connect it in Settings.",
            })
            return

        start, end = _date_range(payload)
        row_limit = int(payload.get("rowLimit", 1000) or 1000)
        rows = fetch_gsc(
            wrapper["service_account"], site_url,
            start_date=start, end_date=end, dimensions=("page",), row_limit=row_limit,
        )

        result = {
            "configured": True,
            "siteUrl": site_url,
            "startDate": start,
            "endDate": end,
            "rows": rows,
        }
        crawl_id = payload.get("crawlId")
        if crawl_id is not None:
            page_urls = _page_urls_for_crawl(crawl_id)
            if page_urls:
                result["byPage"] = join_gsc_to_pages(rows, page_urls)
        send_json(handler, 200, result)
    except GoogleIntegrationError as e:
        send_json(handler, 400, {"configured": True, "error": str(e)})
    except Exception:  # noqa: BLE001
        logger.exception("insights.py (gsc) request failed")
        send_json(handler, 500, {"error": "Internal error while fetching Search Console data."})


def _handle_ga4(handler, payload):
    try:
        try:
            wrapper = _read_vault_wrapper("ga4")
        except ValueError as e:
            send_json(handler, 400, {"error": str(e)})
            return
        if wrapper is None:
            send_json(handler, 200, _not_configured("Analytics 4"))
            return

        property_id = str(wrapper.get("property_id") or "").strip()
        if not property_id:
            send_json(handler, 400, {
                "error": "The saved GA4 credential has no property_id - re-connect it in Settings.",
            })
            return

        start, end = _date_range(payload)
        rows = fetch_ga4(
            wrapper["service_account"], property_id,
            start_date=start, end_date=end,
            dimensions=("pagePath",), metrics=("sessions", "screenPageViews"),
        )

        result = {
            "configured": True,
            "propertyId": property_id,
            "startDate": start,
            "endDate": end,
            "rows": rows,
        }
        crawl_id = payload.get("crawlId")
        if crawl_id is not None:
            page_urls = _page_urls_for_crawl(crawl_id)
            if page_urls:
                # GA4 gives paths; combine with the site origin to match full URLs.
                gsc_wrapper = None
                try:
                    gsc_wrapper = _read_vault_wrapper("gsc")
                except ValueError:
                    gsc_wrapper = None
                gsc_site = (gsc_wrapper.get("site_url") if gsc_wrapper else "") or ""
                origin = _site_origin(gsc_site, page_urls)
                result["byPage"] = join_ga4_to_pages(rows, page_urls, origin)
        send_json(handler, 200, result)
    except GoogleIntegrationError as e:
        send_json(handler, 400, {"configured": True, "error": str(e)})
    except Exception:  # noqa: BLE001
        logger.exception("insights.py (ga4) request failed")
        send_json(handler, 500, {"error": "Internal error while fetching Analytics data."})


_ACTIONS = {
    "status": _handle_status,
    "gsc": _handle_gsc,
    "ga4": _handle_ga4,
}


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # All actions read a stored credential -> require sign-in (matches
        # api/ai.py's gate; anonymous callers must not spend the vaulted key).
        try:
            require_authenticated(self)
        except AuthError as e:
            send_json(self, e.status, {"error": e.message})
            return
        try:
            payload = read_json_body(self)
        except (ValueError, json.JSONDecodeError):
            send_json(self, 400, {"error": "Request body must be valid JSON."})
            return
        except Exception:  # noqa: BLE001
            logger.exception("insights.py request body could not be parsed")
            send_json(self, 500, {"error": "Internal error while processing the request."})
            return

        action = payload.get("action")
        fn = _ACTIONS.get(action)
        if fn is None:
            send_json(self, 400, {"error": f"Unknown or missing action (expected one of {sorted(_ACTIONS)})"})
            return
        fn(self, payload)
