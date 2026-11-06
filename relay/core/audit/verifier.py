"""Walks the audit chain and reports the first break (`relay audit verify`, GET /audit/verify)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.core.audit.chain import CHAINED_FIELDS, GENESIS_HASH, compute_hash
from relay.db.models import AuditEvent


class VerifyResult(BaseModel):
    """Outcome of a chain walk. Keep `head_hash` somewhere else to detect a cut tail later."""

    ok: bool
    checked: int
    head_seq: int | None = None
    head_hash: str | None = None
    first_bad_seq: int | None = None
    problem: Literal["content_changed", "broken_link"] | None = None


def verify_chain(session: Session, *, batch_size: int = 1000) -> VerifyResult:
    """Recompute every event's hash in sequence order.

    Finds a changed field (content_changed) and a removed, inserted or reordered row (broken_link).
    Gaps in `seq` alone are normal, since a rolled-back transaction still consumes a value.
    A chain cannot prove its own last rows were not cut off: compare `head_hash` to a stored copy.
    """
    expected_prev = GENESIS_HASH
    checked = 0
    head_seq: int | None = None
    columns = [getattr(AuditEvent, name) for name in CHAINED_FIELDS]
    query = select(AuditEvent.seq, AuditEvent.hash, *columns).order_by(AuditEvent.seq)
    for row in session.execute(query.execution_options(yield_per=batch_size)):
        fields = row._mapping
        content = {name: fields[name] for name in CHAINED_FIELDS}
        if fields["prev_hash"] != expected_prev:
            return _failure(checked, head_seq, expected_prev, fields["seq"], "broken_link")
        if compute_hash(content) != fields["hash"]:
            return _failure(checked, head_seq, expected_prev, fields["seq"], "content_changed")
        expected_prev = fields["hash"]
        head_seq = fields["seq"]
        checked += 1
    head_hash = None if checked == 0 else expected_prev
    return VerifyResult(ok=True, checked=checked, head_seq=head_seq, head_hash=head_hash)


def _failure(
    checked: int,
    head_seq: int | None,
    last_good_hash: str,
    bad_seq: int,
    problem: Literal["content_changed", "broken_link"],
) -> VerifyResult:
    return VerifyResult(
        ok=False,
        checked=checked,
        head_seq=head_seq,
        head_hash=None if checked == 0 else last_good_hash,
        first_bad_seq=bad_seq,
        problem=problem,
    )
