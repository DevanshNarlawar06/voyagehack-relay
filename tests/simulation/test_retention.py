"""PRD 14: the retention sweeper purges what retention.yaml no longer allows, and nothing else.

Real engine, rules and Postgres; documents and their encrypted files are made here. Every expired
case is dated around 2000, so no seeded document (dated 2026) can be swept by this test.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mocks.app import MockSettings, create_app
from relay.bootstrap import build_engine, start_retention_sweep
from relay.connectors.http import load_connectors
from relay.core.audit.verifier import verify_chain
from relay.core.engine.engine import Engine
from relay.db import models as m
from relay.rules.retention import PURGED, keep_until
from tests.simulation.harness import FakeClock, clean

pytestmark = pytest.mark.db

AS_OF = "2001-01-01"
LONG_AGO = datetime(1999, 1, 1, tzinfo=UTC)


@dataclass
class World:
    engine: Engine
    sessions: sessionmaker[Session]
    storage: Path


@pytest.fixture(scope="module")
def world(db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    storage = tmp_path_factory.mktemp("documents")
    previous = os.environ.get("RELAY_STORAGE_DIR")
    os.environ["RELAY_STORAGE_DIR"] = str(storage)
    sa: SAEngine = create_engine(db_url, pool_size=5)
    clean(sa)
    sessions = sessionmaker(bind=sa, expire_on_commit=False)
    mocks = create_app(MockSettings(slow_seconds=0, timeout_seconds=0))
    connectors = load_connectors(transport=httpx.ASGITransport(app=mocks))
    engine = build_engine(sessions, connectors=connectors, clock=FakeClock())
    yield World(engine, sessions, storage)
    if previous is None:
        os.environ.pop("RELAY_STORAGE_DIR", None)
    else:
        os.environ["RELAY_STORAGE_DIR"] = previous
    sa.dispose()


def _document(
    world: World,
    category: m.DocumentCategory,
    state: m.EmployeeState | None,
    last_day: date | None = None,
    uploaded: datetime | None = None,
    verified_at: str | None = None,
    with_file: bool = True,
) -> uuid.UUID:
    """A person, maybe an employee, and one document with a file on disk."""
    ref = uuid.uuid4().hex
    if with_file:
        (world.storage / ref).write_text("ciphertext", encoding="ascii")
    with world.sessions.begin() as s:
        person = m.Person(full_name="Retention Case")
        s.add(person)
        s.flush()
        if state is not None:
            s.add(
                m.Employee(
                    person_id=person.id,
                    employee_code=f"RET-{ref[:8]}",
                    state=state,
                    last_working_day=last_day,
                )
            )
        doc = m.Document(
            person_id=person.id,
            category=category,
            storage_ref=ref,
            extracted={"name": "Retention Case"},
            validation={"verified_at": verified_at} if verified_at else None,
        )
        if uploaded is not None:
            doc.created_at = uploaded
        s.add(doc)
        s.flush()
        return doc.id


def _sweep(world: World) -> dict[str, object]:
    [run_id] = start_retention_sweep(world.engine, AS_OF)  # what `relay retention-sweep` does
    world.engine.run_until_idle()
    with world.sessions() as s:
        run = s.get(m.WorkflowRun, run_id)
        assert run is not None and run.status is m.RunStatus.COMPLETED, run and run.status
        ctx: dict[str, object] = run.context["ctx"]
        return ctx


def _doc(world: World, doc_id: uuid.UUID) -> m.Document:
    with world.sessions() as s:
        doc = s.get(m.Document, doc_id)
        assert doc is not None
        return doc


def test_the_sweeper_purges_only_expired_documents_and_audits_each(world: World) -> None:
    left = _document(world, m.DocumentCategory.PHOTO, m.EmployeeState.EXITED, date(2000, 1, 1))
    verified = _document(
        world, m.DocumentCategory.AADHAAR, m.EmployeeState.ACTIVE, verified_at="1999-06-01"
    )
    unverified = _document(  # uploaded long ago, never verified: counting from upload is too early
        world, m.DocumentCategory.AADHAAR, m.EmployeeState.ACTIVE, uploaded=LONG_AGO
    )
    lost_file = _document(  # expired, but its file is not where the sweeper looks
        world, m.DocumentCategory.PHOTO, m.EmployeeState.EXITED, date(2000, 1, 1), with_file=False
    )
    working = _document(world, m.DocumentCategory.PAN, m.EmployeeState.ACTIVE)
    no_exit_date = _document(world, m.DocumentCategory.PHOTO, m.EmployeeState.EXITED)
    no_employee = _document(world, m.DocumentCategory.PHOTO, None)
    recent_leaver = _document(
        world, m.DocumentCategory.PAN, m.EmployeeState.EXITED, date(2000, 6, 1)
    )  # 8 years after exit: kept

    files = {d: _doc(world, d).storage_ref for d in (left, verified)}
    _doc(world, unverified)
    ctx = _sweep(world)

    for gone in (left, verified):
        doc = _doc(world, gone)
        assert doc.storage_ref == PURGED and doc.extracted is None
        assert not (world.storage / files[gone]).exists()  # the encrypted file is deleted
    for kept in (working, no_exit_date, no_employee, recent_leaver, unverified):
        doc = _doc(world, kept)
        assert doc.storage_ref != PURGED and doc.extracted == {"name": "Retention Case"}
        assert (world.storage / doc.storage_ref).exists()
    assert _doc(world, recent_leaver).retention_until == date(2008, 5, 30)
    purge = ctx["purge"]
    assert purge["count"] == 3 and purge["files_missing"] == 1  # type: ignore[index]
    found = {d["document_id"]: d["file_found"] for d in purge["documents"]}  # type: ignore[index]
    assert found == {str(left): True, str(verified): True, str(lost_file): False}
    with world.sessions() as s:
        assert verify_chain(s).ok


def test_a_second_sweep_purges_nothing_more(world: World) -> None:
    ctx = _sweep(world)
    assert ctx["expired"]["count"] == 0  # type: ignore[index]
    assert "purge" not in ctx  # the purge step was skipped


# ---------------------------------------------------------------- the pure rule


def test_keep_until_follows_the_category_rule() -> None:
    exit_rule = {"while": ["active"], "after_exit_days": 30}
    assert keep_until(exit_rule, "active", None, date(2020, 1, 1)) is None  # still employed
    assert keep_until(exit_rule, "exited", None, date(2020, 1, 1)) is None  # no exit date: keep
    assert keep_until(exit_rule, "exited", date(2024, 1, 1), date(2020, 1, 1)) == date(2024, 1, 31)
    assert keep_until(exit_rule, None, date(2024, 1, 1), date(2020, 1, 1)) is None  # no employee
    verify_rule = {"while": ["onboarding"], "after_verification_days": 30}
    assert keep_until(verify_rule, "active", None, date(2024, 1, 1)) == date(2024, 1, 31)
    assert keep_until(verify_rule, "active", None, None) is None  # never verified: keep
    assert keep_until({"while": []}, "exited", date(2024, 1, 1), date(2020, 1, 1)) is None
