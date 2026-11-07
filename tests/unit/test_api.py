"""API tests: the role allow/deny matrix (WP8 acceptance) and each route's behaviour."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mocks.app import MockSettings
from mocks.app import create_app as create_mocks
from relay.api.app import create_app
from relay.api.deps import ApiServices, get_session, issue_token
from relay.api.ratelimit import SlidingWindowLimiter, login_limiter
from relay.api.views import definition_graph
from relay.config import get_config
from relay.connectors.faults import HttpFaultControl
from relay.connectors.http import load_connectors
from relay.core.audit.verifier import verify_chain
from relay.core.audit.writer import policy_audit_hook
from relay.core.errors import TransientError
from relay.core.policy import PolicyService
from relay.db import models as m

pytestmark = pytest.mark.db

ROLES = [
    "hr_admin",
    "hr_ops",
    "manager",
    "auditor",
    "developer",
    "employee",
    "new_joiner",
    "approver_it_spend",
    "it_ops",
]
GRAPH_YAML = """
name: onboarding
version: 1
subject: employee
trigger: { event: test.graph }
steps:
  - { id: a, kind: rule, uses: rules.a, action: x.a }
  - parallel:
      id: group
      needs: [a]
      branches:
        - - { id: b, kind: rule, uses: rules.b, action: x.b }
          - { id: c, kind: rule, uses: rules.c, action: x.c }
        - - { id: d, kind: rule, uses: rules.d, action: x.d }
  - { id: e, kind: rule, uses: rules.e, action: x.e, needs: [group] }
"""


@dataclass
class FakeEngine:
    """Records what the API asks of the engine."""

    started: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    cancelled: list[UUID] = field(default_factory=list)
    decided: list[UUID] = field(default_factory=list)

    def handle_event(
        self, event: str, payload: Mapping[str, Any], *, actor: str, subject_id: str
    ) -> list[UUID]:
        self.started.append((event, dict(payload)))
        return [uuid.uuid4()]

    def cancel_run(self, run_id: UUID, *, actor: str, reason: str) -> None:
        self.cancelled.append(run_id)

    def decide_task(
        self,
        task_id: UUID,
        *,
        user_id: UUID,
        option: str,
        reason: str | None = None,
        output: Mapping[str, Any] | None = None,
    ) -> None:
        self.decided.append(task_id)


class Api:
    """A test client per role, against one rolled-back transaction."""

    def __init__(self, session: Session, engine: FakeEngine | None) -> None:
        self.session = session
        self.engine = engine
        self.mocks = create_mocks(MockSettings(slow_seconds=0, timeout_seconds=0))
        transport = httpx.ASGITransport(app=self.mocks)
        self.app = create_app(
            ApiServices(
                policy=PolicyService(audit=policy_audit_hook),
                config=get_config(),
                connectors=dict(load_connectors(transport=transport)),
                faults=HttpFaultControl("http://mocks.test", transport),
                engine=engine,
            )
        )
        self.app.dependency_overrides[get_session] = lambda: session
        self.anonymous = TestClient(self.app)
        self.users: dict[str, m.User] = {}

    def user(self, *roles: str, email: str | None = None, person_id: UUID | None = None) -> m.User:
        name = email or f"{'-'.join(roles) or 'none'}@test.example"
        if name not in self.users:
            row = m.User(email=name, display_name=name, roles=list(roles), person_id=person_id)
            self.session.add(row)
            self.session.flush()
            self.users[name] = row
        return self.users[name]

    def client(self, *roles: str, **kwargs: Any) -> TestClient:
        user = self.user(*roles, **kwargs)
        login_limiter.reset()
        response = self.anonymous.post("/api/v1/auth/demo-login", json={"email": user.email})
        assert response.status_code == 200, response.text
        return TestClient(self.app, headers={"Authorization": f"Bearer {response.json()['token']}"})

    def run(self, **fields: Any) -> m.WorkflowRun:
        values: dict[str, Any] = {
            "definition_name": "onboarding",
            "definition_version": 1,
            "subject_type": "employee",
            "subject_id": str(uuid.uuid4()),
            "status": m.RunStatus.RUNNING,
            "started_by_actor": "system:test",
            **fields,
        }
        run = m.WorkflowRun(**values)
        self.session.add(run)
        self.session.flush()
        return run

    def task(self, **fields: Any) -> m.HumanTask:
        values: dict[str, Any] = {
            "kind": m.HumanTaskKind.CONFLICT,
            "action": "employee.verify",
            "assignee_role": "hr_ops",
            "options": ["accept", "reject"],
            "context_packet": {"summary": "Names differ", "suggested_option": "accept"},
            **fields,
        }
        task = m.HumanTask(**values)
        self.session.add(task)
        self.session.flush()
        return task


@pytest.fixture
def api(db_session: Session) -> Iterator[Api]:
    yield Api(db_session, FakeEngine())


FIXED_ID = "00000000-0000-0000-0000-000000000001"

# method, path, body, roles that get past authorization
MATRIX: list[tuple[str, str, dict[str, Any] | None, set[str]]] = [
    ("GET", "/runs", None, {"hr_admin", "hr_ops", "manager", "auditor", "developer"}),
    ("GET", f"/runs/{FIXED_ID}", None, {"hr_admin", "hr_ops", "manager", "auditor", "developer"}),
    ("POST", f"/runs/{FIXED_ID}/cancel", None, {"hr_admin"}),
    (
        "POST",
        "/events",
        {"event": "candidate.offer_accepted", "subject_id": "c1"},
        {"hr_admin", "hr_ops"},
    ),
    (
        "GET",
        "/tasks",
        None,
        {"hr_admin", "hr_ops", "manager", "approver_it_spend", "it_ops"},
    ),
    ("GET", "/metrics/summary", None, {"hr_admin", "hr_ops", "auditor"}),
    ("GET", "/policies", None, {"hr_admin", "hr_ops", "auditor"}),
    ("GET", "/policies/approvals", None, {"hr_admin", "hr_ops", "auditor"}),
    ("PUT", "/policies/approvals/draft", {"content": "pack: approvals"}, {"hr_admin"}),
    ("POST", "/policies/approvals/test", None, {"hr_admin"}),
    ("POST", "/policies/approvals/activate", None, {"hr_admin"}),
    ("POST", "/policies/approvals/rollback", {"version": 1}, {"hr_admin"}),
    ("GET", "/kill-switches", None, {"hr_admin", "hr_ops", "auditor"}),
    ("POST", "/kill-switches", {"scope": "skill", "target": "x", "active": False}, {"hr_admin"}),
    ("GET", "/connectors/health", None, {"hr_admin", "hr_ops", "auditor", "it_ops"}),
    ("GET", "/controls/faults", None, {"hr_admin", "hr_ops", "auditor", "it_ops"}),
    ("POST", "/controls/faults", {"system": "assets", "fault": "503"}, {"hr_admin"}),
    ("DELETE", "/controls/faults", None, {"hr_admin"}),
    ("GET", "/audit", None, {"hr_admin", "auditor"}),
    ("GET", "/audit/verify", None, {"hr_admin", "auditor"}),
    ("GET", f"/audit/{FIXED_ID}", None, {"hr_admin", "auditor"}),
    ("GET", "/definitions", None, {"hr_admin", "developer"}),
    ("POST", "/data-requests", {"type": "access"}, {"hr_admin", "hr_ops", "manager", "employee"}),
    ("GET", "/data-requests", None, set(ROLES)),  # scoped, not refused: see the test below
    ("GET", "/employees", None, {"hr_admin", "hr_ops", "auditor"}),
    ("GET", f"/employees/{FIXED_ID}", None, {"hr_admin", "hr_ops", "auditor"}),
]


def _matrix_cases() -> list[tuple[str, str, dict[str, Any] | None, str, bool]]:
    return [
        (method, path, body, role, role in allowed)
        for method, path, body, allowed in MATRIX
        for role in ROLES
    ]


@pytest.mark.parametrize(
    ("method", "path", "body", "role", "allowed"),
    _matrix_cases(),
    ids=[f"{c[0]} {c[1]} {c[3]}" for c in _matrix_cases()],
)
def test_role_matrix(
    api: Api, method: str, path: str, body: dict[str, Any] | None, role: str, allowed: bool
) -> None:
    response = api.client(role).request(method, f"/api/v1{path}", json=body)
    if allowed:
        assert response.status_code != 403, response.text
        assert response.status_code != 401
    else:
        assert response.status_code == 403, response.text


@pytest.mark.parametrize(("method", "path", "body", "_allowed"), MATRIX)
def test_every_protected_route_needs_a_token(
    api: Api, method: str, path: str, body: dict[str, Any] | None, _allowed: set[str]
) -> None:
    assert api.anonymous.request(method, f"/api/v1{path}", json=body).status_code == 401


@pytest.mark.parametrize("header", ["Bearer nonsense", "Basic abc", "Bearer ", "bearer x"])
def test_bad_tokens_are_401(api: Api, header: str) -> None:
    assert api.anonymous.get("/api/v1/runs", headers={"Authorization": header}).status_code == 401


def test_token_signed_with_another_secret_is_401(api: Api) -> None:
    import jwt

    user = api.user("hr_admin")
    forged = jwt.encode(
        {"sub": user.email}, "not-the-secret-not-the-secret-not-the", algorithm="HS256"
    )
    response = api.anonymous.get("/api/v1/runs", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_roles_come_from_the_database_not_the_token(api: Api) -> None:
    client = api.client("hr_admin", email="demoted@test.example")
    assert client.get("/api/v1/audit").status_code == 200
    api.users["demoted@test.example"].roles = ["employee"]
    api.session.flush()
    assert client.get("/api/v1/audit").status_code == 403


def test_a_refusal_is_on_record(api: Api) -> None:
    client = api.client("employee")
    before = api.session.scalar(select(func.count()).select_from(m.Decision))
    assert client.get("/api/v1/audit").status_code == 403
    after = api.session.scalar(select(func.count()).select_from(m.Decision))
    assert after == (before or 0) + 1
    last = api.session.scalars(
        select(m.AuditEvent).order_by(m.AuditEvent.seq.desc()).limit(1)
    ).one()
    assert last.action == "policy.authorize:audit.read"
    assert last.effect == "DENY"
    assert verify_chain(api.session).ok


def test_openapi_is_published(api: Api) -> None:
    spec = api.anonymous.get("/openapi.json").json()
    for path in ("/api/v1/runs", "/api/v1/tasks/{task_id}/decide", "/api/v1/metrics/summary"):
        assert path in spec["paths"]


def test_demo_login_rejects_unknown_users_and_is_rate_limited(api: Api) -> None:
    login_limiter.reset()
    assert api.anonymous.post("/api/v1/auth/demo-login", json={"email": "x@y.z"}).status_code == 401
    for _ in range(29):
        api.anonymous.post("/api/v1/auth/demo-login", json={"email": "x@y.z"})
    assert api.anonymous.post("/api/v1/auth/demo-login", json={"email": "x@y.z"}).status_code == 429
    login_limiter.reset()


def test_demo_routes_vanish_outside_demo_mode(db_session: Session) -> None:
    api = Api(db_session, None)
    config = get_config()
    services: ApiServices = api.app.state.services
    services.config = config.model_copy(
        update={"app": config.app.model_copy(update={"demo_mode": False})}
    )
    assert api.anonymous.get("/api/v1/auth/demo-users").status_code == 404
    assert api.anonymous.post("/api/v1/auth/demo-login", json={"email": "a@b.c"}).status_code == 404


def test_the_dev_jwt_secret_is_refused_outside_demo_mode() -> None:
    config = get_config()
    no_demo = config.model_copy(update={"app": config.app.model_copy(update={"demo_mode": False})})
    with pytest.raises(RuntimeError, match="RELAY_JWT_SECRET"):
        issue_token("a@b.c", no_demo)


def test_limiter_window_slides() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(2, 10, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 10.0
    assert limiter.allow("a")


def test_definition_graph_expands_parallel_blocks() -> None:
    graph = definition_graph(GRAPH_YAML)
    assert graph["b"].needs == ["a"]
    assert graph["c"].needs == ["b"]
    assert graph["d"].needs == ["a"]
    assert graph["e"].needs == ["b", "c", "d"]
    assert graph["e"].action == "x.e"


@pytest.mark.parametrize("text", [None, "", ": not yaml: [", "- a list", "steps: [1, 2]"])
def test_definition_graph_survives_bad_input(text: str | None) -> None:
    assert definition_graph(text) == {}


def test_runs_list_and_filter(api: Api) -> None:
    client = api.client("hr_ops")
    running = api.run(context={"subject_label": "Test Joiner"})
    api.session.add(
        m.StepRun(
            run_id=running.id, step_id="a", kind=m.StepKind.RULE, status=m.StepStatus.SUCCEEDED
        )
    )
    api.session.add(m.StepRun(run_id=running.id, step_id="b", kind=m.StepKind.RULE))
    api.session.add(m.CostEvent(run_id=running.id, kind=m.CostKind.LLM, cost_inr=1.25))
    api.run(status=m.RunStatus.FAILED)
    api.session.flush()
    listed = {r["id"]: r for r in client.get("/api/v1/runs").json()}
    row = listed[str(running.id)]
    assert row["subject_label"] == "Test Joiner"
    assert (row["steps_done"], row["steps_total"], row["cost_inr"]) == (1, 2, "1.2500")
    failed = client.get("/api/v1/runs", params={"status": "failed"}).json()
    assert str(running.id) not in {r["id"] for r in failed}
    assert client.get("/api/v1/runs", params={"status": "nope"}).status_code == 422


def test_manager_sees_only_their_team(api: Api) -> None:
    person = m.Person(full_name="Boss")
    other = m.Person(full_name="Report")
    api.session.add_all([person, other])
    api.session.flush()
    boss = m.Employee(person_id=person.id)
    api.session.add(boss)
    api.session.flush()
    report = m.Employee(person_id=other.id, manager_id=boss.id)
    api.session.add(report)
    api.session.flush()
    mine = api.run(subject_id=str(report.id))
    theirs = api.run()
    client = api.client("manager", email="boss@test.example", person_id=person.id)
    ids = {r["id"] for r in client.get("/api/v1/runs").json()}
    assert str(mine.id) in ids
    assert str(theirs.id) not in ids
    assert client.get(f"/api/v1/runs/{mine.id}").status_code == 200
    assert client.get(f"/api/v1/runs/{theirs.id}").status_code == 404


def test_run_detail_explains_each_step(api: Api) -> None:
    api.session.add(
        m.WorkflowDefinition(name="onboarding", version=99, content_hash="x", yaml=GRAPH_YAML)
    )
    run = api.run(definition_version=99)
    decision = m.Decision(
        request={},
        effect=m.Effect.ALLOW,
        rule_ids=["allow"],
        policy_version="approvals@1",
        reason="all policy checks passed",
    )
    api.session.add(decision)
    api.session.flush()
    step = m.StepRun(
        run_id=run.id,
        step_id="e",
        kind=m.StepKind.SKILL,
        status=m.StepStatus.PARKED,
        decision_id=decision.id,
        error={"reason": "assets: HTTP 503"},
    )
    api.session.add(step)
    api.session.flush()
    api.session.add(
        m.CostEvent(run_id=run.id, step_run_id=step.id, kind=m.CostKind.LLM, cost_inr=2)
    )
    api.session.flush()
    body = api.client("hr_ops").get(f"/api/v1/runs/{run.id}").json()
    node = body["steps"][0]
    assert node["needs"] == ["b", "c", "d"]
    assert node["action"] == "x.e"
    assert node["decision"]["rule_ids"] == ["allow"]
    assert node["cost_inr"] == "2.0000"
    assert node["error"] == "assets: HTTP 503"
    assert body["cost_inr"] == "2.0000"


def test_run_not_found(api: Api) -> None:
    assert api.client("hr_ops").get(f"/api/v1/runs/{uuid.uuid4()}").status_code == 404


def test_inbox_shows_only_my_roles_and_open_tasks(api: Api) -> None:
    mine = api.task(sla_due_at=datetime.now(UTC) + timedelta(hours=1))
    urgent = api.task(sla_due_at=datetime.now(UTC))
    other = api.task(assignee_role="manager")
    closed = api.task(status=m.HumanTaskStatus.DECIDED)
    ids = [t["id"] for t in api.client("hr_ops").get("/api/v1/tasks").json()]
    assert ids.index(str(urgent.id)) < ids.index(str(mine.id))
    assert str(other.id) not in ids
    assert str(closed.id) not in ids


def test_decide_records_audits_and_resumes(api: Api) -> None:
    task = api.task()
    client = api.client("hr_ops")
    response = client.post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "decided"
    assert response.json()["decision"] == "accept"
    assert api.engine is not None
    assert api.engine.decided == []  # no run, so nothing to resume
    event = api.session.scalars(
        select(m.AuditEvent).order_by(m.AuditEvent.seq.desc()).limit(1)
    ).one()
    assert event.action == "task.decide:employee.verify"
    assert event.actor_id == "human:hr_ops@test.example"
    assert event.data["option"] == "accept"
    assert verify_chain(api.session).ok


@pytest.mark.parametrize("option", ["reject"])
def test_rejecting_needs_a_reason(api: Api, option: str) -> None:
    task = api.task()
    client = api.client("hr_ops")
    path = f"/api/v1/tasks/{task.id}/decide"
    assert client.post(path, json={"option": option}).status_code == 422
    assert client.post(path, json={"option": option, "reason": "   "}).status_code == 422
    response = client.post(path, json={"option": option, "reason": "Forged scan"})
    assert response.status_code == 200
    assert response.json()["reason"] == "Forged scan"


def test_unknown_option_is_refused(api: Api) -> None:
    task = api.task()
    response = api.client("hr_ops").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "approve_all"}
    )
    assert response.status_code == 422


def test_deciding_twice_is_a_conflict_and_resumes_once(api: Api) -> None:
    task = api.task()
    client = api.client("hr_ops")
    path = f"/api/v1/tasks/{task.id}/decide"
    assert client.post(path, json={"option": "accept"}).status_code == 200
    assert client.post(path, json={"option": "accept"}).status_code == 409


def test_a_task_on_a_run_goes_to_the_engine(api: Api) -> None:
    run = api.run()
    step = m.StepRun(
        run_id=run.id, step_id="s", kind=m.StepKind.RULE, status=m.StepStatus.WAITING_HUMAN
    )
    api.session.add(step)
    api.session.flush()
    task = api.task(run_id=run.id, step_run_id=step.id)
    client = api.client("hr_ops")
    client.post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"})
    assert api.engine is not None
    assert api.engine.decided == [task.id]


def test_the_api_checks_still_run_before_the_engine(api: Api) -> None:
    run = api.run()
    task = api.task(run_id=run.id, assignee_role="manager")
    client = api.client("hr_ops")
    assert (
        client.post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}).status_code == 403
    )
    assert api.engine is not None
    assert api.engine.decided == []


def test_another_role_cannot_decide(api: Api) -> None:
    task = api.task(assignee_role="manager")
    response = api.client("hr_ops").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}
    )
    assert response.status_code == 403
    api.session.refresh(task)
    assert task.status is m.HumanTaskStatus.OPEN


def test_hr_admin_cannot_take_a_reserved_decision_routed_to_someone_else(api: Api) -> None:
    """PRD 6.3: even the role that holds `*` does not get a reserved decision by default."""
    task = api.task(
        kind=m.HumanTaskKind.RESERVED_DECISION,
        action="employment.resignation.accept",
        assignee_role="manager",
    )
    response = api.client("hr_admin").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}
    )
    assert response.status_code == 403
    api.session.refresh(task)
    assert task.status is m.HumanTaskStatus.OPEN


def test_the_routed_human_can_take_a_reserved_decision(api: Api) -> None:
    task = api.task(
        kind=m.HumanTaskKind.RESERVED_DECISION,
        action="employment.resignation.accept",
        assignee_role="manager",
    )
    response = api.client("manager").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}
    )
    assert response.status_code == 200, response.text


def test_a_reserved_task_routed_to_a_role_the_floor_disagrees_with_is_refused(api: Api) -> None:
    """A task row cannot widen the floor: the baseline says who decides, not the task's role."""
    task = api.task(
        kind=m.HumanTaskKind.RESERVED_DECISION,
        action="employment.terminate",  # baseline approver is hr_admin
        assignee_role="manager",
    )
    response = api.client("manager").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}
    )
    assert response.status_code == 403
    api.session.refresh(task)
    assert task.status is m.HumanTaskStatus.OPEN


def test_an_approval_needs_the_approver(api: Api) -> None:
    task = api.task(
        kind=m.HumanTaskKind.APPROVAL,
        action="assets.laptop.allocate",
        assignee_role="approver_it_spend",
    )
    assert (
        api.client("hr_ops")
        .post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"})
        .status_code
        == 403
    )
    assert (
        api.client("approver_it_spend")
        .post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"})
        .status_code
        == 200
    )


def test_a_task_for_a_named_person_is_for_them_alone(api: Api) -> None:
    """PRD 2: reserved decisions go to a named human, not to anyone sharing the role."""
    named = api.client("manager", email="named@test.example")
    other = api.client("manager", email="other@test.example")
    task = api.task(
        kind=m.HumanTaskKind.RESERVED_DECISION,
        action="employment.resignation.accept",
        assignee_role="manager",
        assignee_user_id=api.users["named@test.example"].id,
    )
    assert str(task.id) not in {t["id"] for t in other.get("/api/v1/tasks").json()}
    assert str(task.id) in {t["id"] for t in named.get("/api/v1/tasks").json()}
    path = f"/api/v1/tasks/{task.id}/decide"
    assert other.post(path, json={"option": "accept"}).status_code == 403
    assert named.post(path, json={"option": "accept"}).status_code == 200


def test_being_named_does_not_replace_holding_the_role(api: Api) -> None:
    named = api.client("hr_ops", email="wrong-role@test.example")
    task = api.task(
        assignee_role="manager", assignee_user_id=api.users["wrong-role@test.example"].id
    )
    assert (
        named.post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}).status_code == 403
    )


def test_a_claimed_task_is_closed_to_others_in_the_role(api: Api) -> None:
    task = api.task()
    first = api.client("hr_ops", email="first@test.example")
    second = api.client("hr_ops", email="second@test.example")
    assert first.post(f"/api/v1/tasks/{task.id}/claim").status_code == 200
    assert (
        second.post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}).status_code == 403
    )
    assert (
        first.post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}).status_code == 200
    )


def test_claim_then_decide(api: Api) -> None:
    task = api.task()
    client = api.client("hr_ops")
    claimed = client.post(f"/api/v1/tasks/{task.id}/claim")
    assert claimed.status_code == 200
    assert claimed.json()["status"] == "claimed"
    assert client.post(f"/api/v1/tasks/{task.id}/claim").status_code == 409
    assert (
        client.post(f"/api/v1/tasks/{task.id}/decide", json={"option": "accept"}).status_code == 200
    )


def test_task_not_found(api: Api) -> None:
    client = api.client("hr_ops")
    assert client.post(f"/api/v1/tasks/{uuid.uuid4()}/claim").status_code == 404
    assert (
        client.post(f"/api/v1/tasks/{uuid.uuid4()}/decide", json={"option": "x"}).status_code == 404
    )


def _lowered(yaml_text: str, version: int = 2, limit: int = 100000) -> str:
    return re.sub(r"(?m)^version: \d+", f"version: {version}", yaml_text).replace(
        "150000", str(limit)
    )


def _edited_tests(limit: int) -> str:
    return f"""pack: approvals
cases:
  - name: above the new limit needs approval
    request: {{ action: assets.laptop.allocate, actor: {{type: system, id: engine, roles: [system]}}, attributes: {{ amount_inr: {limit + 1} }} }}
    expect: {{ effect: REQUIRE_HUMAN, approver_role: approver_it_spend }}
"""


def test_policy_lifecycle_draft_test_activate_rollback(api: Api) -> None:
    client = api.client("hr_admin")
    active = client.get("/api/v1/policies/approvals").json()
    assert active["version"] == 1
    assert active["draft"] is None
    draft = _lowered(active["content"])
    tests = _edited_tests(100000)

    assert (
        client.put(
            "/api/v1/policies/approvals/draft", json={"content": draft, "tests": tests}
        ).status_code
        == 204
    )
    saved = client.get("/api/v1/policies/approvals").json()
    assert saved["draft"] == draft
    assert saved["version"] == 1

    results = client.post("/api/v1/policies/approvals/test").json()
    assert results
    assert all(r["passed"] for r in results), results

    activated = client.post("/api/v1/policies/approvals/activate")
    assert activated.status_code == 200, activated.text
    assert activated.json()["version"] == 2
    assert activated.json()["activated_by"] == "hr_admin@test.example"
    after = client.get("/api/v1/policies/approvals").json()
    assert after["draft"] is None
    assert "100000" in after["content"]
    assert [h["version"] for h in after["history"]] == [2, 1]

    rolled = client.post("/api/v1/policies/approvals/rollback", json={"version": 1})
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["version"] == 1
    assert "150000" in client.get("/api/v1/policies/approvals").json()["content"]


def test_a_failing_draft_cannot_be_activated(api: Api) -> None:
    client = api.client("hr_admin")
    content = client.get("/api/v1/policies/approvals").json()["content"]
    broken = _lowered(content, limit=500000)  # breaks "expensive laptop needs approval"
    client.put("/api/v1/policies/approvals/draft", json={"content": broken})
    results = client.post("/api/v1/policies/approvals/test").json()
    assert not all(r["passed"] for r in results)
    response = client.post("/api/v1/policies/approvals/activate")
    assert response.status_code == 422
    assert client.get("/api/v1/policies/approvals").json()["version"] == 1


@pytest.mark.parametrize(
    "content",
    ["not: [valid", "pack: approvals", "pack: nonsense\nversion: 2\n"],
)
def test_invalid_drafts_are_reported_not_crashes(api: Api, content: str) -> None:
    client = api.client("hr_admin")
    client.put("/api/v1/policies/approvals/draft", json={"content": content})
    results = client.post("/api/v1/policies/approvals/test")
    assert results.status_code == 200
    assert not all(r["passed"] for r in results.json())
    assert client.post("/api/v1/policies/approvals/activate").status_code == 422


def test_a_draft_of_another_pack_cannot_be_activated_here(api: Api) -> None:
    client = api.client("hr_admin")
    other = client.get("/api/v1/policies/reserved").json()["content"]
    client.put(
        "/api/v1/policies/approvals/draft",
        json={"content": other.replace("version: 1", "version: 2")},
    )
    assert client.post("/api/v1/policies/approvals/activate").status_code == 422


def test_reserved_pack_cannot_remove_the_floor_through_the_api(api: Api) -> None:
    client = api.client("hr_admin")
    content = client.get("/api/v1/policies/reserved").json()["content"]
    weakened = content.replace("version: 1", "version: 2").replace(
        "action: employment.probation.confirm", "action: employment.terminate"
    )
    client.put("/api/v1/policies/reserved/draft", json={"content": weakened})
    assert client.post("/api/v1/policies/reserved/activate").status_code == 422


def test_policy_routes_need_a_draft_and_a_real_pack(api: Api) -> None:
    client = api.client("hr_admin")
    assert client.post("/api/v1/policies/approvals/test").status_code == 409
    assert client.post("/api/v1/policies/approvals/activate").status_code == 409
    assert client.get("/api/v1/policies/nope").status_code == 404
    assert (
        client.post("/api/v1/policies/approvals/rollback", json={"version": 77}).status_code == 404
    )


def test_list_policies(api: Api) -> None:
    packs = {p["pack"] for p in api.client("hr_ops").get("/api/v1/policies").json()}
    assert {"approvals", "reserved", "rbac", "labour_rules", "retention"} == packs


def test_scoped_kill_switch_set_and_release(api: Api) -> None:
    client = api.client("hr_admin")
    on = client.post(
        "/api/v1/kill-switches",
        json={"scope": "workflow", "target": "onboarding", "active": True, "note": "drill"},
    )
    assert on.status_code == 200, on.text
    assert on.json()["active"] is True
    assert on.json()["set_by"] == "hr_admin@test.example"
    rows = client.get("/api/v1/kill-switches").json()
    assert any(r["target"] == "onboarding" and r["active"] for r in rows)
    off = client.post(
        "/api/v1/kill-switches", json={"scope": "workflow", "target": "onboarding", "active": False}
    )
    assert off.json()["active"] is False


def test_global_kill_switch_can_be_released_through_the_api(api: Api) -> None:
    client = api.client("hr_admin")
    assert (
        client.post("/api/v1/kill-switches", json={"scope": "global", "active": True}).status_code
        == 200
    )
    services: ApiServices = api.app.state.services
    services.policy.kill_switches(api.session, fresh=True)
    released = client.post("/api/v1/kill-switches", json={"scope": "global", "active": False})
    assert released.status_code == 200
    assert client.get("/api/v1/audit").status_code == 200


def test_bad_kill_switch_scope_is_422(api: Api) -> None:
    client = api.client("hr_admin")
    assert (
        client.post("/api/v1/kill-switches", json={"scope": "planet", "active": True}).status_code
        == 422
    )


def test_connector_health_reports_every_connector(api: Api) -> None:
    rows = api.client("hr_ops").get("/api/v1/connectors/health").json()
    assert {r["connector"] for r in rows} >= {"assets", "access", "payroll", "notify"}
    assert all(r["healthy"] for r in rows)


def test_fault_injection_shows_in_health_and_clears(api: Api) -> None:
    client = api.client("hr_admin")
    added = client.post("/api/v1/controls/faults", json={"system": "assets", "fault": "503"})
    assert added.status_code == 201, added.text
    assert [f["system"] for f in client.get("/api/v1/controls/faults").json()] == ["assets"]
    health = {r["connector"]: r for r in client.get("/api/v1/connectors/health").json()}
    assert health["assets"]["healthy"] is False
    assert health["access"]["healthy"] is True
    assert client.delete("/api/v1/controls/faults").status_code == 204
    assert client.get("/api/v1/controls/faults").json() == []


def test_unknown_fault_is_422(api: Api) -> None:
    client = api.client("hr_admin")
    assert (
        client.post(
            "/api/v1/controls/faults", json={"system": "assets", "fault": "gremlin"}
        ).status_code
        == 422
    )


def test_fault_controls_vanish_outside_demo_mode(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    client = api.client("hr_admin")
    monkeypatch.setattr("relay.api.deps._DEV_JWT_SECRET", "not-the-configured-secret")
    config = get_config()
    services: ApiServices = api.app.state.services
    services.config = config.model_copy(
        update={"app": config.app.model_copy(update={"demo_mode": False})}
    )
    assert client.get("/api/v1/controls/faults").status_code == 404


def test_mock_service_unreachable_is_a_502(api: Api) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    services: ApiServices = api.app.state.services
    services.faults = HttpFaultControl("http://mocks.test", httpx.MockTransport(refuse))
    client = api.client("hr_admin")
    assert client.get("/api/v1/controls/faults").status_code == 502


def test_fault_client_classifies_transport_errors() -> None:
    import asyncio

    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    control = HttpFaultControl("http://mocks.test", httpx.MockTransport(refuse))
    with pytest.raises(TransientError):
        asyncio.run(control.clear())


def test_audit_search_detail_and_verify(api: Api) -> None:
    payload = m.ModelPayload(
        direction=m.PayloadDirection.OUT, masked_text="PAN <PAN_1>", provider="p", region="local"
    )
    api.session.add(payload)
    api.session.flush()
    client = api.client("auditor")
    from relay.core.audit.writer import AuditEntry, append_event

    append_event(
        api.session,
        AuditEntry(actor_type=m.ActorType.SYSTEM, actor_id="engine", action="earlier.event"),
    )
    event = append_event(
        api.session,
        AuditEntry(
            actor_type=m.ActorType.AGENT,
            actor_id="agent:document_extractor",
            action="documents.extract",
            masked_payload_ref=payload.id,
            model_id="mock/deterministic",
        ),
    )
    found = client.get("/api/v1/audit", params={"actor": "agent:document_extractor"}).json()
    assert [e["id"] for e in found] == [str(event.id)]
    assert client.get("/api/v1/audit", params={"action": "documents.ext"}).json()[0]["id"] == str(
        event.id
    )
    assert client.get("/api/v1/audit", params={"effect": "NOPE"}).json() == []
    assert (
        client.get("/api/v1/audit", params={"before_seq": event.seq}).json()[0]["seq"] < event.seq
    )
    detail = client.get(f"/api/v1/audit/{event.id}").json()
    assert detail["payload"]["masked_text"] == "PAN <PAN_1>"
    assert detail["model_id"] == "mock/deterministic"
    assert client.get("/api/v1/audit/verify").json()["ok"] is True
    assert client.get("/api/v1/audit", params={"limit": 0}).status_code == 422


def test_metrics_endpoint_uses_the_canonical_queries(api: Api) -> None:
    window = {"from": "2031-01-01T00:00:00Z", "to": "2031-02-01T00:00:00Z"}
    start = datetime(2031, 1, 2, tzinfo=UTC)
    api.run(
        status=m.RunStatus.COMPLETED, started_at=start, completed_at=start + timedelta(seconds=60)
    )
    client = api.client("hr_ops")
    body = client.get("/api/v1/metrics/summary", params=window).json()
    assert body["completed_runs"] == 1
    assert body["cycle_time_p50_seconds"] == 60
    assert set(body["by_workflow"]) == {"onboarding"}
    assert (
        client.get("/api/v1/metrics/summary", params={**window, "workflow": "exit"}).json()[
            "completed_runs"
        ]
        == 0
    )


def test_definitions_list(api: Api) -> None:
    api.session.add(m.WorkflowDefinition(name="zz_test", version=1, content_hash="h", yaml="x"))
    api.session.flush()
    rows = api.client("developer").get("/api/v1/definitions").json()
    assert any(r["name"] == "zz_test" for r in rows)


def test_event_starts_runs_through_the_engine(api: Api) -> None:
    client = api.client("hr_ops")
    response = client.post(
        "/api/v1/events",
        json={
            "event": "candidate.offer_accepted",
            "subject_id": "c1",
            "payload": {"candidate_id": "c1"},
        },
    )
    assert response.status_code == 202
    assert api.engine is not None
    assert api.engine.started == [("candidate.offer_accepted", {"candidate_id": "c1"})]


def test_cancel_goes_through_the_engine(api: Api) -> None:
    run = api.run()
    assert api.client("hr_admin").post(f"/api/v1/runs/{run.id}/cancel").status_code == 204
    assert api.engine is not None
    assert api.engine.cancelled == [run.id]


def test_engine_routes_say_when_it_is_not_running(db_session: Session) -> None:
    api = Api(db_session, None)
    client = api.client("hr_admin")
    assert client.post("/api/v1/events", json={"event": "x", "subject_id": "c1"}).status_code == 503
    run = api.run()
    assert client.post(f"/api/v1/runs/{run.id}/cancel").status_code == 503


def test_data_request_is_about_the_signed_in_person(api: Api) -> None:
    person = m.Person(full_name="Requester")
    api.session.add(person)
    api.session.flush()
    run = api.run()
    assert api.engine is not None
    api.engine.handle_event = lambda *_a, **_k: [run.id]  # type: ignore[method-assign]
    client = api.client("employee", email="me@test.example", person_id=person.id)
    response = client.post("/api/v1/data-requests", json={"type": "erasure"})
    assert response.status_code == 201
    assert response.json()["type"] == "erasure"
    assert response.json()["run_id"] == str(run.id)  # the route started the workflow
    row = api.session.get(m.DataRequest, UUID(response.json()["id"]))
    assert row is not None
    assert row.person_id == person.id


def test_data_request_without_a_person_is_a_conflict(api: Api) -> None:
    assert (
        api.client("employee").post("/api/v1/data-requests", json={"type": "access"}).status_code
        == 409
    )
    assert (
        api.client("employee").post("/api/v1/data-requests", json={"type": "nope"}).status_code
        == 422
    )


def test_employee_record_shows_no_sensitive_values(api: Api) -> None:
    person = m.Person(
        full_name="Test Person",
        pan="ABCPK1234L",
        phone="9876543210",
        dob="1995-03-14",
        bank_account="50100123456789",
    )
    api.session.add(person)
    api.session.flush()
    employee = m.Employee(
        person_id=person.id,
        employee_code="TBO-T1",
        role_title="Engineer",
        ctc_approved={"annual_inr": 123456},
    )
    api.session.add(employee)
    api.session.flush()
    api.session.add(
        m.Document(
            person_id=person.id,
            category=m.DocumentCategory.PAN,
            storage_ref="vault/secret-path",
            extracted={"pan": "ABCPK1234L"},
        )
    )
    api.session.add(
        m.Consent(
            person_id=person.id, purpose_id="payroll_setup", notice_version="v1", channel="web"
        )
    )
    api.session.add(m.DataRequest(person_id=person.id, type=m.DataRequestType.ACCESS))
    api.session.add(m.LifecycleEvent(employee_id=employee.id, event_type="pre_hire_created"))
    api.session.flush()
    client = api.client("hr_ops")
    assert "Test Person" in {e["full_name"] for e in client.get("/api/v1/employees").json()}
    response = client.get(f"/api/v1/employees/{employee.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["documents"][0]["category"] == "pan"
    assert body["consents"][0]["purpose_id"] == "payroll_setup"
    assert body["lifecycle"][0]["event_type"] == "pre_hire_created"
    assert body["data_requests"][0]["type"] == "access"
    for secret in (
        "ABCPK1234L",
        "9876543210",
        "1995-03-14",
        "50100123456789",
        "123456",
        "secret-path",
    ):
        assert secret not in response.text


def test_employee_not_found(api: Api) -> None:
    assert api.client("hr_ops").get(f"/api/v1/employees/{uuid.uuid4()}").status_code == 404


def test_health_is_public(api: Api) -> None:
    assert api.anonymous.get("/api/v1/health").json() == {"status": "ok"}


def test_cors_allows_only_the_console(api: Api) -> None:
    allowed = api.anonymous.options(
        "/api/v1/runs",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    blocked = api.anonymous.options(
        "/api/v1/runs",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in blocked.headers


# ---------------------------------------------------------------- decision output (#69)


def _accept(api: Api, output: dict[str, object]) -> int:
    task = api.task(
        kind=m.HumanTaskKind.RESERVED_DECISION,
        action="employment.resignation.accept",
        assignee_role="manager",
    )
    response = api.client("manager").post(
        f"/api/v1/tasks/{task.id}/decide", json={"option": "accept", "output": output}
    )
    return response.status_code


def test_a_manager_can_agree_a_release_date_or_a_buyout(api: Api) -> None:
    assert _accept(api, {"release_date": "2026-12-18"}) == 200
    assert _accept(api, {"buyout_agreed": True}) == 200


def test_decision_output_is_refused_outside_what_the_action_takes(api: Api) -> None:
    assert _accept(api, {"decided_by": "someone-else"}) == 422
    assert _accept(api, {"release_date": "not a date"}) == 422
    assert _accept(api, {"release_date": "2026-12-18", "buyout_agreed": True}) == 422


def test_a_reserved_decision_with_no_declared_fields_takes_no_output(api: Api) -> None:
    task = api.task(
        kind=m.HumanTaskKind.RESERVED_DECISION,
        action="payroll.fnf.signoff",
        assignee_role="approver_finance",
    )
    response = api.client("approver_finance").post(
        f"/api/v1/tasks/{task.id}/decide",
        json={"option": "accept", "output": {"amount_inr": 1}},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------- linked runs (#66)


def test_run_detail_links_the_parent_and_its_children(api: Api) -> None:
    parent = api.run(status=m.RunStatus.COMPLETED, context={"subject_label": "Asha Rao"})
    child = api.run(
        definition_name="onboarding_probation",
        parent_run_id=parent.id,
        status=m.RunStatus.WAITING,
        context={"subject_label": "Asha Rao"},
    )
    client = api.client("hr_ops")
    detail = client.get(f"/api/v1/runs/{parent.id}").json()
    assert detail["parent"] is None
    assert [c["id"] for c in detail["children"]] == [str(child.id)]
    assert detail["children"][0]["definition_name"] == "onboarding_probation"
    assert detail["children"][0]["subject_label"] == "Asha Rao"
    back = client.get(f"/api/v1/runs/{child.id}").json()
    assert back["parent"]["id"] == str(parent.id) and back["parent_run_id"] == str(parent.id)
    assert back["children"] == []


def test_runs_can_be_filtered_by_workflow(api: Api) -> None:
    api.run(definition_name="exit", subject_id="TBO-X1")
    api.run(definition_name="onboarding")
    rows = api.client("hr_ops").get("/api/v1/runs", params={"workflow": "exit"}).json()
    assert rows and {r["definition_name"] for r in rows} == {"exit"}


def test_data_requests_list_is_everyone_for_hr_and_only_your_own_otherwise(api: Api) -> None:
    mine, theirs = m.Person(full_name="Asha Rao"), m.Person(full_name="Vikram Shah")
    api.session.add_all([mine, theirs])
    api.session.flush()
    api.session.add_all(
        [
            m.DataRequest(person_id=mine.id, type=m.DataRequestType.ACCESS),
            m.DataRequest(person_id=theirs.id, type=m.DataRequestType.ERASURE),
        ]
    )
    api.session.flush()
    hr = api.client("hr_ops").get("/api/v1/data-requests").json()
    assert {r["person_name"] for r in hr} >= {"Asha Rao", "Vikram Shah"}
    me = api.client("employee", email="asha@test.example", person_id=mine.id)
    own = me.get("/api/v1/data-requests").json()
    assert [(r["person_name"], r["type"], r["run_id"]) for r in own] == [
        ("Asha Rao", "access", None)
    ]
    assert api.client("employee").get("/api/v1/data-requests").json() == []  # no person record


# ---------------------------------------------------------------- employee lifecycle (#68)


def test_an_employee_record_shows_notice_last_day_and_their_runs(api: Api) -> None:
    person = m.Person(full_name="Leaver Person", email="leaver@test.example")
    api.session.add(person)
    api.session.flush()
    code = f"TBO-{uuid.uuid4().hex[:6]}"
    employee = m.Employee(
        person_id=person.id,
        employee_code=code,
        state=m.EmployeeState.NOTICE,
        last_working_day=date(2026, 12, 18),
    )
    api.session.add(employee)
    api.session.flush()
    onboarding = api.run(context={"ctx": {"employee": {"employee_id": str(employee.id)}}})
    leaving = api.run(definition_name="exit", subject_id=code)
    api.run(definition_name="exit", subject_id="someone-else")
    client = api.client("hr_ops")
    row = next(e for e in client.get("/api/v1/employees").json() if e["employee_code"] == code)
    assert row["state"] == "notice" and row["last_working_day"] == "2026-12-18"
    body = client.get(f"/api/v1/employees/{employee.id}").json()
    assert [r["id"] for r in body["runs"]] == [str(onboarding.id), str(leaving.id)]
    assert [r["definition_name"] for r in body["runs"]] == ["onboarding", "exit"]
