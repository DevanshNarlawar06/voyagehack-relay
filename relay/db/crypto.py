"""Application-level encryption for sensitive columns (PRD 10, 14.2).

AES-256-GCM with a random 96-bit nonce per value. The key is `RELAY_ENCRYPTION_KEY` (urlsafe
base64 of 32 bytes) from the environment or `.env`. Stored format: ``v1:<urlsafe-b64(nonce || ciphertext+tag)>``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from relay.config import get_config, get_settings

_log = logging.getLogger(__name__)
_PREFIX = "v1:"
_DEV_KEY = base64.urlsafe_b64encode(b"relay-dev-only-key-do-not-use!!!").decode()


def generate_key() -> str:
    """Return a new random key suitable for `RELAY_ENCRYPTION_KEY`."""
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()


_warned_dev_key = False


class MissingEncryptionKey(RuntimeError):
    """No RELAY_ENCRYPTION_KEY is configured and demo mode is off."""


def _configured_key() -> str:
    """Key from the environment or `.env` (via Settings). Falls back to the dev key only in demo
    mode, with a warning; otherwise refuses to run (issue #26)."""
    raw = get_settings().relay_encryption_key
    if raw:
        return raw
    if get_config().app.demo_mode:
        global _warned_dev_key
        if not _warned_dev_key:  # once per process, not on every encrypt/decrypt
            _log.warning(
                "RELAY_ENCRYPTION_KEY is not set; using the public dev key (demo mode only)"
            )
            _warned_dev_key = True
        return _DEV_KEY
    raise MissingEncryptionKey(
        "RELAY_ENCRYPTION_KEY is not set. Generate one with `relay config gen-key` and put it in .env"
    )


def _key_bytes(key: str | None = None) -> bytes:
    raw = key or _configured_key()
    decoded = base64.urlsafe_b64decode(raw)
    if len(decoded) != 32:
        raise ValueError("RELAY_ENCRYPTION_KEY must be urlsafe base64 of exactly 32 bytes")
    return decoded


def encrypt(plaintext: str, key: str | None = None) -> str:
    """Encrypt a string; every call uses a fresh nonce, so equal inputs give different outputs."""
    nonce = os.urandom(12)
    ct = AESGCM(_key_bytes(key)).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token: str, key: str | None = None) -> str:
    """Decrypt a value produced by `encrypt`. Raises on tampering or a wrong key."""
    if not token.startswith(_PREFIX):
        raise ValueError("unknown ciphertext format")
    blob = base64.urlsafe_b64decode(token[len(_PREFIX) :])
    return AESGCM(_key_bytes(key)).decrypt(blob[:12], blob[12:], None).decode("utf-8")


class EncryptedString(TypeDecorator[str]):
    """String column transparently encrypted at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else decrypt(value)


class EncryptedJSON(TypeDecorator[Any]):
    """JSON value transparently encrypted at rest (stored as text)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else encrypt(json.dumps(value, sort_keys=True, default=str))

    def process_result_value(self, value: str | None, dialect: Dialect) -> Any:
        return None if value is None else json.loads(decrypt(value))
