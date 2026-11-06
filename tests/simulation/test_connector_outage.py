"""The real engine, connectors and mock service together (#13): outage to task, and exactly once.

Unlike test_engine.py, nothing here is a fake except the clock. Requests go through HttpConnector
to the mock FastAPI app, in process.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from mocks.app import MockSettings, create_app
from relay.connectors.http import load_connectors
from relay.core.engine.engine import Engine
from relay.core.engine.ports import EngineDeps
from relay.core.policy import PolicyService
from relay.core.registry.store import DefinitionStore
from relay.db import models as m
from tests.simulation.harness import FakeClock, clean

pytestmark = pytest.mark.db

WORKFLOW = """
name: connector_check
version: 1
subject: employee
trigger: { event: test.connector_check }
context:
  code: "trigger.code"
  consented_purposes: "['payroll_setup']"
steps:
  - id: order_laptop
    kind: connector
    uses: assets.allocate_laptop
    action: assets.laptop.allocate
    inputs: { employee_code: "ctx.code", sku: "'LAT-14'" }
    attributes: { amount_inr: "90000" }
    retry: { max_attempts: 3, base_seconds: 1 }
  - id: issue_card
    kind: connector
    uses: access.issue_card
    action: access.card.issue
    needs: [order_laptop]
    inputs: { employee_code: "ctx.code", photo_ref: "'photo-1'" }
    retry: { max_attempts: 3, base_seconds: 1 }
  - id: setup_payroll
    kind: connector
    uses: payroll.setup_employee
    action: payroll.employee.setup
    needs: [issue_card]
    inputs: { employee_code: "ctx.code", ctc_inr: "1200000" }
    attributes: { ctc_matches_offer: "true" }
    retry: { max_attempts: 3, base_seconds: 1 }
"""


@dataclass
class Stack:
    engine: Engine
    clock: FakeClock
    mocks: TestClient
    sessions: sessionmaker[Session]

    def start(self) -> uuid.UUID:
        return self.engine.start_run(
            "connector_check",
            {"code": f"E-{uuid.uuid4().hex[:6]}"},
            actor="test",
            subject_id=str(uuid.uuid4()),
        )

    def fault(self, system: str, fault: str, **scope: Any) -> None:
        response = self.mocks.post(
            "/_control/faults", json={"system": system, "fault": fault, **scope}
        )
        assert response.status_code == 201, response.text

    def records(self, system: str) -> int:
        return int(self.mocks.get("/_control/stats").json()[system]["records"])

    def step(self, run_id: uuid.UUID, step_id: str) -> m.StepRun:
        with self.sessions() as s:
            return s.scalars(
                select(m.StepRun).where(m.StepRun.run_id == run_id, m.StepRun.step_id == step_id)
            ).one()

    def tasks(self, run_id: uuid.UUID) -> list[m.HumanTask]:
        with self.sessions() as s:
            return list(
                s.scalars(
                    select(m.HumanTask).where(
                        m.HumanTask.run_id == run_id, m.HumanTask.status == m.HumanTaskStatus.OPEN
                    )
                )
            )

    def run_status(self, run_id: uuid.UUID) -> m.RunStatus:
        with self.sessions() as s:
            run = s.get(m.WorkflowRun, run_id)
            assert run is not None
            return run.status

    def hr_ops(self) -> uuid.UUID:
        with self.sessions.begin() as s:
            user = m.User(
                email=f"{uuid.uuid4().hex[:8]}@relay.test", display_name="HR", roles=["hr_ops"]
            )
            s.add(user)
            s.flush()
            return user.id

    def drain_retries(self) -> None:
        """Let every scheduled retry come due."""
        for _ in range(6):
            self.engine.run_until_idle()
            self.clock.advance(minutes=15)
        self.engine.run_until_idle()


@pytest.fixture(scope="module")
def sa_engine(db_url: str) -> Iterator[SAEngine]:
    engine = create_engine(db_url, pool_size=5)
    yield engine
    engine.dispose()


@pytest.fixture
def stack(sa_engine: SAEngine) -> Stack:
    clean(sa_engine)
    app = create_app(MockSettings(slow_seconds=0, timeout_seconds=0))
    connectors = load_connectors(transport=httpx.ASGITransport(app=app))
    clock = FakeClock()
    deps = EngineDeps(connectors=connectors, clock=clock)  # type: ignore[arg-type]
    sessions = sessionmaker(bind=sa_engine, expire_on_commit=False)
    store = DefinitionStore()
    with sessions.begin() as s:
        store.register(s, WORKFLOW, policies=PolicyService().policies(s), deps=deps, actor="test")
    engine = Engine(sessions, deps, definitions=store, worker_id="w1")
    return Stack(engine, clock, TestClient(app), sessions)


def test_healthy_systems_complete_with_one_record_each(stack: Stack) -> None:
    run_id = stack.start()
    stack.engine.run_until_idle()
    assert stack.run_status(run_id) is m.RunStatus.COMPLETED
    assert (stack.records("assets"), stack.records("access"), stack.records("payroll")) == (1, 1, 1)


def test_a_503_storm_parks_the_step_and_opens_an_outage_task(stack: Stack) -> None:
    """WP5 acceptance: 503 storm -> retries -> park -> outage task -> recovery, no duplicates."""
    stack.fault("assets", "503")
    run_id = stack.start()
    stack.drain_retries()

    step = stack.step(run_id, "order_laptop")
    assert step.status is m.StepStatus.PARKED
    assert step.attempt == 3
    assert step.error is not None
    assert "HTTP 503" in step.error["reason"]
    (task,) = stack.tasks(run_id)
    assert task.kind is m.HumanTaskKind.OUTAGE
    assert task.assignee_role == "hr_ops"
    assert stack.records("assets") == 0
    assert stack.step(run_id, "issue_card").status is m.StepStatus.PENDING

    stack.mocks.delete("/_control/faults")
    stack.engine.decide_task(task.id, user_id=stack.hr_ops(), option="retry")
    stack.engine.run_until_idle()
    assert stack.step(run_id, "order_laptop").status is m.StepStatus.SUCCEEDED
    assert stack.run_status(run_id) is m.RunStatus.COMPLETED
    assert stack.records("assets") == 1


def test_a_short_outage_recovers_on_its_own(stack: Stack) -> None:
    stack.fault("assets", "503", remaining=2)
    run_id = stack.start()
    stack.drain_retries()
    assert stack.run_status(run_id) is m.RunStatus.COMPLETED
    assert stack.tasks(run_id) == []
    assert stack.records("assets") == 1


@pytest.mark.parametrize(
    ("system", "step_id", "op"),
    [
        ("assets", "order_laptop", "allocate_laptop"),  # header mode
        ("access", "issue_card", "issue_card"),  # no idempotency: only reconcile protects it
        ("payroll", "setup_payroll", "setup_employee"),  # natural key
    ],
)
def test_a_lost_acknowledgement_never_duplicates(
    stack: Stack, system: str, step_id: str, op: str
) -> None:
    """The side effect lands, the response is lost: the retry must find it, for all three modes."""
    stack.fault(system, "timeout_after_commit", op=op, remaining=1)
    run_id = stack.start()
    stack.drain_retries()
    assert stack.run_status(run_id) is m.RunStatus.COMPLETED
    assert stack.records(system) == 1
    assert stack.step(run_id, step_id).status is m.StepStatus.SUCCEEDED


def test_a_refused_request_parks_as_an_exception_not_an_outage(stack: Stack) -> None:
    # A number where the mock requires a string: its 422 is permanent, so no retries.
    bad = WORKFLOW.replace("sku: \"'LAT-14'\"", 'sku: "1"').replace(
        "name: connector_check", "name: connector_bad"
    )
    assert 'sku: "1"' in bad
    with stack.sessions.begin() as s:
        stack.engine.definitions.register(
            s,
            bad,
            policies=stack.engine.policy.policies(s),
            deps=stack.engine.deps,
            actor="test",
        )
    run_id = stack.engine.start_run(
        "connector_bad", {"code": "E-bad"}, actor="test", subject_id=str(uuid.uuid4())
    )
    stack.drain_retries()
    assert stack.step(run_id, "order_laptop").status is m.StepStatus.PARKED
    (task,) = stack.tasks(run_id)
    assert task.kind is m.HumanTaskKind.EXCEPTION
    assert stack.step(run_id, "order_laptop").attempt == 0
