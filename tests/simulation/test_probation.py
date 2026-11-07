"""Probation review as a linked child run (#59, PRD 19 note)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from mocks.app import MockSettings, create_app
from relay.bootstrap import build_engine, in_process_api
from relay.connectors.http import load_connectors
from relay.core.engine.engine import Engine, TaskDecisionRefused
from relay.db import models as m
from relay.seeding.generate import read
from relay.seeding.load import load
from relay.seeding.scenarios import ScenarioPlayer
from tests.simulation.harness import FakeClock, clean

pytestmark = pytest.mark.db


@pytest.fixture()
def onboarded(
    db_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Engine, sessionmaker[Session], FakeClock, uuid.UUID]]:
    sa: SAEngine = create_engine(db_url, pool_size=5)
    clean(sa)
    sessions = sessionmaker(bind=sa, expire_on_commit=False)
    with sessions.begin() as s:
        load(s, read())
    transport = httpx.ASGITransport(app=create_app(MockSettings(slow_seconds=0)))
    clock = FakeClock()
    engine = build_engine(sessions, connectors=load_connectors(transport=transport), clock=clock)
    api = in_process_api(engine, sessions, tmp_path_factory.mktemp("uploads"))
    candidate = next(c for c in read().candidates if c["scenario"] == "clean")
    outcome = ScenarioPlayer(engine, sessions, clock, api).play(candidate)
    assert outcome.end_state == "completed" and outcome.run_id is not None
    yield engine, sessions, clock, outcome.run_id
    sa.dispose()


def _child(sessions: sessionmaker[Session], parent: uuid.UUID) -> m.WorkflowRun:
    with sessions() as s:
        return s.scalars(select(m.WorkflowRun).where(m.WorkflowRun.parent_run_id == parent)).one()


def test_onboarding_completes_and_hands_over_to_a_probation_child_run(onboarded) -> None:  # type: ignore[no-untyped-def]
    _, sessions, _, parent = onboarded
    child = _child(sessions, parent)
    assert child.definition_name == "onboarding_probation"
    assert child.status is m.RunStatus.WAITING  # six months away, and not counted in onboarding
    with sessions() as s:
        onboarding = s.get(m.WorkflowRun, parent)
        assert onboarding is not None and onboarding.status is m.RunStatus.COMPLETED


def test_at_probation_end_only_the_named_manager_can_confirm(onboarded) -> None:  # type: ignore[no-untyped-def]
    engine, sessions, clock, parent = onboarded
    child = _child(sessions, parent)
    clock.advance(days=200)
    engine.run_until_idle()
    with sessions() as s:
        task = s.scalars(select(m.HumanTask).where(m.HumanTask.run_id == child.id)).one()
        other = s.scalars(
            select(m.User).where(
                m.User.roles.contains(["manager"]), m.User.id != task.assignee_user_id
            )
        ).first()
    assert task.kind is m.HumanTaskKind.RESERVED_DECISION and task.assignee_role == "manager"
    assert task.assignee_user_id == uuid.UUID(child.context["ctx"]["manager_user_id"])
    assert other is not None
    with pytest.raises(TaskDecisionRefused):
        engine.decide_task(task.id, user_id=other.id, option="confirm")
    engine.decide_task(task.id, user_id=task.assignee_user_id, option="confirm")
    with sessions() as s:
        done = s.get(m.WorkflowRun, child.id)
        assert done is not None and done.status is m.RunStatus.COMPLETED
