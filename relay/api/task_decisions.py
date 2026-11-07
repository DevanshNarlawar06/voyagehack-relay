"""Recording a human decision, the point where reserved decisions are enforced (PRD 6.3, 16.2)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.api.decision_outputs import OutputRefused, validate_output
from relay.api.deps import Authorizer, EngineGateway
from relay.core.audit.writer import AuditEntry, append_event
from relay.core.engine.engine import TaskDecisionRefused
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


class EngineUnavailable(Exception):
    """The task is on a run, and nothing is running that could resume it."""


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


def can_decide(task: m.HumanTask, user: m.User) -> bool:
    """Whether this user is the one the task is routed to (role, and named person if any).

    A first check for interfaces such as Slack; `decide_task` checks again, with the policy engine.
    """
    return _may_act(task, user)


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


def decide_task(
    auth: Authorizer,
    engine: EngineGateway | None,
    task_id: UUID,
    option: str,
    reason: str,
    output: Mapping[str, Any] | None = None,
) -> m.HumanTask:
    """Record a decision and let the run continue.

    These checks come first and have no bypass: `hr_admin` can do almost anything else, but a
    reserved decision belongs to the role and person it was routed to. For a task on a run, the
    engine then authorizes the decider and resumes the step in one transaction. A task with no run,
    such as an offer approval, has nothing to resume and is recorded here.
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
    try:
        output = validate_output(task, output)
    except OutputRefused as exc:
        raise InvalidDecision(str(exc)) from None
    if task.run_id is None:
        return _record_without_a_run(auth, task_id, option, reason)
    if engine is None:
        # Recording it here would look done while the step waits forever.
        raise EngineUnavailable
    session.commit()  # release the row lock: the engine locks this task in its own transaction
    try:
        engine.decide_task(
            task_id, user_id=user.id, option=option, reason=reason.strip() or None, output=output
        )
    except TaskDecisionRefused as exc:
        raise TaskRefused(str(exc)) from None
    session.expire_all()
    return _load_for_update(session, task_id)


def _record_without_a_run(auth: Authorizer, task_id: UUID, option: str, reason: str) -> m.HumanTask:
    session, user = auth.session, auth.user
    task = _load_for_update(session, task_id)
    verdict = auth.check(task.action if task.kind in _DECIDES_THE_ACTION else "tasks.decide")
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
            effect=verdict.effect,
            policy_version=verdict.policy_version,
            reasoning=task.reason,
            data={"task_id": str(task.id), "kind": task.kind.value, "option": option},
        ),
    )
    return task
