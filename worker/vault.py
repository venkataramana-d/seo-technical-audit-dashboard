"""Encryption primitives for the API-key vault (Phase 5,
05-INFRASTRUCTURE-AND-OPS.md §4: "encrypt provider secrets at rest").

Uses Fernet (AES-128-CBC + HMAC, from the already-installed `cryptography`
package) rather than hand-rolled crypto. The doc's own suggestion - "AES-256-
GCM with a KMS-managed key" - assumes a KMS this local-dev setup doesn't
have (the same class of gap as Postgres/Redis being stood in for locally);
Fernet keyed by an env var is the same tier of tradeoff, using a real,
audited implementation rather than approximating one.

`VAULT_ENCRYPTION_KEY` is read at call time, not import time, so modules
that import this file but never actually touch the vault aren't broken by
it being unset. There is deliberately no fallback: a missing key must fail
loudly, never silently store plaintext or encrypt under a throwaway
key that would make existing rows undecryptable after a restart.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_ENV_VAR = "VAULT_ENCRYPTION_KEY"


def _get_fernet() -> Fernet:
    key = os.environ.get(_ENV_VAR)
    if not key:
        raise RuntimeError(
            f"{_ENV_VAR} is not set - generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and set it as an environment variable before saving or reading vaulted API keys."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except ValueError as exc:
        raise RuntimeError(f"{_ENV_VAR} is not a valid Fernet key: {exc}") from exc


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt vaulted value - VAULT_ENCRYPTION_KEY may have changed since it was saved."
        ) from exc
