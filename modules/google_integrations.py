"""Google Search Console (GSC) + Google Analytics 4 (GA4) read-only clients,
built on a service-account model - no interactive OAuth consent flow.

Why hand-rolled instead of google-api-python-client / google-auth: this app
ships as Vercel Python serverless functions whose whole requirements.txt is
reinstalled per function at build time (see modules/_http.py's note), so the
hard rule here is NO new pip dependencies. Everything below uses only
`requests` (HTTP) and `cryptography` (RS256 JWT signing) - both already deps.

The service-account access-token exchange (JWT-bearer grant) is:
  1. build a JWT: header {alg:RS256, typ:JWT}, claims {iss: client_email,
     scope, aud: token endpoint, iat, exp};
  2. sign the `<header>.<claims>` signing input with the SA's RSA private key
     (SHA-256, PKCS#1 v1.5) and base64url-encode the signature;
  3. POST grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=<jwt>
     to https://oauth2.googleapis.com/token and read `access_token`.

All network I/O goes through `requests` so tests can monkeypatch it; the pure
join_* helpers need no network at all.
"""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import quote, urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

# JWT-bearer grant type (RFC 7523).
_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# A generous default for a single audit-time report; both APIs are read-only.
_HTTP_TIMEOUT = 60


class GoogleIntegrationError(Exception):
    """A typed, user-actionable failure from a Google API call or the
    service-account token exchange. The message is safe to surface to an
    operator/end-user (never contains the private key)."""


# --------------------------------------------------------------------------- #
# base64url helpers (no padding, per JWT spec)
# --------------------------------------------------------------------------- #
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_json(obj: dict) -> str:
    # Compact separators -> the exact bytes we sign, no incidental whitespace.
    return _b64url(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


# --------------------------------------------------------------------------- #
# service-account -> OAuth access token (JWT-bearer flow)
# --------------------------------------------------------------------------- #
def _require_sa_fields(service_account: dict) -> tuple[str, str]:
    if not isinstance(service_account, dict):
        raise GoogleIntegrationError(
            "Invalid service account: expected the parsed JSON key object "
            "(a dict with client_email and private_key)."
        )
    client_email = service_account.get("client_email")
    private_key = service_account.get("private_key")
    if not client_email or not private_key:
        raise GoogleIntegrationError(
            "Service account JSON is missing 'client_email' and/or 'private_key' - "
            "paste the full JSON key file downloaded from Google Cloud."
        )
    return client_email, private_key


def _sign_rs256(signing_input: bytes, private_key_pem: str) -> bytes:
    try:
        key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
    except Exception as e:  # noqa: BLE001 - normalise to a typed, actionable error
        raise GoogleIntegrationError(
            "Could not load the service account private key - ensure the "
            "'private_key' field is the exact PEM block from the JSON key "
            f"(including the BEGIN/END lines). ({e})"
        ) from e
    try:
        return key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:  # noqa: BLE001
        raise GoogleIntegrationError(f"Failed to sign the service-account JWT: {e}") from e


def get_access_token(service_account: dict, scope: str) -> str:
    """Exchange a service-account key for a short-lived OAuth access token via
    the JWT-bearer grant. Cache-free (each call mints a fresh token) - fine for
    the low request volume here."""
    client_email, private_key = _require_sa_fields(service_account)

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": client_email,
        "scope": scope,
        "aud": TOKEN_ENDPOINT,
        "iat": now,
        "exp": now + 3600,  # max 1h; Google caps assertion lifetime at 1h
    }
    signing_input = f"{_b64url_json(header)}.{_b64url_json(claims)}".encode("ascii")
    signature = _sign_rs256(signing_input, private_key)
    assertion = f"{signing_input.decode('ascii')}.{_b64url(signature)}"

    try:
        resp = requests.post(
            TOKEN_ENDPOINT,
            data={"grant_type": _JWT_BEARER_GRANT, "assertion": assertion},
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        raise GoogleIntegrationError(f"Could not reach Google's token endpoint: {e}") from e

    if resp.status_code != 200:
        detail = _error_detail(resp)
        # invalid_grant here almost always means a clock skew or a wrong/rotated
        # key - point the operator at the concrete cause.
        raise GoogleIntegrationError(
            "Service-account token exchange failed "
            f"(HTTP {resp.status_code}): {detail}. Check that the key is valid "
            "and the required API is enabled in the Google Cloud project."
        )
    try:
        token = resp.json().get("access_token")
    except ValueError as e:
        raise GoogleIntegrationError("Token endpoint returned a non-JSON response.") from e
    if not token:
        raise GoogleIntegrationError("Token endpoint response did not include an access_token.")
    return token


def _error_detail(resp: "requests.Response") -> str:
    """Best-effort extraction of Google's structured error message."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:300] or "no response body"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("message") or json.dumps(err)[:300]
        if isinstance(err, str):
            return body.get("error_description") or err
    return json.dumps(body)[:300]


def _raise_for_status(resp: "requests.Response", *, what: str, permission_hint: str) -> None:
    if resp.status_code == 200:
        return
    detail = _error_detail(resp)
    if resp.status_code in (401, 403):
        raise GoogleIntegrationError(f"{permission_hint} (HTTP {resp.status_code}: {detail})")
    if resp.status_code == 404:
        raise GoogleIntegrationError(
            f"{what} returned 404 - the property/site could not be found or the "
            f"service account cannot see it. ({detail})"
        )
    raise GoogleIntegrationError(f"{what} failed (HTTP {resp.status_code}): {detail}")


# --------------------------------------------------------------------------- #
# Google Search Console: Search Analytics query
# --------------------------------------------------------------------------- #
def fetch_gsc(
    service_account: dict,
    site_url: str,
    *,
    start_date: str,
    end_date: str,
    dimensions=("page",),
    row_limit: int = 1000,
) -> list[dict]:
    """Query GSC Search Analytics for a site. Returns one dict per row with the
    dimension value(s) plus clicks/impressions/ctr/position.

    `site_url` must be exactly as it appears in Search Console (a URL-prefix
    property like "https://example.com/", or a "sc-domain:example.com" domain
    property)."""
    if not site_url:
        raise GoogleIntegrationError("site_url is required for a GSC query.")
    dimensions = list(dimensions)
    token = get_access_token(service_account, GSC_SCOPE)

    endpoint = (
        f"https://www.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}"
        "/searchAnalytics/query"
    )
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
    }
    try:
        resp = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        raise GoogleIntegrationError(f"Could not reach the Search Console API: {e}") from e

    _raise_for_status(
        resp,
        what="Search Console query",
        permission_hint=(
            "The service account lacks access to this GSC property - add its "
            "client_email as a user in Search Console (Settings -> Users and "
            f"permissions) for {site_url!r}"
        ),
    )

    rows = (resp.json() or {}).get("rows", []) or []
    out: list[dict] = []
    for row in rows:
        keys = row.get("keys", []) or []
        entry: dict = {}
        for i, dim in enumerate(dimensions):
            entry[dim] = keys[i] if i < len(keys) else None
        entry["clicks"] = row.get("clicks", 0)
        entry["impressions"] = row.get("impressions", 0)
        entry["ctr"] = row.get("ctr", 0.0)
        entry["position"] = row.get("position")
        out.append(entry)
    return out


# --------------------------------------------------------------------------- #
# Google Analytics 4 (Data API v1beta): runReport
# --------------------------------------------------------------------------- #
def fetch_ga4(
    service_account: dict,
    property_id: str,
    *,
    start_date: str,
    end_date: str,
    dimensions=("pagePath",),
    metrics=("sessions", "screenPageViews"),
) -> list[dict]:
    """Run a GA4 report for a property. Returns one dict per row: each requested
    dimension name -> value (string) and each metric name -> value (float when
    numeric, else the raw string)."""
    if not property_id:
        raise GoogleIntegrationError("property_id is required for a GA4 report.")
    # Accept "properties/123" or "123"; the endpoint wants the bare numeric id.
    property_id = str(property_id).strip()
    if property_id.startswith("properties/"):
        property_id = property_id.split("/", 1)[1]

    dimensions = list(dimensions)
    metrics = list(metrics)
    token = get_access_token(service_account, GA4_SCOPE)

    endpoint = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
    body = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "dimensions": [{"name": d} for d in dimensions],
        "metrics": [{"name": m} for m in metrics],
    }
    try:
        resp = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        raise GoogleIntegrationError(f"Could not reach the Analytics Data API: {e}") from e

    _raise_for_status(
        resp,
        what="GA4 runReport",
        permission_hint=(
            "The service account lacks access to this GA4 property - add its "
            "client_email as a Viewer on the property (Admin -> Property Access "
            f"Management) for property {property_id!r}"
        ),
    )

    data = resp.json() or {}
    # Header order is authoritative for mapping value arrays back to names.
    dim_headers = [h.get("name") for h in data.get("dimensionHeaders", [])] or dimensions
    metric_headers = [h.get("name") for h in data.get("metricHeaders", [])] or metrics

    out: list[dict] = []
    for row in data.get("rows", []) or []:
        entry: dict = {}
        dim_values = row.get("dimensionValues", []) or []
        for i, name in enumerate(dim_headers):
            entry[name] = dim_values[i].get("value") if i < len(dim_values) else None
        metric_values = row.get("metricValues", []) or []
        for i, name in enumerate(metric_headers):
            raw = metric_values[i].get("value") if i < len(metric_values) else None
            entry[name] = _coerce_number(raw)
        out.append(entry)
    return out


def _coerce_number(raw):
    if raw is None:
        return None
    try:
        f = float(raw)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return raw


# --------------------------------------------------------------------------- #
# joining metrics onto a crawl's page URLs (pure - no network)
# --------------------------------------------------------------------------- #
def _normalize_url(url: str) -> str:
    """Trailing-slash / case-insensitive-host tolerant key for matching. Keeps
    the path case (paths can be case-sensitive) but lowercases scheme+host and
    drops a single trailing slash from the path (except the root "/")."""
    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.scheme and not parts.netloc:
        # A bare path (e.g. GA4's "/pricing") - normalise the trailing slash only.
        path = url
        return path[:-1] if len(path) > 1 and path.endswith("/") else path
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    key = f"{scheme}://{netloc}{path}"
    if parts.query:
        key += f"?{parts.query}"
    return key


def _metrics_only(row: dict, drop_keys) -> dict:
    return {k: v for k, v in row.items() if k not in drop_keys}


def join_gsc_to_pages(gsc_rows: list[dict], page_urls) -> dict:
    """Map GSC rows (keyed by the 'page' dimension = a full URL) onto a crawl's
    page URLs. Returns {page_url: {clicks, impressions, ctr, position}} for the
    URLs that have GSC data. Trailing-slash/host-case tolerant."""
    by_norm: dict[str, dict] = {}
    for row in gsc_rows:
        page = row.get("page")
        if not page:
            continue
        by_norm[_normalize_url(page)] = _metrics_only(row, {"page"})

    result: dict[str, dict] = {}
    for url in page_urls:
        match = by_norm.get(_normalize_url(url))
        if match is not None:
            result[url] = match
    return result


def join_ga4_to_pages(ga4_rows: list[dict], page_urls, site_origin: str) -> dict:
    """Map GA4 rows (keyed by 'pagePath', e.g. "/pricing") onto a crawl's full
    page URLs by combining each path with `site_origin`. Returns
    {page_url: {<metric>: value, ...}}. Trailing-slash/host-case tolerant."""
    origin = (site_origin or "").rstrip("/")
    by_norm: dict[str, dict] = {}
    for row in ga4_rows:
        path = row.get("pagePath")
        if path is None:
            continue
        if not path.startswith("/"):
            path = "/" + path
        full = f"{origin}{path}" if origin else path
        by_norm[_normalize_url(full)] = _metrics_only(row, {"pagePath"})

    result: dict[str, dict] = {}
    for url in page_urls:
        match = by_norm.get(_normalize_url(url))
        if match is not None:
            result[url] = match
    return result
