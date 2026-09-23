"""Tests for modules/google_integrations.py - the service-account JWT-bearer
token exchange, the GSC/GA4 clients, and the pure join_* helpers.

All HTTP is monkeypatched (no network, no real Google credentials). The token
exchange is exercised against a real RSA keypair generated in-test so the
RS256/PKCS1v15 signing path is covered end-to-end, and the produced JWT is
decoded/verified back to prove the header + claims are well-formed.
"""

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from modules import google_integrations as gi


# --------------------------------------------------------------------------- #
# helpers / fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def rsa_service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    sa = {"client_email": "svc@example-project.iam.gserviceaccount.com", "private_key": pem}
    return sa, key.public_key()


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --------------------------------------------------------------------------- #
# get_access_token
# --------------------------------------------------------------------------- #
def test_get_access_token_builds_valid_signed_jwt(monkeypatch, rsa_service_account):
    sa, public_key = rsa_service_account
    captured = {}

    def fake_post(url, data=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["data"] = data
        return FakeResponse(200, {"access_token": "ya29.fake-token"})

    monkeypatch.setattr(gi.requests, "post", fake_post)

    token = gi.get_access_token(sa, gi.GSC_SCOPE)
    assert token == "ya29.fake-token"

    # POSTed to the token endpoint with the JWT-bearer grant.
    assert captured["url"] == gi.TOKEN_ENDPOINT
    assert captured["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"

    # The assertion is a real, verifiable RS256 JWT.
    assertion = captured["data"]["assertion"]
    header_b64, claims_b64, sig_b64 = assertion.split(".")
    header = json.loads(_b64url_decode(header_b64))
    claims = json.loads(_b64url_decode(claims_b64))
    assert header == {"alg": "RS256", "typ": "JWT"}
    assert claims["iss"] == sa["client_email"]
    assert claims["scope"] == gi.GSC_SCOPE
    assert claims["aud"] == gi.TOKEN_ENDPOINT
    assert claims["exp"] > claims["iat"]

    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    # Raises InvalidSignature if the signature doesn't verify.
    public_key.verify(_b64url_decode(sig_b64), signing_input, padding.PKCS1v15(), hashes.SHA256())


def test_get_access_token_missing_fields_raises():
    with pytest.raises(gi.GoogleIntegrationError, match="client_email"):
        gi.get_access_token({}, gi.GSC_SCOPE)


def test_get_access_token_bad_private_key_raises(monkeypatch):
    sa = {"client_email": "x@y.iam.gserviceaccount.com", "private_key": "not-a-pem"}
    with pytest.raises(gi.GoogleIntegrationError, match="private key"):
        gi.get_access_token(sa, gi.GSC_SCOPE)


def test_get_access_token_token_endpoint_error_raises(monkeypatch, rsa_service_account):
    sa, _ = rsa_service_account

    def fake_post(url, data=None, timeout=None, **kwargs):
        return FakeResponse(400, {"error": "invalid_grant", "error_description": "clock skew"})

    monkeypatch.setattr(gi.requests, "post", fake_post)
    with pytest.raises(gi.GoogleIntegrationError, match="token exchange failed"):
        gi.get_access_token(sa, gi.GSC_SCOPE)


# --------------------------------------------------------------------------- #
# fetch_gsc
# --------------------------------------------------------------------------- #
def test_fetch_gsc_parses_rows(monkeypatch, rsa_service_account):
    sa, _ = rsa_service_account
    monkeypatch.setattr(gi, "get_access_token", lambda *a, **k: "tok")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return FakeResponse(200, {"rows": [
            {"keys": ["https://example.com/a"], "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 3.5},
            {"keys": ["https://example.com/b"], "clicks": 2, "impressions": 50, "ctr": 0.04, "position": 8.0},
        ]})

    monkeypatch.setattr(gi.requests, "post", fake_post)
    rows = gi.fetch_gsc(sa, "https://example.com/", start_date="2024-01-01", end_date="2024-01-28")

    # site_url is URL-encoded into the path.
    assert "https%3A%2F%2Fexample.com%2F" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["body"]["dimensions"] == ["page"]
    assert len(rows) == 2
    assert rows[0] == {"page": "https://example.com/a", "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 3.5}


def test_fetch_gsc_403_gives_actionable_error(monkeypatch, rsa_service_account):
    sa, _ = rsa_service_account
    monkeypatch.setattr(gi, "get_access_token", lambda *a, **k: "tok")

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        return FakeResponse(403, {"error": {"message": "User does not have permission"}})

    monkeypatch.setattr(gi.requests, "post", fake_post)
    with pytest.raises(gi.GoogleIntegrationError, match="lacks access to this GSC property"):
        gi.fetch_gsc(sa, "https://example.com/", start_date="2024-01-01", end_date="2024-01-28")


# --------------------------------------------------------------------------- #
# fetch_ga4
# --------------------------------------------------------------------------- #
def test_fetch_ga4_parses_rows_and_coerces_numbers(monkeypatch, rsa_service_account):
    sa, _ = rsa_service_account
    monkeypatch.setattr(gi, "get_access_token", lambda *a, **k: "tok")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["body"] = json
        return FakeResponse(200, {
            "dimensionHeaders": [{"name": "pagePath"}],
            "metricHeaders": [{"name": "sessions"}, {"name": "screenPageViews"}],
            "rows": [
                {"dimensionValues": [{"value": "/pricing"}], "metricValues": [{"value": "42"}, {"value": "100"}]},
            ],
        })

    monkeypatch.setattr(gi.requests, "post", fake_post)
    rows = gi.fetch_ga4(sa, "properties/123456", start_date="2024-01-01", end_date="2024-01-28")

    # "properties/123456" is normalised to the bare numeric id in the endpoint.
    assert captured["url"].endswith("/properties/123456:runReport")
    assert captured["body"]["dateRanges"] == [{"startDate": "2024-01-01", "endDate": "2024-01-28"}]
    assert rows == [{"pagePath": "/pricing", "sessions": 42, "screenPageViews": 100}]


def test_fetch_ga4_403_gives_actionable_error(monkeypatch, rsa_service_account):
    sa, _ = rsa_service_account
    monkeypatch.setattr(gi, "get_access_token", lambda *a, **k: "tok")

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        return FakeResponse(403, {"error": {"message": "permission denied"}})

    monkeypatch.setattr(gi.requests, "post", fake_post)
    with pytest.raises(gi.GoogleIntegrationError, match="lacks access to this GA4 property"):
        gi.fetch_ga4(sa, "123456", start_date="2024-01-01", end_date="2024-01-28")


# --------------------------------------------------------------------------- #
# join helpers (pure, no network)
# --------------------------------------------------------------------------- #
def test_join_gsc_to_pages_trailing_slash_tolerant():
    gsc_rows = [
        {"page": "https://example.com/a/", "clicks": 5, "impressions": 50, "ctr": 0.1, "position": 2.0},
        {"page": "https://example.com/missing", "clicks": 1, "impressions": 1, "ctr": 1.0, "position": 1.0},
    ]
    page_urls = ["https://example.com/a", "https://example.com/b"]
    joined = gi.join_gsc_to_pages(gsc_rows, page_urls)

    assert set(joined) == {"https://example.com/a"}  # trailing-slash difference still matches
    assert joined["https://example.com/a"]["clicks"] == 5
    assert "page" not in joined["https://example.com/a"]  # dimension key stripped


def test_join_ga4_to_pages_combines_path_with_origin():
    ga4_rows = [
        {"pagePath": "/pricing", "sessions": 42, "screenPageViews": 100},
        {"pagePath": "/about/", "sessions": 3, "screenPageViews": 9},
    ]
    page_urls = ["https://example.com/pricing", "https://example.com/about", "https://example.com/orphan"]
    joined = gi.join_ga4_to_pages(ga4_rows, page_urls, "https://example.com")

    assert set(joined) == {"https://example.com/pricing", "https://example.com/about"}
    assert joined["https://example.com/pricing"]["sessions"] == 42
    assert joined["https://example.com/about"]["screenPageViews"] == 9


def test_join_ga4_handles_origin_with_trailing_slash_and_bare_path():
    ga4_rows = [{"pagePath": "contact", "sessions": 7, "screenPageViews": 8}]  # no leading slash
    page_urls = ["https://example.com/contact"]
    joined = gi.join_ga4_to_pages(ga4_rows, page_urls, "https://example.com/")

    assert joined == {"https://example.com/contact": {"sessions": 7, "screenPageViews": 8}}


def test_normalize_url_root_slash_preserved():
    assert gi._normalize_url("https://example.com/") == "https://example.com/"
    assert gi._normalize_url("https://Example.com/A/") == "https://example.com/A"
