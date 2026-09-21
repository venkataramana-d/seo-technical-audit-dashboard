"""Tests for worker/vault.py - the encryption primitives behind the API-key
vault. Uses monkeypatch.setenv/delenv so no test leaks VAULT_ENCRYPTION_KEY
state into another test."""

import pytest
from cryptography.fernet import Fernet

from worker import vault


def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())

    token = vault.encrypt("my-secret-value")

    assert token != "my-secret-value"  # not plaintext
    assert vault.decrypt(token) == "my-secret-value"


def test_missing_env_var_raises_clear_error(monkeypatch):
    monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)

    with pytest.raises(RuntimeError, match="VAULT_ENCRYPTION_KEY is not set"):
        vault.encrypt("anything")


def test_invalid_key_format_raises_clear_error(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", "not-a-valid-fernet-key")

    with pytest.raises(RuntimeError, match="not a valid Fernet key"):
        vault.encrypt("anything")


def test_decrypting_with_a_different_key_fails(monkeypatch):
    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    token = vault.encrypt("my-secret-value")

    monkeypatch.setenv("VAULT_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="Could not decrypt"):
        vault.decrypt(token)
