"""The hash chain itself: which fields are covered and how they are hashed (PRD 6.6, 10.3)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

GENESIS_HASH = "0" * 64

# `seq` is assigned by the database after hashing; order is proven by the prev_hash links instead.
CHAINED_FIELDS = (
    "id",
    "ts",
    "actor_type",
    "actor_id",
    "on_behalf_of",
    "action",
    "subject_type",
    "subject_id",
    "run_id",
    "step_run_id",
    "effect",
    "workflow_def_version",
    "policy_version",
    "skill_version",
    "prompt_version",
    "model_id",
    "input_hash",
    "masked_payload_ref",
    "reasoning",
    "data",
    "prev_hash",
)


def _canonical_value(value: Any) -> Any:
    """Reduce a value to the form it has after a round trip through Postgres."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)


def compute_hash(content: dict[str, Any]) -> str:
    """Return the sha256 of the chained fields of an event, including its `prev_hash`."""
    canonical = {name: _canonical_value(content.get(name)) for name in CHAINED_FIELDS}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
