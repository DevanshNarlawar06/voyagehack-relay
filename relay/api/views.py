"""Read models: turn runtime tables into what the console shows. No HTTP in here."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from relay.api.schemas import (
    ContextPacketOut,
    RunDetailOut,
    RunOut,
    StepDecisionOut,
    StepOut,
    TaskOut,
    TimelineEventOut,
)
from relay.db import models as m

_MAX_RUNS = 200
_MAX_TIMELINE = 500


@dataclass(frozen=True)
class GraphNode:
    """What a definition says about a step that `step_runs` does not store."""

    needs: list[str]
    action: str | None


def definition_graph(text: str | None) -> dict[str, GraphNode]:
    """Read each step's `needs` and `action` from a definition's YAML.

    A `parallel` block's id stands for all its steps, and its `needs` pass to each branch's first
    step. Anything unreadable gives an empty graph: the steps still show, without their edges.
    """
    try:
        document = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        return {}
    if not isinstance(document, dict):
        return {}
    nodes: dict[str, GraphNode] = {}
    groups: dict[str, list[str]] = {}

    def add(step: dict[str, Any], inherited: list[str]) -> None:
        own = [str(need) for need in step.get("needs") or []]
        nodes[str(step["id"])] = GraphNode(own or inherited, step.get("action"))

    for step in document.get("steps") or []:
        if not isinstance(step, dict):
            continue
        block = step.get("parallel") if isinstance(step.get("parallel"), dict) else None
        if block is None and step.get("kind") == "parallel":
            block = step
        if block is None:
            if "id" in step:
                add(step, [])
            continue
        inherited = [str(need) for need in block.get("needs") or []]
        members: list[str] = []
        for branch in block.get("branches") or []:
            for inner in branch if isinstance(branch, list) else [branch]:
                if isinstance(inner, dict) and "id" in inner:
                    add(inner, inherited)
                    members.append(str(inner["id"]))
        groups[str(block.get("id"))] = members

    # A step that needs a parallel block needs every step inside it.
    return {
        step_id: GraphNode(
            [member for need in node.needs for member in groups.get(need, [need])], node.action
        )
        for step_id, node in nodes.items()
    }


def subject_label(run: m.WorkflowRun) -> str:
    """Return the run's display label. The engine may set `context.subject_label`."""
    label = run.context.get("subject_label") if isinstance(run.context, dict) else None
    return str(label) if label else f"{run.subject_type} {run.subject_id[:8]}"


def _run_out(run: m.WorkflowRun, total: int, done: int, cost: Decimal) -> RunOut:
    return RunOut(
        id=run.id,
        definition_name=run.definition_name,
        definition_version=run.definition_version,
        subject_label=subject_label(run),
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        steps_total=total,
        steps_done=done,
        cost_inr=cost,
    )


def team_subject_ids(session: Session, user: m.User) -> set[str]:
    """Return the employee ids reporting to `user`, for a manager's view of their team."""
    if user.person_id is None:
        return set()
    manager = aliased(m.Employee)
    query = (
        select(m.Employee.id)
        .join(manager, m.Employee.manager_id == manager.id)
        .where(manager.person_id == user.person_id)
    )
    return {str(employee_id) for employee_id in session.scalars(query)}


def list_runs(
    session: Session, *, status: str | None = None, subject_ids: set[str] | None = None
) -> list[RunOut]:
    """List runs, newest first. `subject_ids` limits them to those employees."""
    steps = (
        select(
            m.StepRun.run_id,
            func.count().label("total"),
            func.count().filter(m.StepRun.status == m.StepStatus.SUCCEEDED).label("done"),
        )
        .group_by(m.StepRun.run_id)
        .subquery()
    )
    costs = (
        select(m.CostEvent.run_id, func.sum(m.CostEvent.cost_inr).label("cost"))
        .group_by(m.CostEvent.run_id)
        .subquery()
    )
    query = (
        select(
            m.WorkflowRun,
            func.coalesce(steps.c.total, 0),
            func.coalesce(steps.c.done, 0),
            func.coalesce(costs.c.cost, 0),
        )
        .outerjoin(steps, steps.c.run_id == m.WorkflowRun.id)
        .outerjoin(costs, costs.c.run_id == m.WorkflowRun.id)
        .order_by(m.WorkflowRun.started_at.desc())
        .limit(_MAX_RUNS)
    )
    if status is not None:
        query = query.where(m.WorkflowRun.status == m.RunStatus(status))
    if subject_ids is not None:
        query = query.where(
            m.WorkflowRun.subject_type == "employee", m.WorkflowRun.subject_id.in_(subject_ids)
        )
    return [
        _run_out(run, total, done, Decimal(cost))
        for run, total, done, cost in session.execute(query)
    ]


def _error_text(error: dict[str, Any] | None) -> str | None:
    if not error:
        return None
    return str(error.get("reason") or error.get("message") or "Step failed")


def run_detail(session: Session, run: m.WorkflowRun) -> RunDetailOut:
    """Build the run page: graph, per-step explanation and cost, and the audit timeline."""
    definition = session.scalar(
        select(m.WorkflowDefinition).where(
            m.WorkflowDefinition.name == run.definition_name,
            m.WorkflowDefinition.version == run.definition_version,
        )
    )
    graph = definition_graph(definition.yaml if definition else None)
    rows = session.execute(
        select(m.StepRun, m.Decision)
        .outerjoin(m.Decision, m.StepRun.decision_id == m.Decision.id)
        .where(m.StepRun.run_id == run.id)
        .order_by(m.StepRun.created_at)
    ).all()
    cost_rows = session.execute(
        select(m.CostEvent.step_run_id, func.sum(m.CostEvent.cost_inr))
        .where(m.CostEvent.run_id == run.id)
        .group_by(m.CostEvent.step_run_id)
    ).all()
    step_costs = {step_run_id: Decimal(total) for step_run_id, total in cost_rows}
    events = list(
        session.scalars(
            select(m.AuditEvent)
            .where(m.AuditEvent.run_id == run.id)
            .order_by(m.AuditEvent.seq)
            .limit(_MAX_TIMELINE)
        )
    )
    step_names = {step.id: step.step_id for step, _ in rows}
    models: dict[UUID, m.AuditEvent] = {
        event.step_run_id: event for event in events if event.step_run_id and event.model_id
    }

    steps: list[StepOut] = []
    for step, decision in rows:
        node = graph.get(step.step_id, GraphNode([], None))
        model_event = models.get(step.id)
        steps.append(
            StepOut(
                step_id=step.step_id,
                kind=step.kind.value,
                status=step.status.value,
                needs=node.needs,
                attempt=step.attempt,
                human_touched=step.human_touched,
                started_at=step.started_at,
                finished_at=step.finished_at,
                cost_inr=step_costs.get(step.id, Decimal(0)),
                action=node.action,
                decision=StepDecisionOut(
                    effect=decision.effect.value,
                    rule_ids=decision.rule_ids,
                    policy_version=decision.policy_version,
                    reason=decision.reason,
                )
                if decision
                else None,
                model_id=model_event.model_id if model_event else None,
                prompt_version=model_event.prompt_version if model_event else None,
                error=_error_text(step.error),
            )
        )
    summary = _run_out(
        run,
        len(rows),
        sum(1 for step, _ in rows if step.status is m.StepStatus.SUCCEEDED),
        sum(step_costs.values(), Decimal(0)),
    )
    return RunDetailOut(
        **summary.model_dump(),
        steps=steps,
        timeline=[
            TimelineEventOut(
                seq=event.seq,
                ts=event.ts,
                actor_type=event.actor_type.value,
                actor_id=event.actor_id,
                action=event.action,
                effect=event.effect,
                step_id=step_names.get(event.step_run_id) if event.step_run_id else None,
                reasoning=event.reasoning,
            )
            for event in events
        ],
    )


def task_out(task: m.HumanTask, step_id: str | None) -> TaskOut:
    """Shape a task for the inbox. The packet may be partial: the engine fills what it knows."""
    packet = task.context_packet or {}
    facts = packet.get("facts")
    decision = task.decision or {}
    return TaskOut(
        id=task.id,
        run_id=task.run_id,
        step_id=step_id,
        kind=task.kind.value,
        action=task.action,
        title=str(packet.get("title") or task.action),
        assignee_role=task.assignee_role,
        status=task.status.value,
        options=list(task.options or []),
        sla_due_at=task.sla_due_at,
        context_packet=ContextPacketOut(
            summary=str(packet.get("summary") or "No summary was recorded for this task."),
            likely_cause=packet.get("likely_cause"),
            suggested_option=packet.get("suggested_option"),
            facts={str(k): str(v) for k, v in facts.items()} if isinstance(facts, dict) else {},
            rule=packet.get("rule"),
        ),
        decision=decision.get("option"),
        reason=task.reason,
    )


def list_tasks(session: Session, user: m.User) -> list[TaskOut]:
    """Return open tasks for the user's roles or assigned to them, most urgent first."""
    query = (
        select(m.HumanTask, m.StepRun.step_id)
        .outerjoin(m.StepRun, m.HumanTask.step_run_id == m.StepRun.id)
        .where(
            m.HumanTask.status.in_((m.HumanTaskStatus.OPEN, m.HumanTaskStatus.CLAIMED)),
            m.HumanTask.assignee_role.in_(user.roles) | (m.HumanTask.assignee_user_id == user.id),
        )
        .order_by(case((m.HumanTask.sla_due_at.is_(None), 1), else_=0), m.HumanTask.sla_due_at)
    )
    return [task_out(task, step_id) for task, step_id in session.execute(query)]
