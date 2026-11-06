"""Idempotency ledger for exactly-once side effects (PRD 11.5).

Protocol for every connector op with `side_effect: true`:
1. `key = sha256(f"{run_id}:{step_id}:{operation}")`, deliberately not attempt-scoped.
2. `claim()` inserts the row as `pending` and COMMITS before the external call, so a crash after
   the call is visible to whoever retries. An existing row is returned as is.
3. The caller passes `key` as `Idempotency-Key` / `client_reference`.
4. `complete()` marks the row succeeded in the same transaction that marks the step SUCCEEDED.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from relay.db.models import IdempotencyLedger, LedgerStatus


def idempotency_key(run_id: UUID, step_id: str, operation: str) -> str:
    """The ledger key for one side effect of one step of one run."""
    return hashlib.sha256(f"{run_id}:{step_id}:{operation}".encode()).hexdigest()


def request_hash(payload: dict[str, Any]) -> str:
    """Hash of the request body, to notice a changed payload under the same key."""
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    key: str
    status: LedgerStatus
    external_id: str | None
    response: dict[str, Any] | None
    fresh: bool  # True if this claim created the row


def claim(
    session: Session, key: str, connector: str, operation: str, payload: dict[str, Any]
) -> LedgerEntry:
    """Insert a pending row (or read the existing one). The caller must commit before calling out."""
    inserted = session.execute(
        insert(IdempotencyLedger)
        .values(
            key=key,
            connector=connector,
            operation=operation,
            request_hash=request_hash(payload),
            status=LedgerStatus.PENDING,
        )
        .on_conflict_do_nothing(index_elements=[IdempotencyLedger.key])
        .returning(IdempotencyLedger.id)
    ).first()
    row = session.scalar(select(IdempotencyLedger).where(IdempotencyLedger.key == key))
    assert row is not None
    return LedgerEntry(key, row.status, row.external_id, row.response, inserted is not None)


def complete(
    session: Session,
    key: str,
    *,
    external_id: str | None,
    response: dict[str, Any],
    at: datetime,
) -> None:
    """Mark the side effect done (same transaction as the step's SUCCEEDED)."""
    row = session.scalar(
        select(IdempotencyLedger).where(IdempotencyLedger.key == key).with_for_update()
    )
    assert row is not None
    row.status = LedgerStatus.SUCCEEDED
    row.external_id = external_id
    row.response = response
    row.completed_at = at


def fail_permanently(session: Session, key: str, at: datetime) -> None:
    """Record that the side effect will not happen (permanent error)."""
    row = session.scalar(select(IdempotencyLedger).where(IdempotencyLedger.key == key))
    if row is not None and row.status is LedgerStatus.PENDING:
        row.status = LedgerStatus.FAILED_PERMANENT
        row.completed_at = at
