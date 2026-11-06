"""Audit chain tests (WP2): tampering in any form is detected and concurrent appends stay linked."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from relay.cli import app
from relay.core.audit.chain import GENESIS_HASH, compute_hash
from relay.core.audit.verifier import VerifyResult, verify_chain
from relay.core.audit.writer import AuditEntry, append_event
from relay.db.models import ActorType, AuditEvent


def entry(action: str = "test.event", **overrides: Any) -> AuditEntry:
    fields: dict[str, Any] = {
        "actor_type": ActorType.SYSTEM,
        "actor_id": "engine",
        "action": action,
        **overrides,
    }
    return AuditEntry(**fields)


def test_hash_is_stable_and_covers_every_change() -> None:
    content = {"id": "1", "action": "a", "prev_hash": GENESIS_HASH, "data": {"b": 1, "a": 2}}
    same = {"data": {"a": 2, "b": 1}, "prev_hash": GENESIS_HASH, "action": "a", "id": "1"}
    assert compute_hash(content) == compute_hash(same)
    assert compute_hash({**content, "action": "b"}) != compute_hash(content)
    assert compute_hash({**content, "prev_hash": "1" * 64}) != compute_hash(content)
    assert compute_hash({**content, "data": {"b": 1, "a": 3}}) != compute_hash(content)


def test_hash_treats_one_instant_in_two_zones_as_equal() -> None:
    utc = datetime(2027, 1, 4, 4, 30, tzinfo=UTC)
    ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert compute_hash({"ts": utc}) == compute_hash({"ts": ist})


db = pytest.mark.db


def _bypass_trigger(session: Session) -> None:
    """Act as an attacker with owner rights. ALTER is transactional, so the test rollback undoes it."""
    session.execute(text("ALTER TABLE audit_events DISABLE TRIGGER USER"))


def _chain(session: Session, count: int = 3) -> list[AuditEvent]:
    return [append_event(session, entry(f"test.event.{i}")) for i in range(count)]


@db
def test_events_link_to_the_previous_head(db_session: Session) -> None:
    first, second = _chain(db_session, 2)
    assert second.prev_hash == first.hash
    assert second.seq > first.seq
    result = verify_chain(db_session)
    assert result.ok
    assert result.head_seq == second.seq
    assert result.head_hash == second.hash


@db
def test_every_field_survives_the_database_round_trip(db_session: Session) -> None:
    """A hash taken before insert must still match after JSONB, UUID and timestamp conversion."""
    append_event(
        db_session,
        entry(
            on_behalf_of="hr_ops",
            subject_type="employee",
            subject_id=str(uuid.uuid4()),
            effect="ALLOW",
            workflow_def_version="onboarding@1",
            policy_version="approvals@3",
            skill_version="2",
            prompt_version="v2",
            model_id="mock/deterministic",
            input_hash="a" * 64,
            masked_payload_ref=uuid.uuid4(),
            reasoning="Grade L5 → standard laptop",
            data={
                "amount": Decimal("12.50"),
                "when": datetime(2027, 1, 4, tzinfo=UTC),
                "n": [1, 2.5],
            },
        ),
    )
    db_session.expire_all()
    assert verify_chain(db_session).ok


@db
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("action", "'employment.terminate'"),
        ("actor_id", "'someone_else'"),
        ("effect", "'ALLOW'"),
        ("reasoning", "'rewritten'"),
        ("data", """'{"amount_inr": 1}'::jsonb"""),
        ("ts", "now() - interval '1 day'"),
        ("policy_version", "'approvals@99'"),
    ],
)
def test_changing_any_field_is_detected(db_session: Session, column: str, value: str) -> None:
    _first, second, _third = _chain(db_session)
    _bypass_trigger(db_session)
    db_session.execute(
        text(f"UPDATE audit_events SET {column} = {value} WHERE seq = :seq"), {"seq": second.seq}
    )
    result = verify_chain(db_session)
    assert not result.ok
    assert result.first_bad_seq == second.seq
    assert result.problem == "content_changed"


@db
def test_rewriting_a_row_and_its_hash_breaks_the_next_link(db_session: Session) -> None:
    _first, second, _third = _chain(db_session)
    _bypass_trigger(db_session)
    db_session.execute(
        text("UPDATE audit_events SET hash = :hash WHERE seq = :seq"),
        {"hash": "f" * 64, "seq": second.seq},
    )
    result = verify_chain(db_session)
    assert not result.ok
    assert result.first_bad_seq == second.seq


@db
def test_a_forger_who_rehashes_one_row_still_breaks_the_next_link(db_session: Session) -> None:
    _first, second, third = _chain(db_session)
    forged = compute_hash(
        {
            "id": second.id,
            "ts": second.ts,
            "actor_type": second.actor_type,
            "actor_id": second.actor_id,
            "action": "forged.action",
            "data": second.data,
            "prev_hash": second.prev_hash,
        }
    )
    _bypass_trigger(db_session)
    db_session.execute(
        text("UPDATE audit_events SET action = 'forged.action', hash = :hash WHERE seq = :seq"),
        {"hash": forged, "seq": second.seq},
    )
    result = verify_chain(db_session)
    assert not result.ok
    assert result.first_bad_seq == third.seq
    assert result.problem == "broken_link"


@db
def test_deleting_a_row_is_detected(db_session: Session) -> None:
    _first, second, third = _chain(db_session)
    _bypass_trigger(db_session)
    db_session.execute(text("DELETE FROM audit_events WHERE seq = :seq"), {"seq": second.seq})
    result = verify_chain(db_session)
    assert not result.ok
    assert result.first_bad_seq == third.seq
    assert result.problem == "broken_link"


@db
def test_inserting_a_row_is_detected(db_session: Session) -> None:
    first, _second, _third = _chain(db_session)
    db_session.add(
        AuditEvent(
            actor_type=ActorType.HUMAN,
            actor_id="intruder",
            action="forged",
            prev_hash=first.hash,
            hash="e" * 64,
        )
    )
    db_session.flush()
    result = verify_chain(db_session)
    assert not result.ok


@db
def test_cut_tail_is_visible_only_against_a_stored_head(db_session: Session) -> None:
    _first, second, third = _chain(db_session)
    stored_head = verify_chain(db_session).head_hash
    assert stored_head == third.hash
    _bypass_trigger(db_session)
    db_session.execute(text("DELETE FROM audit_events WHERE seq = :seq"), {"seq": third.seq})
    result = verify_chain(db_session)
    assert result.ok
    assert result.head_hash == second.hash != stored_head


@db
def test_a_rolled_back_append_leaves_a_seq_gap_that_is_not_a_break(db_engine: Engine) -> None:
    factory = sessionmaker(bind=db_engine)
    with factory() as session:
        append_event(session, entry())
        session.commit()
    with factory() as session:
        append_event(session, entry("rolled.back"))
        session.rollback()
    with factory() as session:
        append_event(session, entry())
        session.commit()
        assert verify_chain(session).ok


@db
def test_concurrent_writers_keep_one_unbroken_chain(db_engine: Engine) -> None:
    factory = sessionmaker(bind=db_engine)

    def write(worker: int) -> None:
        for i in range(10):
            with factory() as session:
                append_event(session, entry(f"worker.{worker}.{i}"))
                session.commit()

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(write, range(6)))
    with factory() as session:
        result = verify_chain(session)
    assert result.ok, result
    assert result.checked >= 60


def test_cli_exit_code_follows_the_result() -> None:
    runner = CliRunner()
    good = VerifyResult(ok=True, checked=3, head_seq=3, head_hash="a" * 64)
    bad = VerifyResult(ok=False, checked=1, first_bad_seq=2, problem="content_changed")
    with (
        patch("relay.core.audit.verifier.verify_chain", return_value=good),
        patch("relay.db.session.session_scope"),
    ):
        result = runner.invoke(app, ["audit", "verify"])
        assert result.exit_code == 0
        assert "3 events" in result.output
    with (
        patch("relay.core.audit.verifier.verify_chain", return_value=bad),
        patch("relay.db.session.session_scope"),
    ):
        result = runner.invoke(app, ["audit", "verify"])
        assert result.exit_code == 1
        assert "seq 2" in result.output


@db
def test_cli_verifies_a_real_database(db_url: str) -> None:
    result = CliRunner().invoke(app, ["audit", "verify", "--url", db_url])
    assert result.exit_code == 0, result.output
