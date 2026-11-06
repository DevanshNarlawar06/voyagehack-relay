"""Regression tests for the engine bugs Session B reproduced in issue #44."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select, update

from relay.core.engine.engine import TaskDecisionRefused
from relay.core.ledger import ledger
from relay.db import models as m
from tests.simulation.harness import World, build_world
from tests.simulation.test_engine import _run, _start, _steps, _tasks, _to_completion

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def sa_engine(db_url: str) -> Iterator[SAEngine]:
    engine = create_engine(db_url, pool_size=10)
    yield engine
    engine.dispose()


@pytest.fixture()
def world(sa_engine: SAEngine) -> World:
    return build_world(sa_engine)


def _escalate(w: World) -> tuple[uuid.UUID, m.HumanTask]:
    run_id = _start(w)
    w.engine.run_until_idle()
    w.clock.advance(hours=73)
    w.engine.run_until_idle()
    (task,) = _tasks(w, run_id)
    assert task.action == "sla.escalation"
    return run_id, task


def _ledger_row(w: World, run_id: uuid.UUID, step: str, op: str) -> m.IdempotencyLedger:
    with w.sessions() as s:
        row = s.scalar(
            select(m.IdempotencyLedger).where(
                m.IdempotencyLedger.key == ledger.idempotency_key(run_id, step, op)
            )
        )
        assert row is not None
        return row


def test_chase_keeps_the_step_waiting_and_re_escalates(world: World) -> None:
    run_id, task = _escalate(world)
    world.engine.decide_task(task.id, user_id=world.user("hr_ops"), option="chase")
    assert _steps(world, run_id)["await_documents"].status is m.StepStatus.WAITING_TIMER
    assert _run(world, run_id).status is m.RunStatus.WAITING
    assert _tasks(world, run_id) == []
    # Still nothing after another escalation window: HR is asked again
    world.clock.advance(hours=73)
    world.engine.run_until_idle()
    (again,) = _tasks(world, run_id)
    assert again.action == "sla.escalation" and again.id != task.id
    # And the joiner can still finish the step
    assert world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")


def test_finishing_a_step_closes_its_escalation(world: World) -> None:
    run_id, task = _escalate(world)
    assert world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    assert _tasks(world, run_id) == []
    assert [t.id for t in _tasks(world, run_id, m.HumanTaskStatus.CANCELLED)] == [task.id]
    with pytest.raises(TaskDecisionRefused):
        world.engine.decide_task(
            task.id, user_id=world.user("hr_ops"), option="fail", reason="late"
        )
    assert _steps(world, run_id)["await_documents"].status is m.StepStatus.SUCCEEDED


def test_cancel_stops_ready_steps_without_side_effects(world: World) -> None:
    run_id = _start(world)
    world.engine.run_until_idle()
    assert world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    assert _steps(world, run_id)["provision_identity"].status is m.StepStatus.READY
    world.engine.cancel_run(run_id, actor="hr", reason="candidate withdrew")
    world.engine.run_until_idle()
    assert world.directory.calls["create"] == 0 and world.assets.calls["allocate"] == 0
    steps = _steps(world, run_id)
    assert _run(world, run_id).status is m.RunStatus.CANCELLED
    for sid in ("provision_identity", "select_laptop", "order_laptop", "probation_confirmation"):
        assert steps[sid].status is m.StepStatus.SKIPPED, sid


def test_a_step_leased_before_cancel_skips_itself(world: World) -> None:
    run_id = _start(world)
    world.engine.run_until_idle()
    world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    with world.sessions.begin() as s:  # cancelled behind the worker's back, after it was queued
        s.execute(
            update(m.WorkflowRun)
            .where(m.WorkflowRun.id == run_id)
            .values(status=m.RunStatus.CANCELLED)
        )
    world.engine.run_until_idle()
    assert world.directory.calls["create"] == 0
    assert _steps(world, run_id)["provision_identity"].status is m.StepStatus.SKIPPED


def test_a_failed_branch_stops_its_siblings(world: World) -> None:
    world.directory.faults = ["permanent"]
    run_id = _start(world)
    _to_completion(world, run_id)
    (task,) = _tasks(world, run_id)
    world.engine.decide_task(task.id, user_id=world.user("hr_ops"), option="fail", reason="bad")
    world.engine.run_until_idle()
    assert _run(world, run_id).status is m.RunStatus.FAILED
    assert _steps(world, run_id)["probation_confirmation"].status is m.StepStatus.SKIPPED


def test_retry_after_a_permanent_error_calls_again(world: World) -> None:
    world.directory.faults = ["permanent"]
    run_id = _start(world)
    _to_completion(world, run_id)
    assert _ledger_row(world, run_id, "provision_identity", "directory.create").status is (
        m.LedgerStatus.FAILED_PERMANENT
    )
    (task,) = _tasks(world, run_id)
    world.engine.decide_task(task.id, user_id=world.user("hr_ops"), option="retry")
    world.engine.run_until_idle()
    assert _steps(world, run_id)["provision_identity"].status is m.StepStatus.SUCCEEDED
    assert world.directory.calls["create"] == 2
    row = _ledger_row(world, run_id, "provision_identity", "directory.create")
    assert row.status is m.LedgerStatus.SUCCEEDED


def test_a_named_task_belongs_to_that_person_only(world: World) -> None:
    run_id = _start(world, grade="G2")
    _to_completion(world, run_id)
    (task,) = _tasks(world, run_id)
    named, other = world.user("manager"), world.user("manager")
    with world.sessions.begin() as s:
        s.execute(
            update(m.HumanTask).where(m.HumanTask.id == task.id).values(assignee_user_id=named)
        )
    with pytest.raises(TaskDecisionRefused, match="named person"):
        world.engine.decide_task(task.id, user_id=other, option="confirm")
    world.engine.decide_task(task.id, user_id=named, option="confirm")
    assert _run(world, run_id).status is m.RunStatus.COMPLETED


def test_override_closes_the_ledger_row(world: World) -> None:
    world.directory.faults = ["conflict"]
    run_id = _start(world)
    _to_completion(world, run_id)
    (task,) = _tasks(world, run_id)
    world.engine.decide_task(
        task.id, user_id=world.user("hr_ops"), option="override", reason="checked",
        output={"external_id": "manual-1"},
    )  # fmt: skip
    row = _ledger_row(world, run_id, "provision_identity", "directory.create")
    assert row.status is m.LedgerStatus.FAILED_PERMANENT
    assert row.response is not None and "override_by" in row.response
