"""Issue #78: DPDP data requests run as a workflow added only through extension points.

A person raises access, correction and erasure requests through the real API route. Real engine,
rules, policy and connectors to the mock service in process, real Postgres; only the clock is fake.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from mocks.app import MockSettings, create_app
from relay.bootstrap import build_engine, in_process_api
from relay.connectors.http import load_connectors
from relay.core.engine.engine import Engine, TaskDecisionRefused
from relay.core.engine.sealed import is_sealed
from relay.db import models as m
from relay.seeding.generate import read
from relay.seeding.load import load
from tests.simulation.harness import FakeClock, clean

pytestmark = pytest.mark.db

REQUESTER = "neha.singh@relay.test"  # a seeded employee with a person record


@dataclass
class World:
    engine: Engine
    sessions: sessionmaker[Session]
    api: TestClient
    mocks: TestClient
    token: str


@pytest.fixture(scope="module")
def world(db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    sa: SAEngine = create_engine(db_url, pool_size=5)
    clean(sa)
    sessions = sessionmaker(bind=sa, expire_on_commit=False)
    with sessions.begin() as s:
        load(s, read())
    app = create_app(MockSettings(slow_seconds=0, timeout_seconds=0))
    engine = build_engine(
        sessions,
        connectors=load_connectors(transport=httpx.ASGITransport(app=app)),
        clock=FakeClock(),
    )
    api = in_process_api(engine, sessions, Path(tmp_path_factory.mktemp("uploads")))
    token = api.post("/api/v1/auth/demo-login", json={"email": REQUESTER}).json()["token"]
    yield World(engine, sessions, api, TestClient(app), token)
    sa.dispose()


def _raise(world: World, kind: str) -> tuple[uuid.UUID, uuid.UUID]:
    response = world.api.post(
        "/api/v1/data-requests",
        json={"type": kind},
        headers={"Authorization": f"Bearer {world.token}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run_id"], body  # the route started the workflow
    world.engine.run_until_idle()
    return uuid.UUID(body["id"]), uuid.UUID(body["run_id"])


def _state(world: World, request_id: uuid.UUID, run_id: uuid.UUID) -> tuple[str, str, dict]:  # type: ignore[type-arg]
    with world.sessions() as s:
        row = s.get(m.DataRequest, request_id)
        run = s.get(m.WorkflowRun, run_id)
        assert row is not None and run is not None
        return row.status, run.status.value, run.context["ctx"]


def _task(world: World, run_id: uuid.UUID) -> m.HumanTask:
    with world.sessions() as s:
        task = s.scalar(select(m.HumanTask).where(m.HumanTask.run_id == run_id))
        assert task is not None
        return task


def _user(world: World, email: str) -> uuid.UUID:
    with world.sessions() as s:
        user = s.scalar(select(m.User).where(m.User.email == email))
        assert user is not None
        return user.id


def test_an_access_request_builds_a_sealed_export_and_tells_the_person(world: World) -> None:
    request_id, run_id = _raise(world, "access")
    status, run_status, ctx = _state(world, request_id, run_id)
    assert (status, run_status) == ("completed", "completed")
    assert is_sealed(ctx["export"]["package"])  # the record itself never sits in ctx in plain text
    assert ctx["export"]["summary"]["consents"] >= 0
    bodies = [x["body"] for x in world.mocks.get("/notify/messages").json()["items"]]
    assert any("data export is ready" in b for b in bodies)


def test_a_correction_is_decided_by_hr_ops_and_a_rejection_needs_a_reason(world: World) -> None:
    request_id, run_id = _raise(world, "correction")
    task = _task(world, run_id)
    assert task.assignee_role == "hr_ops" and task.options == ["approve", "reject"]
    hr_ops = _user(world, "hr.ops@relay.test")
    with pytest.raises(TaskDecisionRefused):
        world.engine.decide_task(task.id, user_id=hr_ops, option="reject")  # no reason
    world.engine.decide_task(
        task.id, user_id=hr_ops, option="reject", reason="The record already matches the PAN card"
    )
    world.engine.run_until_idle()
    status, run_status, ctx = _state(world, request_id, run_id)
    assert (status, run_status) == ("rejected", "completed")
    assert ctx["closed"]["outcome"] == "reject"


def test_erasure_is_reserved_for_hr_admin(world: World) -> None:
    request_id, run_id = _raise(world, "erasure")
    task = _task(world, run_id)
    assert task.kind is m.HumanTaskKind.RESERVED_DECISION and task.assignee_role == "hr_admin"
    with pytest.raises(TaskDecisionRefused):
        world.engine.decide_task(task.id, user_id=_user(world, "hr.ops@relay.test"), option="erase")
    world.engine.decide_task(
        task.id,
        user_id=_user(world, "hr.admin@relay.test"),
        option="refuse_retention",
        reason="Payroll records must be kept for 8 years",
    )
    world.engine.run_until_idle()
    status, run_status, ctx = _state(world, request_id, run_id)
    assert (status, run_status) == ("rejected", "completed")
    assert ctx["closed"]["outcome"] == "refuse_retention"
