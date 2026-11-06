"""The API driving the real engine: events start runs, decisions resume them, cancel stops them."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from relay.api.app import create_app
from relay.api.deps import ApiServices, get_session
from relay.api.ratelimit import login_limiter
from relay.config import get_config
from relay.core.audit.verifier import verify_chain
from relay.core.audit.writer import policy_audit_hook
from relay.core.policy import PolicyService
from relay.db import models as m
from tests.simulation.harness import World, build_world

pytestmark = pytest.mark.db


@dataclass
class Stack:
    world: World
    app: TestClient

    def client(self, *roles: str) -> TestClient:
        email = f"{uuid.uuid4().hex[:8]}@relay.test"
        with self.world.sessions.begin() as s:
            s.add(m.User(email=email, display_name=email, roles=list(roles)))
        login_limiter.reset()
        token = self.app.post("/api/v1/auth/demo-login", json={"email": email}).json()["token"]
        return TestClient(self.app.app, headers={"Authorization": f"Bearer {token}"})

    def start(self, grade: str = "G2") -> uuid.UUID:
        return self.world.engine.start_run(
            "toy_onboarding", {"candidate": "c1", "grade": grade}, actor="test", subject_id="c1"
        )

    def to_probation_task(self) -> tuple[uuid.UUID, m.HumanTask]:
        run_id = self.start()
        self.world.engine.run_until_idle()
        assert self.world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
        self.world.engine.run_until_idle()
        (task,) = self.open_tasks(run_id)
        return run_id, task

    def open_tasks(self, run_id: uuid.UUID) -> list[m.HumanTask]:
        with self.world.sessions() as s:
            return list(
                s.scalars(
                    select(m.HumanTask).where(
                        m.HumanTask.run_id == run_id, m.HumanTask.status == m.HumanTaskStatus.OPEN
                    )
                )
            )

    def run_status(self, run_id: uuid.UUID) -> m.RunStatus:
        with self.world.sessions() as s:
            run = s.get(m.WorkflowRun, run_id)
            assert run is not None
            return run.status


@pytest.fixture(scope="module")
def sa_engine(db_url: str) -> Iterator[SAEngine]:
    engine = create_engine(db_url, pool_size=10)
    yield engine
    engine.dispose()


@pytest.fixture
def stack(sa_engine: SAEngine) -> Stack:
    world = build_world(sa_engine)
    app = create_app(
        ApiServices(
            policy=PolicyService(audit=policy_audit_hook),
            config=get_config(),
            engine=world.engine,
        )
    )
    factory: sessionmaker[Session] = world.sessions

    def session() -> Iterator[Session]:
        with factory() as s:
            yield s
            s.commit()

    app.dependency_overrides[get_session] = session
    return Stack(world, TestClient(app))


def test_a_reserved_decision_through_the_api_resumes_and_completes_the_run(stack: Stack) -> None:
    run_id, task = stack.to_probation_task()
    manager = stack.client("manager")
    response = manager.post(f"/api/v1/tasks/{task.id}/decide", json={"option": "confirm"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "decided"
    assert response.json()["decision"] == "confirm"
    assert stack.run_status(run_id) is m.RunStatus.COMPLETED
    with stack.world.sessions() as s:
        assert verify_chain(s).ok


def test_hr_admin_is_refused_a_decision_reserved_for_the_manager(stack: Stack) -> None:
    run_id, task = stack.to_probation_task()
    response = stack.client("hr_admin").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "confirm"}
    )
    assert response.status_code == 403
    assert stack.run_status(run_id) is m.RunStatus.WAITING
    assert stack.open_tasks(run_id)


def test_the_engine_floor_holds_even_when_the_api_checks_pass(stack: Stack) -> None:
    """A row routing `employment.terminate` to a manager must not get past the engine."""
    run_id, task = stack.to_probation_task()
    with stack.world.sessions.begin() as s:
        row = s.get(m.HumanTask, task.id)
        assert row is not None
        row.action = "employment.terminate"
    response = stack.client("manager").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "confirm"}
    )
    assert response.status_code == 403
    assert stack.run_status(run_id) is m.RunStatus.WAITING


def test_a_second_decision_is_a_conflict(stack: Stack) -> None:
    _, task = stack.to_probation_task()
    manager = stack.client("manager")
    path = f"/api/v1/tasks/{task.id}/decide"
    assert manager.post(path, json={"option": "confirm"}).status_code == 200
    assert manager.post(path, json={"option": "confirm"}).status_code == 409


def test_an_expensive_laptop_waits_then_the_approver_releases_it(stack: Stack) -> None:
    run_id = stack.start(grade="G5")
    stack.world.engine.run_until_idle()
    assert stack.world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    stack.world.engine.run_until_idle()
    task = next(t for t in stack.open_tasks(run_id) if t.action == "assets.laptop.allocate")
    assert stack.world.assets.calls["allocate"] == 0
    path = f"/api/v1/tasks/{task.id}/decide"
    assert stack.client("hr_ops").post(path, json={"option": "approve"}).status_code == 403
    assert (
        stack.client("approver_it_spend").post(path, json={"option": "approve"}).status_code == 200
    )
    stack.world.engine.run_until_idle()
    assert stack.world.assets.calls["allocate"] == 1


def test_an_event_starts_a_run_and_the_run_page_shows_it(stack: Stack) -> None:
    client = stack.client("hr_ops")
    response = client.post(
        "/api/v1/events",
        json={
            "event": "toy.hired",
            "subject_id": "c9",
            "payload": {"candidate": "c9", "grade": "G2"},
        },
    )
    assert response.status_code == 202, response.text
    (run_id,) = response.json()["run_ids"]
    stack.world.engine.run_until_idle()
    detail = client.get(f"/api/v1/runs/{run_id}").json()
    assert detail["status"] == "waiting"
    steps = {step["step_id"]: step for step in detail["steps"]}
    assert steps["create_prehire"]["status"] == "SUCCEEDED"
    assert steps["compute_terms"]["needs"] == ["create_prehire"]
    assert steps["create_prehire"]["decision"]["effect"] == "ALLOW"
    assert steps["provision_identity"]["needs"] == ["await_documents"]
    assert steps["order_laptop"]["needs"] == ["select_laptop"]
    assert detail["timeline"]


def test_cancel_through_the_api_stops_the_run_and_its_side_effects(stack: Stack) -> None:
    run_id = stack.start()
    stack.world.engine.run_until_idle()
    assert stack.world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    response = stack.client("hr_admin").post(
        f"/api/v1/runs/{run_id}/cancel", json={"reason": "Candidate withdrew"}
    )
    assert response.status_code == 204, response.text
    stack.world.engine.run_until_idle()
    assert stack.run_status(run_id) is m.RunStatus.CANCELLED
    assert stack.world.directory.calls["create"] == 0


def test_a_decision_on_a_paused_run_waits_for_release(stack: Stack) -> None:
    run_id, task = stack.to_probation_task()
    admin = stack.client("hr_admin")
    assert (
        admin.post(
            "/api/v1/kill-switches",
            json={"scope": "workflow", "target": "toy_onboarding", "active": True},
        ).status_code
        == 200
    )
    with stack.world.sessions() as s:
        stack.world.engine.policy.kill_switches(s, fresh=True)
    manager = stack.client("manager")
    path = f"/api/v1/tasks/{task.id}/decide"
    assert manager.post(path, json={"option": "confirm"}).status_code == 403
    admin.post(
        "/api/v1/kill-switches",
        json={"scope": "workflow", "target": "toy_onboarding", "active": False},
    )
    with stack.world.sessions() as s:
        stack.world.engine.policy.kill_switches(s, fresh=True)
    assert manager.post(path, json={"option": "confirm"}).status_code == 200
    assert stack.run_status(run_id) is m.RunStatus.COMPLETED


def test_a_task_on_a_run_is_refused_when_no_engine_is_running(
    stack: Stack, sa_engine: SAEngine
) -> None:
    """Recording it would look done while the step waits forever."""
    _, task = stack.to_probation_task()
    services: ApiServices = stack.app.app.state.services  # type: ignore[attr-defined]
    services.engine = None
    response = stack.client("manager").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "confirm"}
    )
    assert response.status_code == 503
