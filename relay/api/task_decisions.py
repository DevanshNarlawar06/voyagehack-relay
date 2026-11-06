"""Recording a human decision, the point where reserved decisions are enforced (PRD 6.3, 16.2)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.api.deps import Authorizer, EngineGateway
from relay.core.audit.writer import AuditEntry, append_event
from relay.db import models as m

_NEEDS_REASON = re.compile(r"reject|override|refer")
# These decide the action itself, so the policy engine must agree this person may take it.
_DECIDES_THE_ACTION = (m.HumanTaskKind.RESERVED_DECISION, m.HumanTaskKind.APPROVAL)
_OPEN = (m.HumanTaskStatus.OPEN, m.HumanTaskStatus.CLAIMED)


class TaskNotFound(Exception):
    """No such task."""


class TaskRefused(Exception):
    """This person may not decide this task."""


class TaskConflict(Exception):
    """The task was already decided or is otherwise closed."""


class InvalidDecision(ValueError):
    """The option is not offered, or a required reason is missing."""


def reason_required(task: m.HumanTask, option: str) -> bool:
    """A person rejecting, overriding or going against the suggestion must say why."""
    suggested = (task.context_packet or {}).get("suggested_option")
    return bool(_NEEDS_REASON.search(option)) or (suggested is not None and option != suggested)


def _load_for_update(session: Session, task_id: UUID) -> m.HumanTask:
    # The lock makes two people deciding at once safe: the second sees the first's decision.
    task = session.scalar(select(m.HumanTask).where(m.HumanTask.id == task_id).with_for_update())
    if task is None:
        raise TaskNotFound
    return task


def _may_act(task: m.HumanTask, user: m.User) -> bool:
    """The routed role is always needed; a task assigned to a named person is for them alone."""
    named = task.assignee_user_id is None or task.assignee_user_id == user.id
    return task.assignee_role in user.roles and named


def claim_task(session: Session, task_id: UUID, user: m.User) -> m.HumanTask:
    """Take a task from the role's queue."""
    task = _load_for_update(session, task_id)
    if not _may_act(task, user):
        raise TaskRefused(f"This task is for {task.assignee_role}.")
    if task.status is not m.HumanTaskStatus.OPEN:
        raise TaskConflict("This task is no longer open.")
    task.status = m.HumanTaskStatus.CLAIMED
    task.assignee_user_id = user.id
    session.flush()
    return task


def _run_scope(session: Session, task: m.HumanTask) -> dict[str, Any]:
    """Say where a task belongs, which the policy engine requires of any request from a run."""
    if task.run_id is None:
        return {}
    workflow = session.scalar(
        select(m.WorkflowRun.definition_name).where(m.WorkflowRun.id == task.run_id)
    )
    step_id = session.scalar(select(m.StepRun.step_id).where(m.StepRun.id == task.step_run_id))
    return {"run_id": task.run_id, "workflow": workflow, "step_id": step_id}


def decide_task(
    auth: Authorizer,
    engine: EngineGateway | None,
    task_id: UUID,
    option: str,
    reason: str,
) -> m.HumanTask:
    """Record a decision, audit it and let the engine resume.

    The role check comes first and has no bypass: `hr_admin` can do almost anything else, but a
    reserved decision belongs to the role it was routed to.
    """
    session, user = auth.session, auth.user
    task = _load_for_update(session, task_id)
    if not _may_act(task, user):
        raise TaskRefused(f"This task is for {task.assignee_role}.")
    if task.status not in _OPEN:
        raise TaskConflict("This task has already been closed.")
    if option not in task.options:
        raise InvalidDecision(f"{option} is not one of this task's options.")
    if reason_required(task, option) and not reason.strip():
        raise InvalidDecision("Please give a reason for this decision.")

    scope = _run_scope(session, task)
    if task.kind in _DECIDES_THE_ACTION:
        verdict = auth.check(task.action, **scope)
    else:
        verdict = auth.check("tasks.decide", **scope)
    if verdict.effect != "ALLOW":
        raise TaskRefused(verdict.reason)

    # auth.check committed, which released the row lock, so take it again before writing.
    session.expire(task)
    task = _load_for_update(session, task_id)
    if task.status not in _OPEN:
        raise TaskConflict("This task has already been closed.")
    task.status = m.HumanTaskStatus.DECIDED
    task.decision = {"option": option}
    task.reason = reason.strip() or None
    task.decided_by = user.id
    task.decided_at = datetime.now(UTC)
    session.flush()
    append_event(
        session,
        AuditEntry(
            actor_type=m.ActorType.HUMAN,
            actor_id=f"human:{user.email}",
            action=f"task.decide:{task.action}",
            run_id=task.run_id,
            step_run_id=task.step_run_id,
            effect=verdict.effect,
            policy_version=verdict.policy_version,
            reasoning=task.reason,
            data={"task_id": str(task.id), "kind": task.kind.value, "option": option},
        ),
    )
    if engine is not None:
        engine.task_decided(session, task)
    return task
