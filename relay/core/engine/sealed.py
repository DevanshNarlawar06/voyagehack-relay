"""Sealed values: sensitive data that rides through a run's context encrypted (PRD 10.1).

A workflow's `ctx` is stored as plain JSONB, so a 🔒 value such as the approved CTC is put there as
`{"$sealed": "<ciphertext>"}`. The engine opens it only when handing inputs to a connector call or to an
in-process skill (whose output is masked); ctx, the ledger, step output and audit only ever see
the sealed form.
"""

from __future__ import annotations

import json
from typing import Any

from relay.db.crypto import decrypt, encrypt

SEALED_KEY = "$sealed"


def seal_value(value: Any) -> dict[str, str]:
    """Encrypt any JSON value into a sealed envelope."""
    return {SEALED_KEY: encrypt(json.dumps(value, sort_keys=True))}


def is_sealed(value: Any) -> bool:
    """True for an envelope made by `seal_value`."""
    return isinstance(value, dict) and set(value) == {SEALED_KEY}


def unseal(value: Any) -> Any:
    """Open every sealed envelope inside `value` (dicts and lists are walked)."""
    if is_sealed(value):
        return json.loads(decrypt(value[SEALED_KEY]))
    if isinstance(value, dict):
        return {k: unseal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unseal(v) for v in value]
    return value
