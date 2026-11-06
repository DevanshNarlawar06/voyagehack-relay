"""Engine simulations against Postgres with a fake clock and in-memory connectors (PRD 11, 21)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, func, select

from relay.core.audit.verifier import verify_chain
from relay.core.engine.engine import TaskDecisionRefused, TriggerRefused
from relay.core.policy import PolicyService
from relay.db import models as m
from tests.simulation.harness import TOY, World, build_world

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def sa_engine(db_url: str) -> Iterator[SAEngine]:
    engine = create_engine(db_url, pool_size=10)
    yield engine
    engine.dispose()


@pytest.fixture()
def world(sa_engine: SAEngine) -> World:
    return build_world(sa_engine)


def _steps(w: World, run_id: uuid.UUID) -> dict[str, m.StepRun]:
    with w.sessions() as s:
        return {
            sr.step_id: sr for sr in s.scalars(select(m.StepRun).where(m.StepRun.run_id == run_id))
        }


def _run(w: World, run_id: uuid.UUID) -> m.WorkflowRun:
    with w.sessions() as s:
        run = s.get(m.WorkflowRun, run_id)
        assert run is not None
        return run


def _tasks(
    w: World, run_id: uuid.UUID, status: m.HumanTaskStatus = m.HumanTaskStatus.OPEN
) -> list[m.HumanTask]:
    with w.sessions() as s:
        return list(
            s.scalars(
                select(m.HumanTask).where(
                    m.HumanTask.run_id == run_id, m.HumanTask.status == status
                )
            )
        )


def _start(w: World, grade: str = "G2", candidate: str = "c1") -> uuid.UUID:
    return w.engine.start_run(
        "toy_onboarding",
        {"candidate": candidate, "grade": grade},
        actor="test",
        subject_id=candidate,
    )


def _to_completion(w: World, run_id: uuid.UUID) -> None:
    w.engine.run_until_idle()
    assert w.engine.record_subject_action(
        run_id, "documents.uploaded", {"files": 3}, actor="joiner"
    )
    w.engine.run_until_idle()


# ---------------------------------------------------------------- happy path


def test_toy_workflow_runs_end_to_end(world: World) -> None:
    run_id = _start(world)
    world.engine.run_until_idle()
    steps = _steps(world, run_id)
    assert steps["create_prehire"].status is m.StepStatus.SUCCEEDED
    assert steps["await_documents"].status is m.StepStatus.WAITING_TIMER
    assert steps["provision_identity"].status is m.StepStatus.PENDING
    assert _run(world, run_id).status is m.RunStatus.WAITING

    world.clock.advance(hours=2)
    _to_completion(world, run_id)
    steps = _steps(world, run_id)
    for sid in ("provision_identity", "select_laptop", "order_laptop"):
        assert steps[sid].status is m.StepStatus.SUCCEEDED, sid
    # Reserved probation decision goes to the manager
    (task,) = _tasks(world, run_id)
    assert task.kind is m.HumanTaskKind.RESERVED_DECISION and task.assignee_role == "manager"
    assert task.step_run_id == steps["probation_confirmation"].id

    manager = world.user("manager")
    world.engine.decide_task(task.id, user_id=manager, option="confirm")
    run = _run(world, run_id)
    assert run.status is m.RunStatus.COMPLETED
    steps = _steps(world, run_id)
    assert steps["probation_confirmation"].human_touched
    assert not steps["order_laptop"].human_touched
    assert steps["await_documents"].subject_actions == 1
    assert run.context["ctx"]["code"] == "E-c1"
    assert world.assets.calls["allocate"] == 1 and world.directory.calls["create"] == 1
    # Every step carries the decision that authorized it (PRD 6.2)
    assert all(
        sr.decision_id is not None for sr in steps.values() if sr.status is m.StepStatus.SUCCEEDED
    )
    with world.sessions() as s:
        alloc = s.scalar(
            select(func.count())
            .select_from(m.CostEvent)
            .where(m.CostEvent.run_id == run_id, m.CostEvent.kind == m.CostKind.INFRA_ALLOC)
        )
        assert alloc == 1
        assert verify_chain(s).ok


def test_when_false_skips_the_step(world: World) -> None:
    run_id = _start(world, grade="G4")  # probation 3 months -> no confirmation step
    _to_completion(world, run_id)
    steps = _steps(world, run_id)
    assert steps["probation_confirmation"].status is m.StepStatus.SKIPPED
    assert _run(world, run_id).status is m.RunStatus.COMPLETED


# ---------------------------------------------------------------- governance


def test_expensive_laptop_waits_for_named_approver(world: World) -> None:
    run_id = _start(world, grade="G5")  # 210000 > threshold
    _to_completion(world, run_id)
    steps = _steps(world, run_id)
    assert steps["order_laptop"].status is m.StepStatus.WAITING_HUMAN
    assert world.assets.calls["allocate"] == 0
    (task,) = _tasks(world, run_id)
    assert task.kind is m.HumanTaskKind.APPROVAL and task.assignee_role == "approver_it_spend"

    hr = world.user("hr_ops")
    with pytest.raises(TaskDecisionRefused):
        world.engine.decide_task(task.id, user_id=hr, option="approve")
    approver = world.user("approver_it_spend")
    world.engine.decide_task(task.id, user_id=approver, option="approve")
    world.engine.run_until_idle()
    steps = _steps(world, run_id)
    assert steps["order_laptop"].status is m.StepStatus.SUCCEEDED
    assert steps["order_laptop"].human_touched
    assert world.assets.calls["allocate"] == 1


def test_reject_requires_reason_and_fails_the_run(world: World) -> None:
    run_id = _start(world, grade="G5")
    _to_completion(world, run_id)
    (task,) = _tasks(world, run_id)
    approver = world.user("approver_it_spend")
    with pytest.raises(TaskDecisionRefused, match="reason"):
        world.engine.decide_task(task.id, user_id=approver, option="reject")
    world.engine.decide_task(task.id, user_id=approver, option="reject", reason="too expensive")
    assert _run(world, run_id).status is m.RunStatus.FAILED


def test_kill_switch_pauses_and_release_resumes(world: World) -> None:
    run_id = _start(world)
    world.engine.run_until_idle()
    with world.sessions.begin() as s:
        PolicyService().set_kill_switch(s, "workflow", "toy_onboarding", active=True, actor="hr")
    assert world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    world.engine.run_until_idle()
    steps = _steps(world, run_id)
    assert steps["provision_identity"].status is m.StepStatus.PAUSED
    assert _run(world, run_id).status is m.RunStatus.PAUSED
    assert world.directory.calls["create"] == 0

    with world.sessions.begin() as s:
        PolicyService().set_kill_switch(s, "workflow", "toy_onboarding", active=False, actor="hr")
    world.engine.run_until_idle()
    steps = _steps(world, run_id)
    assert steps["provision_identity"].status is m.StepStatus.SUCCEEDED
    assert world.directory.calls["create"] == 1


@pytest.mark.parametrize(
    ("scope", "target"), [("global", "*"), ("step", "order_laptop"), ("connector", "assets")]
)
def test_each_kill_switch_scope_pauses_a_real_step(world: World, scope: str, target: str) -> None:
    run_id = _start(world)
    world.engine.run_until_idle()
    with world.sessions.begin() as s:
        PolicyService().set_kill_switch(s, scope, target, active=True, actor="hr")
    world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    world.engine.run_until_idle()
    steps = _steps(world, run_id)
    assert steps["order_laptop"].status in (m.StepStatus.PAUSED, m.StepStatus.PENDING)
    assert any(sr.status is m.StepStatus.PAUSED for sr in steps.values())
    assert world.assets.calls["allocate"] == 0
    assert _run(world, run_id).status is m.RunStatus.PAUSED


def test_required_decision_must_exist(sa_engine: SAEngine) -> None:
    gated = TOY.replace("name: toy_onboarding", "name: gated").replace(
        "trigger: { event: toy.hired }",
        "trigger: { event: toy.hired, require_decision: hiring.offer.approve }",
    )
    w = build_world(sa_engine, gated)
    with pytest.raises(TriggerRefused, match="no recorded"):
        w.engine.start_run("gated", {"candidate": "c", "grade": "G2"}, actor="t", subject_id="c")
    hr = w.user("hr_admin")
    approver = w.user("approver_offer")
    with w.sessions.begin() as s:
        bad = m.HumanTask(kind=m.HumanTaskKind.RESERVED_DECISION, action="hiring.offer.approve", assignee_role="approver_offer",
                          status=m.HumanTaskStatus.DECIDED, decision={"option": "approve"}, decided_by=hr)  # fmt: skip
        good = m.HumanTask(kind=m.HumanTaskKind.RESERVED_DECISION, action="hiring.offer.approve", assignee_role="approver_offer",
                           status=m.HumanTaskStatus.DECIDED, decision={"option": "approve"}, decided_by=approver)  # fmt: skip
        s.add_all([bad, good])
        s.flush()
        bad_id, good_id = bad.id, good.id
    with pytest.raises(TriggerRefused, match="not decided by a holder"):
        w.engine.start_run(
            "gated",
            {"candidate": "c", "grade": "G2", "decision_task_id": str(bad_id)},
            actor="t",
            subject_id="c",
        )
    run_id = w.engine.start_run(
        "gated",
        {"candidate": "c", "grade": "G2", "decision_task_id": str(good_id)},
        actor="t",
        subject_id="c",
    )
    assert _run(w, run_id).status in (m.RunStatus.RUNNING, m.RunStatus.WAITING)


# ---------------------------------------------------------------- failure handling


def test_transient_errors_retry_then_succeed(world: World) -> None:
    world.assets.faults = ["transient", "transient"]
    run_id = _start(world)
    _to_completion(world, run_id)
    assert _steps(world, run_id)["order_laptop"].status is m.StepStatus.RETRY_SCHEDULED
    for _ in range(2):  # exponential backoff: 1s, then 2s
        world.clock.advance(seconds=10)
        world.engine.run_until_idle()
    steps = _steps(world, run_id)
    assert steps["order_laptop"].status is m.StepStatus.SUCCEEDED
    assert steps["order_laptop"].attempt == 2
    assert len(world.assets.records) == 1


def test_outage_parks_with_a_human_task_then_retry(world: World) -> None:
    world.assets.faults = ["transient"] * 3
    run_id = _start(world)
    _to_completion(world, run_id)
    for _ in range(3):
        world.clock.advance(seconds=30)
        world.engine.run_until_idle()
    steps = _steps(world, run_id)
    assert steps["order_laptop"].status is m.StepStatus.PARKED
    (task,) = _tasks(world, run_id)
    assert task.kind is m.HumanTaskKind.OUTAGE and task.step_run_id == steps["order_laptop"].id
    world.engine.decide_task(task.id, user_id=world.user("hr_ops"), option="retry")
    world.engine.run_until_idle()
    assert _steps(world, run_id)["order_laptop"].status is m.StepStatus.SUCCEEDED


def test_conflict_goes_to_hr_with_fields_and_override(world: World) -> None:
    world.directory.faults = ["conflict"]
    run_id = _start(world)
    _to_completion(world, run_id)
    (task,) = _tasks(world, run_id)
    assert task.kind is m.HumanTaskKind.CONFLICT and task.context_packet["fields"] == ["full_name"]
    world.engine.decide_task(
        task.id,
        user_id=world.user("hr_ops"),
        option="override",
        reason="verified by phone",
        output={"external_id": "manual-1"},
    )
    world.engine.run_until_idle()
    sr = _steps(world, run_id)["provision_identity"]
    assert sr.status is m.StepStatus.SUCCEEDED and sr.human_touched


def test_permanent_error_parks_as_exception(world: World) -> None:
    world.directory.faults = ["permanent"]
    run_id = _start(world)
    _to_completion(world, run_id)
    assert _steps(world, run_id)["provision_identity"].status is m.StepStatus.PARKED
    (task,) = _tasks(world, run_id)
    assert task.kind is m.HumanTaskKind.EXCEPTION


# ---------------------------------------------------------------- SLA chasing


def test_sla_reminders_then_escalation_on_subject_wait(world: World) -> None:
    run_id = _start(world)
    world.engine.run_until_idle()
    world.clock.advance(hours=25)
    world.engine.run_until_idle()
    assert _run(world, run_id).context["sla"]["reminders"] == 1
    world.clock.advance(hours=24)
    world.engine.run_until_idle()
    assert _run(world, run_id).context["sla"]["reminders"] == 2
    assert _tasks(world, run_id) == []
    world.clock.advance(hours=24)
    world.engine.run_until_idle()
    (task,) = _tasks(world, run_id)
    assert task.action == "sla.escalation" and task.assignee_role == "hr_ops"
    # The subject finally uploads; the wait window is recorded for cycle-time exclusion
    assert world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    sr = _steps(world, run_id)["await_documents"]
    assert sr.started_at is not None and sr.finished_at is not None
    assert (sr.finished_at - sr.started_at).total_seconds() == 73 * 3600


# ---------------------------------------------------------------- exactly once


def test_crash_after_external_call_does_not_duplicate(world: World) -> None:
    """PRD 11.5: the effect lands, the worker dies before recording it; the retry reconciles."""
    world.assets.faults = ["crash_after"]
    run_id = _start(world)
    _to_completion(world, run_id)
    assert _steps(world, run_id)["order_laptop"].status is m.StepStatus.RETRY_SCHEDULED
    world.clock.advance(seconds=10)
    world.engine.run_until_idle()
    assert _steps(world, run_id)["order_laptop"].status is m.StepStatus.SUCCEEDED
    assert len(world.assets.records) == 1
    assert world.assets.calls["allocate"] == 1  # reconcile found it: no second call
    with world.sessions() as s:
        row = s.scalar(select(m.IdempotencyLedger).where(m.IdempotencyLedger.connector == "assets"))
        assert row is not None and row.status is m.LedgerStatus.SUCCEEDED


def test_expired_lease_is_reclaimed_and_not_counted_as_failure(world: World) -> None:
    """A worker that leased a step and died: the reaper puts it back to READY, attempt unchanged."""
    run_id = _start(world)
    world.engine.run_until_idle()
    world.engine.record_subject_action(run_id, "documents.uploaded", actor="joiner")
    dead = world.worker("dead")
    leased = dead._lease(10)  # leases READY steps and then "dies"
    assert leased
    assert world.engine.tick().reclaimed == 0  # lease still valid
    world.clock.advance(seconds=60)
    world.engine.run_until_idle()
    steps = _steps(world, run_id)
    assert all(
        steps[i].status is m.StepStatus.SUCCEEDED for i in ("provision_identity", "order_laptop")
    )
    assert steps["provision_identity"].attempt == 0
    assert world.directory.calls["create"] == 1


def test_two_workers_run_many_runs_exactly_once(world: World) -> None:
    run_ids = [_start(world, candidate=f"c{i}") for i in range(12)]
    world.engine.run_until_idle()
    for rid in run_ids:
        world.engine.record_subject_action(rid, "documents.uploaded", actor="joiner")
    workers = [world.worker("wA"), world.worker("wB")]
    errors: list[BaseException] = []

    def loop(e: object) -> None:
        try:
            for _ in range(200):
                if not e.tick(max_steps=3).busy:  # type: ignore[attr-defined]
                    break
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=loop, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    world.engine.run_until_idle()
    assert world.assets.calls["allocate"] == 12
    assert world.directory.calls["create"] == 12
    assert len(world.assets.records) == 12
    with world.sessions() as s:
        assert verify_chain(s).ok
