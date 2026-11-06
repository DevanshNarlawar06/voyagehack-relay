"""Cost writer tests and metric SQL against fixtures (WP2). Expected values are worked by hand."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.config import InfraCostsConfig, Price, VolumesConfig
from relay.core.cost.metrics import MetricsSummary, compute_metrics, monthly_projection
from relay.core.cost.writer import (
    allocate_infra,
    cpu_price_per_second,
    llm_cost_inr,
    projected_monthly_runs,
    record_cpu_cost,
    record_llm_cost,
)
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

T0 = datetime(2027, 1, 4, 9, 0, tzinfo=UTC)
INFRA = InfraCostsConfig(currency="INR", monthly={"vm": 2592.0, "postgres": 408.0}, vm_vcpus=2)
VOLUMES = VolumesConfig(headcount=5000, monthly={"onboarding": 200, "exit": 100})
PRICE = Price(in_per_1k=0.1, out_per_1k=0.3)


def test_llm_cost_uses_per_1k_prices() -> None:
    assert llm_cost_inr(PRICE, 1000, 2000) == Decimal("0.7")
    assert llm_cost_inr(PRICE, 0, 0) == 0
    assert llm_cost_inr(PRICE, 1, 1) == Decimal("0.0004")


def test_cpu_price_is_vm_cost_over_vcpu_seconds() -> None:
    # 2592 / (2 vCPU x 2,592,000 s) = 0.0005
    assert cpu_price_per_second(INFRA) == Decimal("0.0005")


def test_projected_runs_ignores_unknown_volumes() -> None:
    assert projected_monthly_runs(VOLUMES) == 300
    partial = VolumesConfig(headcount=None, monthly={"onboarding": 50, "exit": None})
    assert projected_monthly_runs(partial) == 50
    assert projected_monthly_runs(VolumesConfig(headcount=None, monthly={"a": None})) is None


def test_monthly_fixed_includes_every_line() -> None:
    assert INFRA.monthly_fixed_inr == 3000


db = pytest.mark.db


def make_run(
    session: Session,
    *,
    status: RunStatus = RunStatus.COMPLETED,
    workflow: str = "onboarding",
    seconds: int = 600,
    started: datetime = T0,
    parent: uuid.UUID | None = None,
) -> WorkflowRun:
    run = WorkflowRun(
        definition_name=workflow,
        definition_version=1,
        subject_type="employee",
        subject_id=str(uuid.uuid4()),
        status=status,
        started_by_actor="system:test",
        started_at=started,
        completed_at=started + timedelta(seconds=seconds)
        if status is RunStatus.COMPLETED
        else None,
        parent_run_id=parent,
    )
    session.add(run)
    session.flush()
    return run


def make_step(session: Session, run: WorkflowRun, step_id: str, **fields: Any) -> StepRun:
    values: dict[str, Any] = {"kind": StepKind.RULE, "status": StepStatus.SUCCEEDED, **fields}
    step = StepRun(run_id=run.id, step_id=step_id, **values)
    session.add(step)
    session.flush()
    return step


def make_task(
    session: Session,
    run: WorkflowRun,
    kind: HumanTaskKind,
    step: StepRun | None = None,
    status: HumanTaskStatus = HumanTaskStatus.DECIDED,
) -> None:
    session.add(
        HumanTask(
            run_id=run.id,
            step_run_id=step.id if step else None,
            kind=kind,
            action="test.action",
            assignee_role="hr_ops",
            status=status,
        )
    )
    session.flush()


def metrics(session: Session, **kwargs: Any) -> MetricsSummary:
    # Each test runs in a rolled-back transaction, but committed rows from other test modules would
    # still count, so scope every query to a window no other test uses.
    window = {"start": T0 - timedelta(days=1), "end": T0 + timedelta(days=30), **kwargs}
    return compute_metrics(session, INFRA, VOLUMES, **window)


@db
def test_no_runs_gives_none_rates_rather_than_zero(db_session: Session) -> None:
    result = metrics(db_session)
    assert result.completed_runs == 0
    assert result.step_automation_rate is None
    assert result.straight_through_rate is None
    assert result.human_escalation_rate is None
    assert result.cycle_time_p50_seconds is None
    assert result.cost_per_workflow_inr is None
    assert result.monthly_projection_inr == Decimal(3000)


@db
def test_step_automation_rate(db_session: Session) -> None:
    run = make_run(db_session)
    for i in range(3):
        make_step(db_session, run, f"auto{i}")
    make_step(db_session, run, "touched", human_touched=True)
    make_step(db_session, run, "skipped", status=StepStatus.SKIPPED)
    make_step(db_session, run, "failed", status=StepStatus.FAILED)
    result = metrics(db_session)
    assert result.steps_completed == 4
    assert result.step_automation_rate == 0.75


@db
def test_steps_of_incomplete_runs_do_not_count(db_session: Session) -> None:
    make_step(db_session, make_run(db_session), "a")
    make_step(db_session, make_run(db_session, status=RunStatus.RUNNING), "b", human_touched=True)
    result = metrics(db_session)
    assert result.steps_completed == 1
    assert result.step_automation_rate == 1


@db
def test_subject_actions_are_counted_and_are_not_human_touches(db_session: Session) -> None:
    run = make_run(db_session)
    make_step(db_session, run, "upload", kind=StepKind.WAIT, subject_actions=2)
    result = metrics(db_session)
    assert result.subject_actions == 2
    assert result.step_automation_rate == 1
    assert result.straight_through_rate == 1


@db
def test_a_reserved_decision_keeps_a_run_straight_through(db_session: Session) -> None:
    run = make_run(db_session)
    step = make_step(db_session, run, "offer", kind=StepKind.HUMAN, human_touched=True)
    make_task(db_session, run, HumanTaskKind.RESERVED_DECISION, step)
    result = metrics(db_session)
    assert result.straight_through_rate == 1
    assert result.human_escalation_rate == 0
    # It is still a human touch for the step automation rate.
    assert result.step_automation_rate == 0


@db
@pytest.mark.parametrize(
    ("kind", "straight", "escalated"),
    [
        (HumanTaskKind.CONFLICT, 0, 1),
        (HumanTaskKind.EXCEPTION, 0, 1),
        (HumanTaskKind.LOW_CONFIDENCE, 0, 1),
        (HumanTaskKind.OUTAGE, 0, 1),
        (HumanTaskKind.APPROVAL, 0, 0),
        (HumanTaskKind.RESERVED_DECISION, 1, 0),
        (HumanTaskKind.ROUTE_ONLY, 1, 0),
    ],
)
def test_each_task_kind_against_prd_definitions(
    db_session: Session, kind: HumanTaskKind, straight: int, escalated: int
) -> None:
    make_task(db_session, make_run(db_session), kind)
    result = metrics(db_session)
    assert result.straight_through_rate == straight
    assert result.human_escalation_rate == escalated


@db
def test_a_cancelled_task_is_neither_a_touch_nor_an_escalation(db_session: Session) -> None:
    make_task(
        db_session, make_run(db_session), HumanTaskKind.CONFLICT, status=HumanTaskStatus.CANCELLED
    )
    result = metrics(db_session)
    assert result.straight_through_rate == 1
    assert result.human_escalation_rate == 0


@db
def test_a_touched_step_with_no_task_is_not_straight_through(db_session: Session) -> None:
    make_step(db_session, make_run(db_session), "manual", human_touched=True)
    assert metrics(db_session).straight_through_rate == 0


@db
def test_rates_are_over_all_completed_runs(db_session: Session) -> None:
    make_task(db_session, make_run(db_session), HumanTaskKind.CONFLICT)
    make_run(db_session)
    make_run(db_session)
    make_run(db_session)
    make_run(db_session, status=RunStatus.FAILED)
    result = metrics(db_session)
    assert result.completed_runs == 4
    assert result.straight_through_rate == 0.75
    assert result.human_escalation_rate == 0.25


@db
def test_cycle_time_percentiles_exclude_subject_waiting(db_session: Session) -> None:
    for seconds in (100, 200, 300, 400):
        make_run(db_session, seconds=seconds)
    waited = make_run(db_session, seconds=10_500)
    make_step(
        db_session,
        waited,
        "await_documents",
        kind=StepKind.WAIT,
        subject_actions=1,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=10_000),
    )
    result = metrics(db_session)
    # Cycle times net of waiting: 100, 200, 300, 400, 500.
    assert result.cycle_time_p50_seconds == 300
    assert result.cycle_time_p90_seconds == pytest.approx(460)
    # Waits: 0, 0, 0, 0, 10000.
    assert result.subject_wait_p50_seconds == 0


@db
def test_a_timer_wait_is_not_excluded_from_cycle_time(db_session: Session) -> None:
    run = make_run(db_session, seconds=600)
    make_step(
        db_session,
        run,
        "cool_off",
        kind=StepKind.WAIT,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=500),
    )
    assert metrics(db_session).cycle_time_p50_seconds == 600


@db
def test_cost_per_workflow_adds_variable_and_infra(db_session: Session) -> None:
    first = make_run(db_session)
    second = make_run(db_session)
    pending = make_run(db_session, status=RunStatus.RUNNING)
    for run, tokens in ((first, 1000), (second, 3000), (pending, 9000)):
        record_llm_cost(
            db_session,
            run_id=run.id,
            step_run_id=None,
            provider="p",
            model_id="m",
            price=PRICE,
            tokens_in=tokens,
            tokens_out=0,
        )
    assert allocate_infra(db_session, first.id, INFRA, VOLUMES)
    assert allocate_infra(db_session, second.id, INFRA, VOLUMES)
    result = metrics(db_session)
    # Variable: 0.1 + 0.3. Infra: 3000 / 300 = 10 per run.
    assert result.variable_cost_inr == Decimal("0.4")
    assert result.infra_alloc_inr == Decimal(20)
    assert result.variable_cost_per_workflow_inr == Decimal("0.2")
    assert result.cost_per_workflow_inr == Decimal("10.2")


@db
def test_allocation_is_exactly_once_per_run(db_session: Session) -> None:
    run = make_run(db_session)
    assert allocate_infra(db_session, run.id, INFRA, VOLUMES) is True
    assert allocate_infra(db_session, run.id, INFRA, VOLUMES) is False
    rows = db_session.scalars(
        select(CostEvent).where(CostEvent.run_id == run.id, CostEvent.kind == CostKind.INFRA_ALLOC)
    ).all()
    assert len(rows) == 1
    assert rows[0].cost_inr == 10
    assert rows[0].unit_prices == {"monthly_fixed_inr": "3000.0", "projected_monthly_runs": 300}


@db
def test_allocation_with_unknown_volumes_is_zero_and_says_so(db_session: Session) -> None:
    run = make_run(db_session)
    unknown = VolumesConfig(headcount=None, monthly={"onboarding": None})
    allocate_infra(db_session, run.id, INFRA, unknown)
    row = db_session.scalars(select(CostEvent).where(CostEvent.run_id == run.id)).one()
    assert row.cost_inr == 0
    assert row.unit_prices["projected_monthly_runs"] is None


@db
def test_cpu_cost_event(db_session: Session) -> None:
    run = make_run(db_session)
    event = record_cpu_cost(
        db_session,
        run_id=run.id,
        step_run_id=None,
        kind=CostKind.OCR_CPU,
        cpu_seconds=100,
        infra=INFRA,
    )
    assert event.cost_inr == Decimal("0.05")
    assert metrics(db_session).variable_cost_inr == Decimal("0.05")


@db
def test_llm_cost_event_records_its_unit_prices(db_session: Session) -> None:
    event = record_llm_cost(
        db_session,
        run_id=None,
        step_run_id=None,
        provider="p",
        model_id="m",
        price=PRICE,
        tokens_in=1,
        tokens_out=1,
    )
    assert event.unit_prices == {"in_per_1k": "0.1", "out_per_1k": "0.3"}


@db
def test_child_runs_do_not_count(db_session: Session) -> None:
    parent = make_run(db_session, seconds=600)
    child = make_run(db_session, seconds=15_000_000, parent=parent.id)
    make_step(db_session, child, "probation", kind=StepKind.HUMAN, human_touched=True)
    make_task(db_session, child, HumanTaskKind.CONFLICT)
    result = metrics(db_session)
    assert result.completed_runs == 1
    assert result.cycle_time_p50_seconds == 600
    assert result.human_escalation_rate == 0
    assert result.steps_completed == 0
    assert result.runs_by_status == {"completed": 1}


@db
def test_filters_by_window_and_workflow(db_session: Session) -> None:
    make_run(db_session, seconds=100)
    make_run(db_session, seconds=200, workflow="exit")
    make_run(db_session, seconds=300, started=T0 + timedelta(days=5))
    assert metrics(db_session).completed_runs == 3
    assert metrics(db_session, workflow="exit").completed_runs == 1
    early = metrics(db_session, end=T0 + timedelta(days=1))
    assert early.completed_runs == 2
    assert early.runs_by_status == {"completed": 2}
    assert metrics(db_session, start=T0 + timedelta(days=1)).completed_runs == 1


@db
def test_by_workflow_breakdown_and_status_counts(db_session: Session) -> None:
    make_step(db_session, make_run(db_session), "a")
    make_step(db_session, make_run(db_session, workflow="exit"), "b", human_touched=True)
    make_run(db_session, status=RunStatus.RUNNING)
    make_run(db_session, status=RunStatus.PAUSED)
    result = metrics(db_session)
    assert set(result.by_workflow) == {"onboarding", "exit"}
    assert result.by_workflow["onboarding"].step_automation_rate == 1
    assert result.by_workflow["exit"].step_automation_rate == 0
    assert result.step_automation_rate == 0.5
    assert result.runs_by_status == {"completed": 2, "running": 1, "paused": 1}


@db
def test_monthly_projection_from_measured_cost(db_session: Session) -> None:
    onboarding = make_run(db_session)
    leaving = make_run(db_session, workflow="exit")
    for run, tokens in ((onboarding, 1000), (leaving, 2000)):
        record_llm_cost(
            db_session,
            run_id=run.id,
            step_run_id=None,
            provider="p",
            model_id="m",
            price=PRICE,
            tokens_in=tokens,
            tokens_out=0,
        )
    result = metrics(db_session)
    # 200 x 0.1 + 100 x 0.2 + 3000 fixed
    assert result.monthly_projection_inr == Decimal(3040)
    assert "Projection" in result.projection_note


@db
def test_projection_is_none_while_a_measured_workflow_has_no_volume(db_session: Session) -> None:
    make_run(db_session, workflow="brand_new")
    assert metrics(db_session).monthly_projection_inr is None


def test_projection_with_no_workflows_is_just_the_fixed_cost() -> None:
    assert monthly_projection({}, INFRA, VOLUMES) == 3000
