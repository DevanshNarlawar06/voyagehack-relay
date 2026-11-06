"""Append-only audit writer. This is the only code path that creates `audit_events` rows."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from relay.core.audit.chain import CHAINED_FIELDS, GENESIS_HASH, compute_hash
from relay.db.models import ActorType, AuditEvent

# Arbitrary fixed key for pg_advisory_xact_lock: appends form one global chain.
_CHAIN_LOCK = 7_203_114_601


class AuditEntry(BaseModel):
    """What happened. The writer adds id, timestamp and hashes. Never put 🔒 fields in here."""

    actor_type: ActorType
    actor_id: str
    action: str
    on_behalf_of: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    run_id: uuid.UUID | None = None
    step_run_id: uuid.UUID | None = None
    effect: str | None = None
    workflow_def_version: str | None = None
    policy_version: str | None = None
    skill_version: str | None = None
    prompt_version: str | None = None
    model_id: str | None = None
    input_hash: str | None = None
    masked_payload_ref: uuid.UUID | None = None
    reasoning: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


def append_event(session: Session, entry: AuditEntry) -> AuditEvent:
    """Add one event to the chain inside the caller's transaction and flush it.

    The advisory lock is held until that transaction ends, so concurrent writers line up and each
    links to a committed head. Keep audited transactions short: they serialize on this lock.
    """
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK})
    head = session.scalar(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))
    content: dict[str, Any] = entry.model_dump()
    # JSONB drops type information, so the hash is taken over what will actually be read back.
    content["data"] = json.loads(json.dumps(content["data"], default=str))
    content["id"] = uuid.uuid4()
    content["ts"] = datetime.now(UTC)
    content["prev_hash"] = head or GENESIS_HASH
    event = AuditEvent(
        **{name: content[name] for name in CHAINED_FIELDS}, hash=compute_hash(content)
    )
    session.add(event)
    session.flush()
    return event


def policy_audit_hook(session: Session, event: Mapping[str, Any]) -> None:
    """Write a `PolicyService` audit record to the chain. Pass as `PolicyService(audit=...)`."""
    append_event(session, AuditEntry.model_validate(dict(event)))
