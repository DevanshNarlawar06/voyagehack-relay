"""The canonical dashboard metrics, computed from events. Definitions are PRD 3.3, word for word.

A linked child run (for example the `onboarding.probation` branch) is not counted: PRD 19 reports
the parent as complete at `readiness_gate`, so a six-month timer does not distort any figure.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, and_, case, exists, extract, func, select
from sqlalchemy.orm import Session, aliased

from relay.config import InfraCostsConfig, VolumesConfig
from relay.core.cost.writer import round_inr
from relay.db.models import (
    CostEvent,
    CostKind,
    HumanTask,
    HumanTaskKind,
    HumanTaskStatus,
    RunStatus,
    StepKind,
    StepRun,
    StepStatus,
    WorkflowRun,
)

# POSH, grievance and whistleblower cases are reserved too (PRD 2): humans are required by design.
_RESERVED_KINDS = (HumanTaskKind.RESERVED_DECISION, HumanTaskKind.ROUTE_ONLY)
# PRD 3.3 lists these as escalations. An `approval` is a human touch, but not an escalation.
_ESCALATION_KINDS = (
    HumanTaskKind.EXCEPTION,
    HumanTaskKind.CONFLICT,
    HumanTaskKind.LOW_CONFIDENCE,
    HumanTaskKind.OUTAGE,
)


class WorkflowMetrics(BaseModel):
    """Metrics over the completed runs of one workflow, or of all. A rate with no runs is None."""

    completed_runs: int
    steps_completed: int
    subject_actions: int
    step_automation_rate: float | None
    straight_through_rate: float | None
    human_escalation_rate: float | None
    cycle_time_p50_seconds: float | None
    cycle_time_p90_seconds: float | None
    subject_wait_p50_seconds: float | None
    variable_cost_inr: Decimal
    infra_alloc_inr: Decimal
    cost_per_workflow_inr: Decimal | None
    variable_cost_per_workflow_inr: Decimal | None


class MetricsSummary(WorkflowMetrics):
    """The response of GET /metrics/summary."""

    runs_by_status: dict[str, int]
    by_workflow: dict[str, WorkflowMetrics]
    monthly_projection_inr: Decimal | None
    projection_note: str


def _completed_runs(
    start: datetime | None, end: datetime | None, workflow: str | None
) -> Select[Any]:
    query = select(WorkflowRun.id).where(
        WorkflowRun.status == RunStatus.COMPLETED, WorkflowRun.parent_run_id.is_(None)
    )
    if start is not None:
        query = query.where(WorkflowRun.completed_at >= start)
    if end is not None:
        query = query.where(WorkflowRun.completed_at < end)
    if workflow is not None:
        query = query.where(WorkflowRun.definition_name == workflow)
    return query


def _has_non_reserved_touch() -> ColumnElement[bool]:
    """Is there a human touch on this run other than a reserved decision?"""
    task = aliased(HumanTask)
    reserved = aliased(HumanTask)
    step = aliased(StepRun)
    non_reserved_task = exists().where(
        task.run_id == WorkflowRun.id,
        task.kind.not_in(_RESERVED_KINDS),
        task.status != HumanTaskStatus.CANCELLED,
    )
    touched_outside_reserved = exists().where(
        step.run_id == WorkflowRun.id,
        step.human_touched.is_(True),
        ~exists().where(reserved.step_run_id == step.id, reserved.kind.in_(_RESERVED_KINDS)),
    )
    return non_reserved_task | touched_outside_reserved


def _has_escalation() -> ColumnElement[bool]:
    task = aliased(HumanTask)
    return exists().where(
        task.run_id == WorkflowRun.id,
        task.kind.in_(_ESCALATION_KINDS),
        task.status != HumanTaskStatus.CANCELLED,
    )


def _compute(
    session: Session, start: datetime | None, end: datetime | None, workflow: str | None
) -> WorkflowMetrics:
    completed = _completed_runs(start, end, workflow)
    in_scope = WorkflowRun.id.in_(completed)

    runs, straight, escalated = session.execute(
        select(
            func.count(),
            func.count().filter(~_has_non_reserved_touch()),
            func.count().filter(_has_escalation()),
        ).where(in_scope)
    ).one()

    steps, untouched, subject_actions = session.execute(
        select(
            func.count().filter(StepRun.status == StepStatus.SUCCEEDED),
            func.count().filter(
                and_(StepRun.status == StepStatus.SUCCEEDED, StepRun.human_touched.is_(False))
            ),
            func.coalesce(func.sum(StepRun.subject_actions), 0),
        ).where(StepRun.run_id.in_(completed))
    ).one()

    # Time a wait step spent on the joiner is shown separately from the cycle time (PRD 3.3).
    subject_wait = (
        select(
            StepRun.run_id.label("run_id"),
            func.sum(extract("epoch", StepRun.finished_at - StepRun.started_at)).label("seconds"),
        )
        .where(
            StepRun.kind == StepKind.WAIT,
            StepRun.subject_actions > 0,
            StepRun.finished_at.is_not(None),
            StepRun.started_at.is_not(None),
        )
        .group_by(StepRun.run_id)
        .subquery()
    )
    wait = func.coalesce(subject_wait.c.seconds, 0)
    total = extract("epoch", WorkflowRun.completed_at - WorkflowRun.started_at)
    cycle = func.greatest(total - wait, 0)
    p50, p90, wait_p50 = session.execute(
        select(
            func.percentile_cont(0.5).within_group(cycle),
            func.percentile_cont(0.9).within_group(cycle),
            func.percentile_cont(0.5).within_group(wait),
        )
        .select_from(WorkflowRun)
        .outerjoin(subject_wait, subject_wait.c.run_id == WorkflowRun.id)
        .where(in_scope)
    ).one()

    is_infra = CostEvent.kind == CostKind.INFRA_ALLOC
    variable, infra = session.execute(
        select(
            func.coalesce(func.sum(case((is_infra, 0), else_=CostEvent.cost_inr)), 0),
            func.coalesce(func.sum(case((is_infra, CostEvent.cost_inr), else_=0)), 0),
        ).where(CostEvent.run_id.in_(completed))
    ).one()
    variable, infra = Decimal(variable), Decimal(infra)

    return WorkflowMetrics(
        completed_runs=runs,
        steps_completed=steps,
        subject_actions=subject_actions,
        step_automation_rate=untouched / steps if steps else None,
        straight_through_rate=straight / runs if runs else None,
        human_escalation_rate=escalated / runs if runs else None,
        cycle_time_p50_seconds=float(p50) if p50 is not None else None,
        cycle_time_p90_seconds=float(p90) if p90 is not None else None,
        subject_wait_p50_seconds=float(wait_p50) if wait_p50 is not None else None,
        variable_cost_inr=variable,
        infra_alloc_inr=infra,
        cost_per_workflow_inr=round_inr((variable + infra) / runs) if runs else None,
        variable_cost_per_workflow_inr=round_inr(variable / runs) if runs else None,
    )


def monthly_projection(
    by_workflow: dict[str, WorkflowMetrics], infra: InfraCostsConfig, volumes: VolumesConfig
) -> Decimal | None:
    """Return Σ(volume x measured variable cost) + monthly fixed cost (PRD 17).

    None until every measured workflow has a known volume: a partial sum would look like a real
    figure while leaving out part of the bill.
    """
    total = Decimal(str(infra.monthly_fixed_inr))
    for name, metrics in by_workflow.items():
        volume = volumes.monthly.get(name)
        if volume is None or metrics.variable_cost_per_workflow_inr is None:
            return None
        total += volume * metrics.variable_cost_per_workflow_inr
    return round_inr(total)


def compute_metrics(
    session: Session,
    infra: InfraCostsConfig,
    volumes: VolumesConfig,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    workflow: str | None = None,
) -> MetricsSummary:
    """Compute every canonical metric. `start` and `end` bound `completed_at`, end exclusive."""
    overall = _compute(session, start, end, workflow)

    names_query = select(WorkflowRun.definition_name).where(
        WorkflowRun.id.in_(_completed_runs(start, end, workflow))
    )
    names = sorted(set(session.scalars(names_query)))
    by_workflow = {name: _compute(session, start, end, name) for name in names}

    status_query = select(WorkflowRun.status, func.count()).where(
        WorkflowRun.parent_run_id.is_(None)
    )
    if start is not None:
        status_query = status_query.where(WorkflowRun.started_at >= start)
    if end is not None:
        status_query = status_query.where(WorkflowRun.started_at < end)
    if workflow is not None:
        status_query = status_query.where(WorkflowRun.definition_name == workflow)
    statuses = session.execute(status_query.group_by(WorkflowRun.status)).all()

    return MetricsSummary(
        **overall.model_dump(),
        runs_by_status={status.value: count for status, count in statuses},
        by_workflow=by_workflow,
        monthly_projection_inr=monthly_projection(by_workflow, infra, volumes),
        projection_note=(
            "Projection, not a measurement: measured variable cost per run x "
            "config/volumes.yaml + config/infra_costs.yaml."
        ),
    )
