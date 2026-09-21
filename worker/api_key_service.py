"""Service layer for the API-key vault (Phase 5), mirroring
worker/crawl_service.py's shape. Encryption itself lives in worker/vault.py;
this file owns DB access and the one hard rule: `list_api_keys()` never
returns a decrypted value - only `get_api_key()` does, for server-side use
by the existing PSI/Groq consumers (api/audit-pipeline.py, api/ai.py).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from worker import vault
from worker.db.models import ApiKey, Organization
from worker.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Matches 00-PLAN-OVERVIEW.md's named providers. gsc/ga4/openai/anthropic/
# gemini can be saved/deleted like any other row; only psi/groq have a real
# "Test Connection" check today (see api/api-keys.py) since only those two
# have existing integration code to validate against.
KNOWN_PROVIDERS = ("psi", "groq", "gsc", "ga4", "openai", "anthropic", "gemini")


def get_or_create_default_org(db) -> Organization:
    """Same "Local Dev" org worker/crawl_service.py's get_or_create_default_
    project() already creates/reuses - kept as its own helper here since the
    vault has no reason to touch Project at all."""
    org = db.execute(select(Organization).where(Organization.name == "Local Dev")).scalar_one_or_none()
    if org is not None:
        return org
    org = Organization(name="Local Dev", plan_tier="free")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _validate_provider(provider: str) -> None:
    if provider not in KNOWN_PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r} (expected one of {KNOWN_PROVIDERS})")


def set_api_key(db, org_id: int, provider: str, plaintext_value: str, created_by: str | None = None) -> ApiKey:
    _validate_provider(provider)
    if not plaintext_value:
        raise ValueError("plaintext_value must be non-empty")

    encrypted_value = vault.encrypt(plaintext_value)
    existing = db.execute(
        select(ApiKey).where(ApiKey.org_id == org_id, ApiKey.provider == provider)
    ).scalar_one_or_none()

    if existing is not None:
        existing.encrypted_value = encrypted_value
        existing.created_by = created_by
        db.commit()
        db.refresh(existing)
        return existing

    row = ApiKey(org_id=org_id, provider=provider, encrypted_value=encrypted_value, created_by=created_by)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_api_key(db, org_id: int, provider: str) -> str | None:
    """Decrypts for server-side use only (e.g. calling PSI/Groq on the
    caller's behalf) - never expose this value back over an API response."""
    row = db.execute(select(ApiKey).where(ApiKey.org_id == org_id, ApiKey.provider == provider)).scalar_one_or_none()
    if row is None:
        return None
    return vault.decrypt(row.encrypted_value)


def _masked_preview(decrypted_value: str) -> str:
    tail = decrypted_value[-4:] if len(decrypted_value) >= 4 else decrypted_value
    return f"{'•' * 8}{tail}"


def list_api_keys(db, org_id: int) -> list[dict]:
    """Provider + created_at + a masked preview only. Decrypts internally
    just to compute the last-4-chars preview - the decrypted value itself
    never leaves this function."""
    rows = db.execute(select(ApiKey).where(ApiKey.org_id == org_id)).scalars().all()
    result = []
    for row in rows:
        try:
            preview = _masked_preview(vault.decrypt(row.encrypted_value))
        except RuntimeError:
            preview = "•" * 12  # VAULT_ENCRYPTION_KEY changed since save; still report the row exists
        result.append({"provider": row.provider, "createdAt": row.created_at, "maskedPreview": preview})
    return result


def delete_api_key(db, org_id: int, provider: str) -> bool:
    row = db.execute(select(ApiKey).where(ApiKey.org_id == org_id, ApiKey.provider == provider)).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def get_default_org_vaulted_key(provider: str) -> str | None:
    """Convenience wrapper for the two existing PSI/Groq consumers
    (api/audit-pipeline.py, api/ai.py) - opens its own short session, so
    callers don't need to import SessionLocal/get_or_create_default_org
    themselves just to slot the vault into their existing key-precedence
    chain (`payload.get(...) or get_default_org_vaulted_key(...) or
    os.environ.get(...)`).

    Fails closed, not loud: those two callers ran with zero DB dependency
    before the vault existed, and a single ad-hoc audit/AI request must
    never break (e.g. worker/dev.db not yet migrated, or unreachable)
    just because an optional, additive convenience layer couldn't reach the
    database. Any failure here is treated the same as "no vault entry
    configured" - the existing per-request-key/env-var fallbacks still
    apply unchanged.
    """
    try:
        with SessionLocal() as db:
            org = get_or_create_default_org(db)
            return get_api_key(db, org.id, provider)
    except Exception:  # noqa: BLE001 - see docstring: fail closed, never break the caller
        logger.warning("vault lookup failed for provider=%s; falling back to env var", provider, exc_info=True)
        return None
