"""The `/api/v1` routes (PRD 16.1). Each one authorizes, delegates, and shapes the response."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated
from uuid import UUID

import yaml
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.api import schemas as s
from relay.api import task_decisions as td
from relay.api import views
from relay.api.deps import Auth, CurrentUser, Db, EngineGateway, Services, issue_token
from relay.api.ratelimit import login_limiter
from relay.connectors.base import FaultControl, HealthStatus
from relay.core.audit.verifier import VerifyResult, verify_chain
from relay.core.audit.writer import AuditEntry, append_event
from relay.core.cost.metrics import MetricsSummary, compute_metrics
from relay.core.errors import TransientError
from relay.core.policy import PolicyTestFailure
from relay.core.policy.packs import PACK_NAMES, parse_pack
from relay.db import models as m

router = APIRouter(prefix="/api/v1")


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} was not found.")


def _require_engine(engine: EngineGateway | None) -> EngineGateway:
    if engine is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The workflow engine is not running."
        )
    return engine


# ---------------------------------------------------------------- auth


@router.get("/auth/demo-users", tags=["auth"])
def demo_users(session: Db, services: Services) -> list[s.UserOut]:
    """List seeded users for the sign-in screen. Demo mode only (PRD 3.2: no production SSO)."""
    if not services.config.app.demo_mode:
        raise _not_found("This page")
    users = session.scalars(select(m.User).order_by(m.User.display_name))
    return [s.UserOut.model_validate(user) for user in users]


@router.post("/auth/demo-login", tags=["auth"])
def demo_login(body: s.LoginIn, request: Request, session: Db, services: Services) -> s.SessionOut:
    """Sign in as a seeded user. Demo mode only, with no password by design."""
    if not services.config.app.demo_mode:
        raise _not_found("This page")
    if not login_limiter.allow(request.client.host if request.client else "unknown"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again soon.")
    user = session.scalar(select(m.User).where(m.User.email == body.email))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown demo user.")
    return s.SessionOut(
        token=issue_token(user.email, services.config), user=s.UserOut.model_validate(user)
    )


@router.get("/auth/me", tags=["auth"])
def me(user: CurrentUser) -> s.UserOut:
    """Return the signed-in person."""
    return s.UserOut.model_validate(user)


# ---------------------------------------------------------------- events and runs


@router.post("/events", status_code=status.HTTP_202_ACCEPTED, tags=["runs"])
def post_event(body: s.EventIn, auth: Auth, session: Db, services: Services) -> s.EventOut:
    """Ingest a domain event, which starts the matching workflows."""
    auth.require("events.ingest")
    engine = _require_engine(services.engine)
    run_ids = engine.start_event(session, body.event, body.payload, f"human:{auth.user.email}")
    session.commit()
    return s.EventOut(run_ids=run_ids)


def _run_scope(auth: Auth) -> set[str] | None:
    """Return None for all runs, or a manager's team, or refuse (PRD 16.1: own team)."""
    if auth.check("runs.read").effect == "ALLOW":
        return None
    auth.require("runs.read.team")
    return views.team_subject_ids(auth.session, auth.user)


@router.get("/runs", tags=["runs"])
def list_runs(
    auth: Auth,
    session: Db,
    run_status: Annotated[m.RunStatus | None, Query(alias="status")] = None,
) -> list[s.RunOut]:
    """List runs, newest first. Managers see their own team."""
    scope = _run_scope(auth)
    return views.list_runs(
        session, status=run_status.value if run_status else None, subject_ids=scope
    )


def _get_run(session: Session, run_id: UUID) -> m.WorkflowRun:
    run = session.get(m.WorkflowRun, run_id)
    if run is None:
        raise _not_found("This run")
    return run


@router.get("/runs/{run_id}", tags=["runs"])
def get_run(run_id: UUID, auth: Auth, session: Db) -> s.RunDetailOut:
    """Return a run with its graph, decisions, costs and timeline."""
    scope = _run_scope(auth)
    run = _get_run(session, run_id)
    outside_team = scope is not None and (
        run.subject_type != "employee" or run.subject_id not in scope
    )
    if outside_team:
        raise _not_found("This run")
    return views.run_detail(session, run)


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT, tags=["runs"])
def cancel_run(run_id: UUID, auth: Auth, session: Db, services: Services) -> None:
    """Cancel a run."""
    auth.require("runs.cancel")
    engine = _require_engine(services.engine)
    _get_run(session, run_id)
    engine.cancel_run(session, run_id, f"human:{auth.user.email}")
    session.commit()


# ---------------------------------------------------------------- human tasks


@router.get("/tasks", tags=["tasks"])
def list_tasks(auth: Auth, session: Db) -> list[s.TaskOut]:
    """Return the open tasks for the signed-in person's roles."""
    auth.require("tasks.read")
    return views.list_tasks(session, auth.user)


def _task_error(exc: Exception) -> HTTPException:
    if isinstance(exc, td.TaskNotFound):
        return _not_found("This task")
    if isinstance(exc, td.TaskRefused):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, td.TaskConflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


_TASK_ERRORS = (td.TaskNotFound, td.TaskRefused, td.TaskConflict, td.InvalidDecision)


def _task_response(session: Session, task: m.HumanTask) -> s.TaskOut:
    step_id = session.scalar(select(m.StepRun.step_id).where(m.StepRun.id == task.step_run_id))
    return views.task_out(task, step_id)


@router.post("/tasks/{task_id}/claim", tags=["tasks"])
def claim_task(task_id: UUID, auth: Auth, session: Db) -> s.TaskOut:
    """Take a task from the role's queue."""
    auth.require("tasks.claim")
    try:
        task = td.claim_task(session, task_id, auth.user)
    except _TASK_ERRORS as exc:
        raise _task_error(exc) from None
    session.commit()
    return _task_response(session, task)


@router.post("/tasks/{task_id}/decide", tags=["tasks"])
def decide_task(
    task_id: UUID, body: s.DecideIn, auth: Auth, session: Db, services: Services
) -> s.TaskOut:
    """Record a decision. Reserved decisions need a human holding the routed role (PRD 6.3)."""
    try:
        task = td.decide_task(auth, services.engine, task_id, body.option, body.reason)
    except _TASK_ERRORS as exc:
        raise _task_error(exc) from None
    session.commit()
    return _task_response(session, task)


# ---------------------------------------------------------------- policies


def _check_pack(pack: str) -> str:
    if pack not in PACK_NAMES:
        raise _not_found("This policy pack")
    return pack


def _draft(session: Session, pack: str) -> m.PolicyDraft | None:
    return session.scalar(select(m.PolicyDraft).where(m.PolicyDraft.pack == pack))


def _latest_activation(session: Session, pack: str) -> m.PolicyVersion | None:
    return session.scalar(
        select(m.PolicyVersion)
        .where(m.PolicyVersion.pack == pack, m.PolicyVersion.activated_at.is_not(None))
        .order_by(m.PolicyVersion.activated_at.desc())
        .limit(1)
    )


def _summary(session: Session, services: Services, pack: str) -> s.PolicySummaryOut:
    active = getattr(services.policy.policies(session), pack)
    latest = _latest_activation(session, pack)
    return s.PolicySummaryOut(
        pack=pack,
        version=active.version,
        owner=active.owner,
        activated_by=latest.activated_by if latest else "bootstrap",
        activated_at=latest.activated_at if latest else None,
    )


@router.get("/policies", tags=["policies"])
def list_policies(auth: Auth, session: Db, services: Services) -> list[s.PolicySummaryOut]:
    """List policy packs and their active versions."""
    auth.require("policy.read")
    return [_summary(session, services, pack) for pack in PACK_NAMES]


@router.get("/policies/{pack}", tags=["policies"])
def get_policy(pack: str, auth: Auth, session: Db, services: Services) -> s.PolicyOut:
    """Return a pack's active source, its draft and its version history."""
    auth.require("policy.read")
    _check_pack(pack)
    summary = _summary(session, services, pack)
    latest = _latest_activation(session, pack)
    content = latest.content if latest else services.policy.bootstrap_source(pack)
    draft = _draft(session, pack)
    history = sorted(
        services.policy.history(session, pack), key=lambda row: row.version, reverse=True
    )
    return s.PolicyOut(
        **summary.model_dump(),
        content=content,
        draft=draft.content if draft else None,
        draft_tests=draft.tests if draft else None,
        history=[
            s.PolicyHistoryOut(
                version=row.version, activated_by=row.activated_by, activated_at=row.activated_at
            )
            for row in history
        ],
    )


@router.put("/policies/{pack}/draft", status_code=status.HTTP_204_NO_CONTENT, tags=["policies"])
def save_draft(pack: str, body: s.DraftIn, auth: Auth, session: Db) -> None:
    """Save an edited pack. Nothing changes until it passes its tests and is activated."""
    auth.require("policy.edit")
    _check_pack(pack)
    draft = _draft(session, pack)
    if draft is None:
        draft = m.PolicyDraft(
            pack=pack, content=body.content, tests=body.tests, updated_by=auth.user.email
        )
        session.add(draft)
    else:
        draft.content = body.content
        draft.tests = body.tests
        draft.updated_by = auth.user.email
    session.commit()


def _require_draft(session: Session, pack: str) -> m.PolicyDraft:
    draft = _draft(session, pack)
    if draft is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Save a draft of this pack first.")
    return draft


@router.post("/policies/{pack}/test", tags=["policies"])
def test_policy(pack: str, auth: Auth, session: Db, services: Services) -> list[s.TestResultOut]:
    """Run every policy test case against the draft."""
    auth.require("policy.edit")
    _check_pack(pack)
    draft = _require_draft(session, pack)
    try:
        results = services.policy.test(session, parse_pack(draft.content), draft.tests)
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        return [s.TestResultOut(name="the draft is valid", passed=False, detail=_first_line(exc))]
    return [
        s.TestResultOut(name=f"{r.pack}: {r.name}", passed=r.passed, detail=r.detail or None)
        for r in results
    ]


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__


@router.post("/policies/{pack}/activate", tags=["policies"])
def activate_policy(pack: str, auth: Auth, session: Db, services: Services) -> s.PolicySummaryOut:
    """Activate the draft. The tests run again here, so a failing draft can never go live."""
    auth.require("policy.activate")
    _check_pack(pack)
    draft = _require_draft(session, pack)
    try:
        if parse_pack(draft.content).pack != pack:
            raise ValueError(f"This draft is not the {pack} pack.")
        services.policy.activate(
            session, draft.content, actor=auth.user.email, draft_tests=draft.tests
        )
    except PolicyTestFailure as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Policy tests failed; it was not activated."
        ) from exc
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _first_line(exc)) from None
    session.delete(draft)
    session.commit()
    return _summary(session, services, pack)


@router.post("/policies/{pack}/rollback", tags=["policies"])
def rollback_policy(
    pack: str, body: s.RollbackIn, auth: Auth, session: Db, services: Services
) -> s.PolicySummaryOut:
    """Make an earlier version active again. Its tests run first."""
    auth.require("policy.activate")
    _check_pack(pack)
    try:
        services.policy.rollback(session, pack, body.version, actor=auth.user.email)
    except PolicyTestFailure as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That version fails the current tests."
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _first_line(exc)) from None
    session.commit()
    return _summary(session, services, pack)


# ---------------------------------------------------------------- kill switches and controls


@router.get("/kill-switches", tags=["controls"])
def list_kill_switches(auth: Auth, session: Db) -> list[s.KillSwitchOut]:
    """List kill switches, latest change first."""
    auth.require("kill_switch.read")
    rows = session.scalars(select(m.KillSwitch).order_by(m.KillSwitch.set_at.desc()))
    return [_kill_out(row) for row in rows]


def _kill_out(row: m.KillSwitch) -> s.KillSwitchOut:
    return s.KillSwitchOut(
        scope=row.scope.value,
        target=row.target,
        active=row.active,
        set_by=row.set_by,
        set_at=row.set_at,
        note=row.note,
    )


@router.post("/kill-switches", tags=["controls"])
def set_kill_switch(
    body: s.KillSwitchIn, auth: Auth, session: Db, services: Services
) -> s.KillSwitchOut:
    """Set or release a kill switch."""
    auth.require("kill_switch.set")
    row = services.policy.set_kill_switch(
        session, body.scope, body.target, active=body.active, actor=auth.user.email, note=body.note
    )
    session.commit()
    return _kill_out(row)


@router.get("/connectors/health", tags=["controls"])
async def connector_health(auth: Auth, services: Services) -> list[HealthStatus]:
    """Check every connector's system."""
    await asyncio.to_thread(auth.require, "connectors.read")
    return list(await asyncio.gather(*(c.health() for c in services.connectors.values())))


def _fault_control(services: Services) -> FaultControl:
    if not services.config.app.demo_mode or services.faults is None:
        raise _not_found("This page")
    return services.faults


@router.get("/controls/faults", tags=["controls"])
async def list_faults(auth: Auth, services: Services) -> list[dict[str, object]]:
    """List injected faults. Demo mode only."""
    await asyncio.to_thread(auth.require, "connectors.read")
    control = _fault_control(services)
    try:
        return await control.list()
    except TransientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.reason) from None


@router.post("/controls/faults", status_code=status.HTTP_201_CREATED, tags=["controls"])
async def add_fault(body: s.FaultIn, auth: Auth, services: Services) -> dict[str, object]:
    """Inject a fault into a mock system. Demo mode only."""
    await asyncio.to_thread(auth.require, "controls.fault")
    control = _fault_control(services)
    try:
        return await control.add(body.system, body.fault)
    except TransientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.reason) from None


@router.delete("/controls/faults", status_code=status.HTTP_204_NO_CONTENT, tags=["controls"])
async def clear_faults(auth: Auth, services: Services) -> None:
    """Remove every injected fault. Demo mode only."""
    await asyncio.to_thread(auth.require, "controls.fault")
    control = _fault_control(services)
    try:
        await control.clear()
    except TransientError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.reason) from None


# ---------------------------------------------------------------- audit


@router.get("/audit", tags=["audit"])
def list_audit(
    auth: Auth,
    session: Db,
    actor: str | None = None,
    action: str | None = None,
    run_id: UUID | None = None,
    effect: str | None = None,
    before_seq: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[s.AuditEventOut]:
    """Search the audit log, newest first. Page with `before_seq`."""
    auth.require("audit.read")
    query = select(m.AuditEvent).order_by(m.AuditEvent.seq.desc()).limit(limit)
    if actor:
        query = query.where(m.AuditEvent.actor_id == actor)
    if action:
        query = query.where(m.AuditEvent.action.contains(action, autoescape=True))
    if run_id:
        query = query.where(m.AuditEvent.run_id == run_id)
    if effect:
        query = query.where(m.AuditEvent.effect == effect)
    if before_seq is not None:
        query = query.where(m.AuditEvent.seq < before_seq)
    return [s.AuditEventOut.model_validate(event) for event in session.scalars(query)]


@router.get("/audit/verify", tags=["audit"])
def verify_audit(auth: Auth, session: Db) -> VerifyResult:
    """Recompute the hash chain."""
    auth.require("audit.read")
    return verify_chain(session)


@router.get("/audit/{event_id}", tags=["audit"])
def get_audit_event(event_id: UUID, auth: Auth, session: Db) -> s.AuditEventDetailOut:
    """Return one event, with the exact masked payload that left the system, if any."""
    auth.require("audit.read")
    event = session.get(m.AuditEvent, event_id)
    if event is None:
        raise _not_found("This event")
    payload = (
        session.get(m.ModelPayload, event.masked_payload_ref) if event.masked_payload_ref else None
    )
    return s.AuditEventDetailOut(
        **s.AuditEventOut.model_validate(event).model_dump(),
        payload=s.ModelPayloadOut.model_validate(payload) if payload else None,
    )


# ---------------------------------------------------------------- metrics and registry


@router.get("/metrics/summary", tags=["metrics"])
def metrics_summary(
    auth: Auth,
    session: Db,
    services: Services,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    workflow: str | None = None,
) -> MetricsSummary:
    """Return the canonical metrics (PRD 3.3)."""
    auth.require("metrics.read")
    config = services.config
    return compute_metrics(
        session, config.infra_costs, config.volumes, start=start, end=end, workflow=workflow
    )


@router.get("/definitions", tags=["registry"])
def list_definitions(auth: Auth, session: Db) -> list[s.DefinitionOut]:
    """List workflow definitions and versions."""
    auth.require("definitions.read")
    rows = session.scalars(
        select(m.WorkflowDefinition).order_by(
            m.WorkflowDefinition.name, m.WorkflowDefinition.version.desc()
        )
    )
    return [s.DefinitionOut.model_validate(row) for row in rows]


# ---------------------------------------------------------------- DPDP


@router.post("/data-requests", status_code=status.HTTP_201_CREATED, tags=["dpdp"])
def create_data_request(body: s.DataRequestIn, auth: Auth, session: Db) -> s.DataRequestOut:
    """Raise a DPDP request about the signed-in person's own data."""
    auth.require("data_request.create")
    if auth.user.person_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This account is not linked to a person record."
        )
    row = m.DataRequest(person_id=auth.user.person_id, type=m.DataRequestType(body.type))
    session.add(row)
    session.flush()
    append_event(
        session,
        AuditEntry(
            actor_type=m.ActorType.HUMAN,
            actor_id=f"human:{auth.user.email}",
            action=f"data_request.create:{body.type}",
            subject_type="person",
            subject_id=str(auth.user.person_id),
            data={"data_request_id": str(row.id)},
        ),
    )
    session.commit()
    return s.DataRequestOut(id=row.id, type=row.type.value, status=row.status, run_id=row.run_id)


@router.get("/health", tags=["controls"])
def health(session: Db) -> dict[str, str]:
    """Liveness and database check for Compose. Public, and reveals nothing."""
    session.execute(select(1))
    return {"status": "ok"}
