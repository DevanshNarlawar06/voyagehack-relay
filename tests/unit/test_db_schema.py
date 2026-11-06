"""Schema behaviour against a real Postgres (PRD 10, WP1 acceptance)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from relay.db.models import (
    APPEND_ONLY_TABLES,
    ActorType,
    AuditEvent,
    Person,
    StepKind,
    StepRun,
    WorkflowRun,
)

pytestmark = pytest.mark.db


def _audit(session: Session) -> AuditEvent:
    ev = AuditEvent(
        actor_type=ActorType.SYSTEM,
        actor_id="engine",
        action="test.event",
        prev_hash="0" * 64,
        hash=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    session.add(ev)
    session.flush()
    return ev


def test_migration_created_every_table(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        }
    assert {"audit_events", "step_runs", "idempotency_ledger", "persons"} <= names


@pytest.mark.parametrize(
    "stmt", ["UPDATE {t} SET created_at = now()", "DELETE FROM {t}", "TRUNCATE {t}"]
)
@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_append_only_tables_reject_mutation(db_engine: Engine, table: str, stmt: str) -> None:
    seed = {
        "audit_events": [
            "INSERT INTO audit_events (actor_type, actor_id, action, data, prev_hash, hash) "
            f"VALUES ('system','t','t','{{}}', '{'0' * 64}', '{uuid.uuid4().hex * 2}')"
        ],
        "model_payloads": [
            "INSERT INTO model_payloads (direction, masked_text, provider, token_count) "
            "VALUES ('out', '<PERSON_1>', 'mock', 3)"
        ],
        "lifecycle_events": [
            "INSERT INTO persons (id, full_name) VALUES ('00000000-0000-0000-0000-000000000001', 'P')",
            "INSERT INTO employees (id, person_id, state) VALUES "
            "('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000001', 'pre_hire')",
            "INSERT INTO lifecycle_events (employee_id, event_type, payload) "
            "VALUES ('00000000-0000-0000-0000-000000000002', 'created', '{}')",
        ],
    }
    with db_engine.connect() as conn, conn.begin():
        for sql in seed[table]:
            conn.execute(text(sql))
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text(stmt.format(t=table)))


def test_audit_seq_is_monotonic(db_session: Session) -> None:
    a, b = _audit(db_session), _audit(db_session)
    assert b.seq > a.seq


def test_sensitive_values_are_ciphertext_in_db(db_session: Session) -> None:
    p = Person(full_name="Asha Rao", pan="ABCDE1234F")
    db_session.add(p)
    db_session.flush()
    raw = db_session.execute(
        text("SELECT pan FROM persons WHERE id = :i"), {"i": p.id}
    ).scalar_one()
    assert raw.startswith("v1:") and "ABCDE1234F" not in raw
    db_session.expire(p)
    assert p.pan == "ABCDE1234F"


def test_step_run_unique_per_run(db_session: Session) -> None:
    run = WorkflowRun(
        definition_name="toy", definition_version=1, subject_type="employee",
        subject_id="e1", started_by_actor="system:test",
    )  # fmt: skip
    db_session.add(run)
    db_session.flush()
    db_session.add(StepRun(run_id=run.id, step_id="a", kind=StepKind.RULE))
    db_session.flush()
    db_session.add(StepRun(run_id=run.id, step_id="a", kind=StepKind.RULE))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_enum_constraint_rejects_unknown_status(db_engine: Engine) -> None:
    with db_engine.connect() as conn, conn.begin(), pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO workflow_runs (definition_name, definition_version, subject_type, "
                "subject_id, status, context, started_by_actor) "
                "VALUES ('x', 1, 'employee', 'e', 'exploded', '{}', 's')"
            )
        )
