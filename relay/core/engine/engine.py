"""Durable workflow interpreter on Postgres (PRD 7.2, 11).

The orchestrator is plain code, never an LLM. One `Engine` per worker process; any number of
workers can run against the same database:

- `tick()` = one scheduler pass: release paused steps whose kill switch is gone, reclaim expired
  leases, fire due timers, then lease READY steps with `FOR UPDATE SKIP LOCKED` and execute them.
- Every step builds its `ActionRequest` in `_request()` and calls `authorize()` before doing
  anything; the `decisions` row id is stored on the step (PRD 6.2).
- Connector side effects go through the idempotency ledger; the pending row is committed before
  the external call (PRD 11.5).
- Transitions that touch a run lock its `workflow_runs` row, so parallel branches finishing on
  two workers cannot race the run's completion.

Metrics contract (from WP2, PR #27): `human_touched` on any step a non-subject human acted on;
`subject_actions` + `started_at`/`finished_at` on subject waits; `step_run_id` on every task;
`allocate_infra` on completion; child runs carry `parent_run_id`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from relay.config import get_config
from relay.core.audit.writer import AuditEntry, append_event, policy_audit_hook
from relay.core.cost.writer import allocate_infra
from relay.core.dsl.model import Step, WorkflowDefinition, parse_duration
from relay.core.engine.ports import ConnectorResultLike, EngineDeps
from relay.core.engine.sealed import unseal
from relay.core.errors import (
    ConflictError,
    LowConfidence,
    PermanentError,
    PolicyDenied,
    RelayStepError,
    TransientError,
)
from relay.core.ledger import ledger
from relay.core.policy import ActionRequest, Actor, Decision, PolicyService, SubjectRef, cel
from relay.core.registry.store import DefinitionStore
from relay.db import models as m

log = logging.getLogger(__name__)

TERMINAL_OK = {m.StepStatus.SUCCEEDED, m.StepStatus.SKIPPED}
TERMINAL = TERMINAL_OK | {m.StepStatus.FAILED}
OPEN_TASK = (m.HumanTaskStatus.OPEN, m.HumanTaskStatus.CLAIMED)
RUN_ENDED = (m.RunStatus.CANCELLED, m.RunStatus.COMPLETED, m.RunStatus.FAILED)
# Steps a human task can still act on. RUNNING is excluded: a worker holds the lease.
DECIDABLE = {
    m.StepStatus.WAITING_HUMAN,
    m.StepStatus.WAITING_TIMER,
    m.StepStatus.PARKED,
    m.StepStatus.RETRY_SCHEDULED,
    m.StepStatus.PAUSED,
}
EXCEPTION_OPTIONS = ["retry", "override", "fail"]
ESCALATION_OPTIONS = ["chase", "override", "fail"]
ESCALATION_ACTION = "sla.escalation"
APPROVAL_OPTIONS = ["approve", "reject"]
ENGINE_ACTOR = Actor(type="system", id="engine", roles=["system"])
DEFAULT_POOL = "pool:hr_ops"


class TriggerRefused(ValueError):
    """A run could not start, e.g. the required human decision is missing (audited as DENY)."""


class TaskDecisionRefused(ValueError):
    """The user may not decide this task, or the option is invalid."""


@dataclass(frozen=True)
class TickResult:
    reclaimed: int = 0
    resumed: int = 0
    timers: int = 0
    executed: int = 0

    @property
    def busy(self) -> bool:
        """True if this pass did any work."""
        return bool(self.reclaimed or self.resumed or self.timers or self.executed)


class Engine:
    """Starts runs, executes steps and applies human decisions."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        deps: EngineDeps,
        *,
        policy: PolicyService | None = None,
        definitions: DefinitionStore | None = None,
        worker_id: str | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        """Bind a session factory and the outside world."""
        cfg = get_config()
        self.sessions = sessions
        self.deps = deps
        self.policy = policy or PolicyService(
            audit=policy_audit_hook, confidence_thresholds=deps.confidence_thresholds
        )
        self.definitions = definitions or DefinitionStore()
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.lease = timedelta(seconds=lease_seconds or cfg.app.engine.lease_seconds)

    # ================================================================ starting runs

    def handle_event(
        self,
        event: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
        subject_id: str,
        parent_run_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Start every active workflow triggered by `event`. Returns the new run ids."""
        with self.sessions.begin() as s:
            wfs = self.definitions.active_for_event(s, event)
        return [
            self.start_run(
                wf.name, payload, actor=actor, subject_id=subject_id, parent_run_id=parent_run_id
            )
            for wf in wfs
        ]

    def start_run(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
        subject_id: str,
        parent_run_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Start the active version of workflow `name` for `subject_id`."""
        with self.sessions.begin() as s:
            return self._start_run_in(
                s, name, payload, actor=actor, subject_id=subject_id, parent_run_id=parent_run_id
            )

    def _start_run_in(
        self,
        s: Session,
        name: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
        subject_id: str,
        parent_run_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Start a run inside the caller's transaction."""
        now = self.deps.clock()
        trigger = dict(payload)
        wf = self.definitions.active(s, name)
        if wf.trigger.require_decision:
            self._require_decision(s, wf, trigger, actor, subject_id)
        act = {"trigger": trigger, "now": now.isoformat(), "ctx": {}}
        ctx = {k: cel.evaluate_py(expr, act) for k, expr in wf.context.items()}
        run = m.WorkflowRun(
            definition_name=wf.name,
            definition_version=wf.version,
            subject_type=wf.subject,
            subject_id=subject_id,
            status=m.RunStatus.RUNNING,
            context={
                "ctx": ctx,
                "trigger": trigger,
                "sla": {"reminders": 0},
                "subject_label": str(
                    trigger.get("subject_label") or f"{wf.subject} {subject_id[:8]}"
                ),
            },
            started_by_actor=actor,
            started_at=now,
            trace_id=uuid.uuid4().hex,
            parent_run_id=parent_run_id,
        )
        s.add(run)
        s.flush()
        for step in wf.flat_steps():
            s.add(m.StepRun(run_id=run.id, step_id=step.id, kind=m.StepKind(step.kind)))
        s.flush()
        self._audit(s, wf, run, None, "run.started", data={"actor": actor})
        self._advance(s, run, wf)
        return run.id

    def _require_decision(
        self,
        s: Session,
        wf: WorkflowDefinition,
        trigger: dict[str, Any],
        actor: str,
        subject_id: str,
    ) -> None:
        """The trigger must point at a decided, approving human task for the required action,
        decided by a user holding the approver role (PRD 5.1 step 0)."""
        action = wf.trigger.require_decision
        assert action is not None
        task_id = trigger.get("decision_task_id")
        task = s.get(m.HumanTask, uuid.UUID(str(task_id))) if task_id else None
        approver = self.policy.policies(s).reserved_approver(action)
        problem = None
        if task is None:
            problem = f"no recorded {action} decision"
        elif task.action != action or task.status is not m.HumanTaskStatus.DECIDED:
            problem = f"task {task.id} is not a decided {action}"
        elif (task.decision or {}).get("option") not in ("approve", "approved"):
            problem = f"{action} was not approved"
        else:
            user = s.get(m.User, task.decided_by) if task.decided_by else None
            if user is None or approver not in user.roles:
                problem = f"{action} was not decided by a holder of {approver}"
        if problem:
            append_event(
                s,
                AuditEntry(
                    actor_type=m.ActorType.SYSTEM,
                    actor_id="engine",
                    on_behalf_of=actor,
                    action=f"run.refused:{wf.name}",
                    subject_type=wf.subject,
                    subject_id=subject_id,
                    effect="DENY",
                    workflow_def_version=f"{wf.name}@{wf.version}",
                    reasoning=problem,
                ),
            )
            s.commit()
            raise TriggerRefused(problem)

    # ================================================================ scheduler

    def tick(self, max_steps: int = 10) -> TickResult:
        """One scheduler pass. Kill switches are re-read first, so a switch set by anyone
        applies at the very next step boundary (PRD 6.9, 12.6)."""
        with self.sessions.begin() as s:
            self.policy.kill_switches(s, fresh=True)
        resumed = self._resume_paused()
        reclaimed = self._reap()
        timers = self._fire_timers()
        executed = 0
        for step_run_id in self._lease(max_steps):
            self._execute(step_run_id)
            executed += 1
        return TickResult(reclaimed, resumed, timers, executed)

    def run_until_idle(self, max_passes: int = 1000) -> int:
        """Tick until a pass does nothing (simulation and tests). Returns passes made."""
        for i in range(max_passes):
            if not self.tick().busy:
                return i + 1
        raise RuntimeError("engine did not become idle")

    def _lease(self, n: int) -> list[uuid.UUID]:
        now = self.deps.clock()
        with self.sessions.begin() as s:
            ids = list(
                s.scalars(
                    select(m.StepRun.id)
                    .where(
                        or_(
                            m.StepRun.status == m.StepStatus.READY,
                            and_(
                                m.StepRun.status == m.StepStatus.RETRY_SCHEDULED,
                                m.StepRun.next_attempt_at <= now,
                            ),
                        )
                    )
                    .order_by(m.StepRun.created_at)
                    .limit(n)
                    .with_for_update(skip_locked=True)
                )
            )
            if ids:
                s.execute(
                    update(m.StepRun)
                    .where(m.StepRun.id.in_(ids))
                    .values(
                        status=m.StepStatus.RUNNING,
                        lease_owner=self.worker_id,
                        lease_expires_at=now + self.lease,
                    )
                )
            return ids

    def _reap(self) -> int:
        """Expired leases go back to READY; reclaiming is not a failure (attempt unchanged)."""
        now = self.deps.clock()
        with self.sessions.begin() as s:
            res = s.execute(
                update(m.StepRun)
                .where(m.StepRun.status == m.StepStatus.RUNNING, m.StepRun.lease_expires_at < now)
                .values(status=m.StepStatus.READY, lease_owner=None, lease_expires_at=None)
                .returning(m.StepRun.id)
            )
            return len(res.all())

    def _resume_paused(self) -> int:
        resumed = 0
        with self.sessions.begin() as s:
            paused = list(
                s.scalars(
                    select(m.StepRun)
                    .where(m.StepRun.status == m.StepStatus.PAUSED)
                    .with_for_update(skip_locked=True)
                )
            )
            if not paused:
                return 0
            kills = self.policy.kill_switches(s, fresh=True)
            for sr in paused:
                run = s.get(m.WorkflowRun, sr.run_id)
                assert run is not None
                wf = self.definitions.get(s, run.definition_name, run.definition_version)
                if kills.hit(self._request(s, run, wf, wf.step(sr.step_id), sr, {})) is None:
                    sr.status = sr.paused_from or m.StepStatus.READY
                    sr.paused_from = None
                    resumed += 1
                    self._audit(s, wf, run, sr, "step.resumed")
                    self._advance(s, run, wf)
        return resumed

    # ================================================================ executing a step

    def _execute(self, step_run_id: uuid.UUID) -> None:
        try:
            self._execute_inner(step_run_id)
        except Exception:  # never let one step kill the worker loop
            log.exception("step %s crashed the executor; lease will expire and retry", step_run_id)

    def _execute_inner(self, step_run_id: uuid.UUID) -> None:
        with self.sessions() as s:
            sr = s.get(m.StepRun, step_run_id)
            if (
                sr is None
                or sr.status is not m.StepStatus.RUNNING
                or sr.lease_owner != self.worker_id
            ):
                return
            run = s.get(m.WorkflowRun, sr.run_id)
            assert run is not None
            wf = self.definitions.get(s, run.definition_name, run.definition_version)
            if run.status in RUN_ENDED:
                # The run was cancelled or failed after this step was queued: no side effects.
                sr.lease_owner = None
                self._set_status(
                    s, wf, run, sr, m.StepStatus.SKIPPED, data={"reason": f"run {run.status}"}
                )
                s.commit()
                return
            step = wf.step(sr.step_id)
            now = self.deps.clock()
            if sr.started_at is None:
                sr.started_at = now
            try:
                inputs = self._eval_map(step.inputs, run, now)
                attrs = self._eval_map(step.attributes, run, now)
            except cel.CelError as exc:
                self._on_error(s, run, wf, step, sr, PermanentError(f"bad expression: {exc}"))
                s.commit()
                return
            decision = self._authorize(s, run, wf, step, sr, attrs)
            if decision is None:
                s.commit()
                return
            try:
                if step.kind == "rule":
                    self._run_rule(s, run, wf, step, sr, inputs)
                elif step.kind == "connector":
                    self._run_connector(s, run, wf, step, sr, inputs)
                elif step.kind == "skill":
                    self._run_skill(s, run, wf, step, sr, inputs, attrs)
                elif step.kind == "human":
                    self._open_human_step(s, run, wf, step, sr, decision)
                else:
                    self._start_wait(s, run, wf, step, sr, now)
            except RelayStepError as exc:
                s.rollback()
                self._reload_and_fail(step_run_id, exc)
                return
            except Exception as exc:  # unknown -> transient (PRD 11.4)
                log.warning("step %s raised %s", sr.step_id, type(exc).__name__)
                s.rollback()
                self._reload_and_fail(step_run_id, TransientError(type(exc).__name__))
                return
            s.commit()

    def _reload_and_fail(self, step_run_id: uuid.UUID, exc: RelayStepError) -> None:
        with self.sessions.begin() as s:
            sr = s.get(m.StepRun, step_run_id, with_for_update=True)
            if sr is None or sr.status is not m.StepStatus.RUNNING:
                return
            run = s.get(m.WorkflowRun, sr.run_id)
            assert run is not None
            wf = self.definitions.get(s, run.definition_name, run.definition_version)
            self._on_error(s, run, wf, wf.step(sr.step_id), sr, exc)

    # ---------------------------------------------------------------- authorize

    def _request(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        attrs: Mapping[str, Any],
        actor: Actor | None = None,
    ) -> ActionRequest:
        """The ONLY place an ActionRequest for a step is built (issue #32: kill-switch fields are
        always filled from the run and step)."""
        consented = list(run.context.get("ctx", {}).get("consented_purposes") or [])
        approved_by = (sr.input or {}).get("approved_by")
        if actor is None and approved_by:
            actor = self._user_actor(s, uuid.UUID(approved_by))
        principal_roles = None
        if actor is None and step.kind == "skill":
            # PRD 6.7: an agent may do only what both its principal and its skill allow.
            skill = self.deps.skills.get(step.uses or "")
            actor = Actor(
                type="agent",
                id=f"skill:{step.uses}",
                allowed_actions=list(skill.allowed_actions) if skill else [],
            )
            principal_roles = list(ENGINE_ACTOR.roles)
        return ActionRequest(
            action=step.action,
            actor=actor or ENGINE_ACTOR,
            on_behalf_of=DEFAULT_POOL,
            principal_roles=principal_roles,
            subject=SubjectRef(type=run.subject_type, id=run.subject_id),
            resource={"inputs": sorted(step.inputs)},  # names only: no PII in decisions
            attributes={**attrs, "consented_purposes": consented},
            run_id=run.id,
            step_id=step.id,
            workflow=wf.name,
            skill=step.uses if step.kind == "skill" else None,
            connector=(step.uses or "").partition(".")[0] if step.kind == "connector" else None,
        )

    def _user_actor(self, s: Session, user_id: uuid.UUID) -> Actor:
        user = s.get(m.User, user_id)
        if user is None:
            raise PermanentError("approving user no longer exists")
        return Actor(type="human", id=str(user.id), roles=list(user.roles))

    def _authorize(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        attrs: Mapping[str, Any],
    ) -> Decision | None:
        """Authorize; returns the decision if execution may proceed, else applies the outcome."""
        req = self._request(s, run, wf, step, sr, attrs)
        decision, decision_id = self.policy.authorize(s, req)
        sr.decision_id = decision_id
        if decision.effect == "ALLOW":
            return decision
        if decision.is_kill_switch:
            sr.paused_from = m.StepStatus.READY
            self._set_status(s, wf, run, sr, m.StepStatus.PAUSED, decision=decision)
            run.status = m.RunStatus.PAUSED
            return None
        if decision.effect == "DENY":
            sr.error = {
                "type": "PolicyDenied",
                "reason": decision.reason,
                "rule_ids": decision.rule_ids,
            }
            self._set_status(s, wf, run, sr, m.StepStatus.FAILED, decision=decision)
            self._advance(s, run, wf)
            return None
        # REQUIRE_HUMAN
        if step.kind == "human":
            return decision  # a human step is the human decision itself
        kind = (
            m.HumanTaskKind.RESERVED_DECISION
            if "reserved" in decision.rule_ids
            else m.HumanTaskKind.LOW_CONFIDENCE
            if "confidence_gate" in decision.rule_ids
            else m.HumanTaskKind.APPROVAL
        )
        assert decision.approver_role is not None
        self._open_task(
            s, run, wf, step, sr, kind=kind, action=step.action, role=decision.approver_role,
            options=APPROVAL_OPTIONS,
            packet={"reason": decision.reason, "rule_ids": decision.rule_ids, "attributes": dict(attrs)},
        )  # fmt: skip
        return None

    # ---------------------------------------------------------------- step kinds

    def _finish(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        result: Mapping[str, Any],
        *,
        human_touched: bool = False,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Mark SUCCEEDED, bind outputs into ctx, advance the run."""
        now = self.deps.clock()
        result_py = dict(result)
        act = self._activation(run, now) | {"result": result_py}
        ctx = dict(run.context.get("ctx", {}))
        for key, expr in step.outputs.items():
            ctx[key] = cel.evaluate_py(expr, act)
        run.context = {**run.context, "ctx": ctx}
        sr.output = result_py
        sr.finished_at = now
        sr.human_touched = sr.human_touched or human_touched
        sr.lease_owner = None
        self._set_status(s, wf, run, sr, m.StepStatus.SUCCEEDED, data=dict(extra or {}))
        self._advance(s, run, wf)

    def _run_rule(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        inputs: dict[str, Any],
    ) -> None:
        assert step.uses is not None
        fn = self.deps.rules.get(step.uses)
        if fn is None:
            raise PermanentError(f"unknown rule {step.uses}")
        self._finish(s, run, wf, step, sr, fn(**inputs))

    def _run_connector(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        inputs: dict[str, Any],
    ) -> None:
        assert step.uses is not None
        try:
            conn, op = self.deps.connector_op(step.uses)
        except KeyError as exc:
            raise PermanentError(str(exc.args[0])) from None
        key = ledger.idempotency_key(run.id, step.id, step.uses)
        side_effect = conn.ops()[op].side_effect
        result: ConnectorResultLike | None = None
        replay: dict[str, Any] | None = None
        if side_effect:
            entry = ledger.claim(s, key, conn.name, op, inputs)
            s.commit()  # the pending row must be durable before we call out (PRD 11.5)
            if entry.status is m.LedgerStatus.SUCCEEDED:
                replay = {"external_id": entry.external_id, **(entry.response or {})}
            elif entry.status is m.LedgerStatus.FAILED_PERMANENT:
                raise PermanentError(f"{step.uses} already failed permanently")
            elif not entry.fresh:
                result = asyncio.run(conn.reconcile(op, key, unseal(inputs)))
        if replay is None and result is None:
            # Sealed values are opened only here, for the outgoing call (PRD 10.1).
            result = asyncio.run(conn.call(op, unseal(inputs), idempotency_key=key))
        sr = self._relock(s, sr)
        if replay is not None:
            self._finish(s, run, wf, step, sr, replay, extra={"replayed": True})
            return
        assert result is not None
        out = {"external_id": result.external_id, **result.response}
        if side_effect:
            ledger.complete(
                s,
                key,
                external_id=result.external_id,
                response=dict(result.response),
                at=self.deps.clock(),
            )
        if result.duplicates:
            self._audit(
                s,
                wf,
                run,
                sr,
                "connector.duplicates",
                data={"duplicates": result.duplicates, "connector": step.uses},
            )
        self._finish(s, run, wf, step, sr, out, extra={"reconciled": result.reconciled})

    def _relock(self, s: Session, sr: m.StepRun) -> m.StepRun:
        """After a commit, re-read the step and confirm we still hold its lease."""
        fresh = s.get(m.StepRun, sr.id, with_for_update=True, populate_existing=True)
        if (
            fresh is None
            or fresh.status is not m.StepStatus.RUNNING
            or fresh.lease_owner != self.worker_id
        ):
            raise TransientError("lease lost during external call")
        run = s.get(m.WorkflowRun, fresh.run_id, populate_existing=True)
        assert run is not None
        return fresh

    def _run_skill(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        inputs: dict[str, Any],
        attrs: dict[str, Any],
    ) -> None:
        assert step.uses is not None
        skill = self.deps.skills.get(step.uses)
        if skill is None:
            raise PermanentError(f"unknown skill {step.uses}")
        # Skills run in process and return masked output, so they may see sealed inputs opened.
        out = skill.run(unseal(inputs), run_id=run.id, step_run_id=sr.id)
        stamp = {
            "skill_version": out.skill_version,
            "prompt_version": out.prompt_version,
            "model_id": out.model_id,
        }
        if out.confidence is not None:
            gate = self._request(s, run, wf, step, sr, {**attrs, "confidence": out.confidence})
            decision, decision_id = self.policy.authorize(s, gate)
            sr.decision_id = decision_id
            if decision.effect != "ALLOW":
                sr.output = dict(out.output)
                self._open_task(
                    s, run, wf, step, sr, kind=m.HumanTaskKind.LOW_CONFIDENCE, action=step.action,
                    role=decision.approver_role or "hr_ops", options=["accept", "retry", "override", "fail"],
                    packet={"reason": decision.reason, "confidence": out.confidence, "proposed": dict(out.output), **stamp},
                )  # fmt: skip
                return
        self._finish(
            s,
            run,
            wf,
            step,
            sr,
            out.output,
            extra={**stamp, "confidence": out.confidence, "reasoning": out.reasoning},
        )

    def _open_human_step(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        decision: Decision,
    ) -> None:
        assert step.task is not None
        now = self.deps.clock()
        act = self._activation(run, now)
        packet = {
            f"context_{i}": cel.evaluate_py(e, act) for i, e in enumerate(step.task.context_from)
        }
        user = cel.evaluate_py(step.task.assignee_user, act) if step.task.assignee_user else None
        reserved = self.policy.policies(s).reserved_approver(step.action) is not None
        self._open_task(
            s, run, wf, step, sr,
            kind=m.HumanTaskKind.RESERVED_DECISION if reserved else m.HumanTaskKind.APPROVAL,
            action=step.action, role=step.task.assignee_role, options=list(step.task.options),
            packet={**packet, "policy_reason": decision.reason},
            user_id=uuid.UUID(str(user)) if user else None,
        )  # fmt: skip

    def _start_wait(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        now: datetime,
    ) -> None:
        sr.lease_owner = None
        if step.until:
            due = datetime.fromisoformat(
                str(cel.evaluate_py(step.until, self._activation(run, now)))
            )
            s.add(m.Timer(step_run_id=sr.id, due_at=due, purpose=m.TimerPurpose.WAIT))
        self._set_status(s, wf, run, sr, m.StepStatus.WAITING_TIMER)
        self._advance(s, run, wf)

    # ---------------------------------------------------------------- errors

    def _on_error(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        exc: RelayStepError,
    ) -> None:
        """Apply the PRD 11.4 error taxonomy."""
        now = self.deps.clock()
        sr.lease_owner = None
        sr.error = {"type": type(exc).__name__, "reason": exc.reason, **exc.data}
        if isinstance(exc, ConflictError):
            sr.error["fields"] = exc.fields
            self._open_task(s, run, wf, step, sr, kind=m.HumanTaskKind.CONFLICT, action=step.action, role="hr_ops",
                            options=EXCEPTION_OPTIONS, packet={"reason": exc.reason, "fields": exc.fields})  # fmt: skip
            return
        if isinstance(exc, LowConfidence):
            self._open_task(s, run, wf, step, sr, kind=m.HumanTaskKind.LOW_CONFIDENCE, action=step.action,
                            role="hr_ops", options=EXCEPTION_OPTIONS, packet={"reason": exc.reason})  # fmt: skip
            return
        if isinstance(exc, PolicyDenied):
            self._set_status(s, wf, run, sr, m.StepStatus.FAILED)
            self._advance(s, run, wf)
            return
        retry = step.retry or wf.defaults.retry
        if isinstance(exc, TransientError):
            sr.attempt += 1
            if sr.attempt < retry.max_attempts:
                sr.next_attempt_at = now + retry.delay(sr.attempt)
                self._set_status(
                    s, wf, run, sr, m.StepStatus.RETRY_SCHEDULED, data={"attempt": sr.attempt}
                )
                return
        elif step.uses and step.kind == "connector":
            ledger.fail_permanently(s, ledger.idempotency_key(run.id, step.id, step.uses), now)
        if step.on_failure == "fail":
            self._set_status(s, wf, run, sr, m.StepStatus.FAILED)
            self._advance(s, run, wf)
            return
        kind = (
            m.HumanTaskKind.OUTAGE if isinstance(exc, TransientError) else m.HumanTaskKind.EXCEPTION
        )
        self._open_task(s, run, wf, step, sr, kind=kind, action=step.action, role="hr_ops",
                        options=EXCEPTION_OPTIONS, packet={"reason": exc.reason, "attempts": sr.attempt},
                        status=m.StepStatus.PARKED)  # fmt: skip

    # ================================================================ human tasks

    def _open_task(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        *,
        kind: m.HumanTaskKind,
        action: str,
        role: str,
        options: list[str],
        packet: Mapping[str, Any],
        user_id: uuid.UUID | None = None,
        status: m.StepStatus = m.StepStatus.WAITING_HUMAN,
    ) -> m.HumanTask:
        task = m.HumanTask(
            run_id=run.id, step_run_id=sr.id, kind=kind, action=action, assignee_role=role,
            assignee_user_id=user_id, options=options, status=m.HumanTaskStatus.OPEN,
            context_packet={"step": step.id, "workflow": f"{wf.name}@{wf.version}", **packet},
        )  # fmt: skip
        s.add(task)
        s.flush()
        sr.lease_owner = None
        self._set_status(
            s, wf, run, sr, status, data={"task_id": str(task.id), "task_kind": kind.value}
        )
        self._advance(s, run, wf)
        return task

    def decide_task(
        self,
        task_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        option: str,
        reason: str | None = None,
        output: Mapping[str, Any] | None = None,
    ) -> None:
        """Apply a human decision. The user must be allowed to perform the task's action."""
        now = self.deps.clock()
        with self.sessions.begin() as s:
            task = s.get(m.HumanTask, task_id, with_for_update=True)
            if task is None or task.status not in (
                m.HumanTaskStatus.OPEN,
                m.HumanTaskStatus.CLAIMED,
            ):
                raise TaskDecisionRefused("task is not open")
            if option not in task.options:
                raise TaskDecisionRefused(f"option must be one of {task.options}")
            if option in ("reject", "fail", "override") and not reason:
                raise TaskDecisionRefused("a reason is required to reject, fail or override")
            actor = self._user_actor(s, user_id)
            sr = (
                s.get(m.StepRun, task.step_run_id, with_for_update=True)
                if task.step_run_id
                else None
            )
            run = s.get(m.WorkflowRun, task.run_id, with_for_update=True) if task.run_id else None
            if sr is None or run is None:
                raise TaskDecisionRefused("task is not linked to a step")
            wf = self.definitions.get(s, run.definition_name, run.definition_version)
            step = wf.step(sr.step_id)
            # The decider must pass authorize() for the task's action, as themselves.
            if task.kind in (m.HumanTaskKind.RESERVED_DECISION, m.HumanTaskKind.APPROVAL):
                req = self._request(s, run, wf, step, sr, {}, actor=actor).model_copy(
                    update={"action": task.action}
                )
            else:
                req = self._request(s, run, wf, step, sr, {}, actor=actor).model_copy(
                    update={"action": "tasks.decide"}
                )
            decision, decision_id = self.policy.authorize(s, req)
            if decision.effect != "ALLOW" or task.assignee_role not in actor.roles:
                raise TaskDecisionRefused(f"{actor.id} may not decide this task: {decision.reason}")
            if task.assignee_user_id is not None and task.assignee_user_id != user_id:
                # PRD 2: a task routed to a named human is theirs alone, not the whole role's.
                raise TaskDecisionRefused("this task is assigned to a named person")
            if run.status in RUN_ENDED or sr.status not in DECIDABLE:
                raise TaskDecisionRefused(f"the step is {sr.status}, nothing is waiting on this")
            task.status = m.HumanTaskStatus.DECIDED
            task.decision = {"option": option, "output": dict(output or {})}
            task.decided_by = user_id
            task.decided_at = now
            task.reason = reason
            sr.decision_id = decision_id
            sr.human_touched = True
            self._audit(s, wf, run, sr, "task.decided", actor=actor, effect=option, decision=decision,
                        data={"task_id": str(task.id), "kind": task.kind.value}, reasoning=reason)  # fmt: skip
            try:
                self._apply_decision(
                    s, run, wf, step, sr, task, option, dict(output or {}), user_id
                )
            except cel.CelError as exc:
                # e.g. an override whose output lacks a field the step's `outputs` read
                raise TaskDecisionRefused(f"the output does not fit this step: {exc}") from None

    def _apply_decision(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        task: m.HumanTask,
        option: str,
        output: dict[str, Any],
        user_id: uuid.UUID,
    ) -> None:
        if task.action == ESCALATION_ACTION:
            self._apply_escalation(s, run, wf, step, sr, option, output, user_id)
            return
        if step.kind == "human":
            self._finish(
                s,
                run,
                wf,
                step,
                sr,
                {"decision": option, "decided_by": str(user_id), **output},
                human_touched=True,
            )
            return
        if task.kind in (m.HumanTaskKind.APPROVAL, m.HumanTaskKind.RESERVED_DECISION):
            if option == "approve":
                sr.input = {**(sr.input or {}), "approved_by": str(user_id)}
                self._set_status(s, wf, run, sr, m.StepStatus.READY)
            elif option == "reject":
                sr.error = {"type": "Rejected", "reason": task.reason}
                self._fail_step(s, run, wf, sr)
            else:
                raise TaskDecisionRefused(f"no handler for option {option!r}")
            return
        if option == "accept" and sr.output is not None:
            self._finish(s, run, wf, step, sr, sr.output, human_touched=True)
        elif option == "retry":
            sr.attempt = 0
            sr.next_attempt_at = None
            sr.error = None
            if step.kind == "connector" and step.uses:
                # The person fixed the data: the side effect may be attempted again (#44.4).
                ledger.reopen(s, ledger.idempotency_key(run.id, step.id, step.uses))
            self._set_status(s, wf, run, sr, m.StepStatus.READY)
        elif option == "override":
            self._override(s, run, wf, step, sr, output, user_id)
        elif option == "fail":
            self._fail_step(s, run, wf, sr)
        else:
            raise TaskDecisionRefused(f"no handler for option {option!r}")

    def _apply_escalation(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        option: str,
        output: dict[str, Any],
        user_id: uuid.UUID,
    ) -> None:
        """An SLA escalation: chase leaves the step waiting, override completes it, fail fails it."""
        if option == "chase":
            now = self.deps.clock()
            self.deps.notifier.notify("subject", "chase", {"run_id": str(run.id), "step": step.id})
            if step.sla is not None and step.sla.escalate_delay is not None:
                s.add(
                    m.Timer(
                        step_run_id=sr.id,
                        due_at=now + step.sla.escalate_delay,
                        purpose=m.TimerPurpose.SLA_ESCALATE,
                        payload={"to": step.sla.escalate_to},
                    )
                )
            self._audit(s, wf, run, sr, "sla.chased", data={"by": str(user_id)})
        elif option == "override":
            self._override(s, run, wf, step, sr, output, user_id)
        elif option == "fail":
            self._fail_step(s, run, wf, sr)
        else:
            raise TaskDecisionRefused(f"no handler for option {option!r}")

    def _override(
        self,
        s: Session,
        run: m.WorkflowRun,
        wf: WorkflowDefinition,
        step: Step,
        sr: m.StepRun,
        output: dict[str, Any],
        user_id: uuid.UUID,
    ) -> None:
        if step.kind == "connector" and step.uses:
            # Relay will not perform this side effect; close the row so it is not left pending.
            ledger.abandon(
                s,
                ledger.idempotency_key(run.id, step.id, step.uses),
                self.deps.clock(),
                note={"override_by": str(user_id)},
            )
        self._finish(s, run, wf, step, sr, output, human_touched=True, extra={"override": True})

    def _fail_step(
        self, s: Session, run: m.WorkflowRun, wf: WorkflowDefinition, sr: m.StepRun
    ) -> None:
        sr.lease_owner = None
        self._set_status(s, wf, run, sr, m.StepStatus.FAILED)
        self._advance(s, run, wf)

    def record_subject_action(
        self,
        run_id: uuid.UUID,
        action: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str,
    ) -> bool:
        """The data subject did `action` (uploaded documents, acknowledged policies, ...).
        Not a human touch: counted in `subject_actions`. Returns False if nothing was waiting."""
        with self.sessions.begin() as s:
            run = s.get(m.WorkflowRun, run_id, with_for_update=True)
            if run is None:
                return False
            wf = self.definitions.get(s, run.definition_name, run.definition_version)
            for sr in s.scalars(
                select(m.StepRun).where(
                    m.StepRun.run_id == run_id, m.StepRun.status == m.StepStatus.WAITING_TIMER
                )
            ):
                step = wf.step(sr.step_id)
                if step.for_subject_action == action:
                    sr.subject_actions += 1
                    self._audit(
                        s, wf, run, sr, "subject.action", data={"action": action, "actor": actor}
                    )
                    self._finish(s, run, wf, step, sr, dict(payload or {}))
                    return True
        return False

    def cancel_run(self, run_id: uuid.UUID, *, actor: str, reason: str) -> None:
        """Cancel a run: open tasks are cancelled, non-terminal steps stop."""
        with self.sessions.begin() as s:
            run = s.get(m.WorkflowRun, run_id, with_for_update=True)
            if run is None or run.status in (
                m.RunStatus.COMPLETED,
                m.RunStatus.FAILED,
                m.RunStatus.CANCELLED,
            ):
                return
            wf = self.definitions.get(s, run.definition_name, run.definition_version)
            run.status = m.RunStatus.CANCELLED
            run.completed_at = self.deps.clock()
            self._stop_remaining(s, run, wf, "run cancelled")
            self._audit(s, wf, run, None, "run.cancelled", data={"actor": actor}, reasoning=reason)

    def _stop_remaining(
        self, s: Session, run: m.WorkflowRun, wf: WorkflowDefinition, reason: str
    ) -> None:
        """The run has ended: skip every step that has not finished and close its open tasks.

        A RUNNING step is left alone: its worker is mid-call, and `_execute_inner` / `_relock`
        record the truthful outcome. Steps leased later see the ended run and skip themselves.
        """
        for sr in s.scalars(select(m.StepRun).where(m.StepRun.run_id == run.id).with_for_update()):
            if sr.status in TERMINAL or sr.status is m.StepStatus.RUNNING:
                continue
            self._set_status(s, wf, run, sr, m.StepStatus.SKIPPED, data={"reason": reason})
        s.execute(
            update(m.HumanTask)
            .where(m.HumanTask.run_id == run.id, m.HumanTask.status.in_(OPEN_TASK))
            .values(status=m.HumanTaskStatus.CANCELLED)
        )

    # ================================================================ timers and SLAs

    def _arm_sla(self, s: Session, sr: m.StepRun, step: Step, now: datetime) -> None:
        if step.sla is None:
            return
        for d in step.sla.remind_at:
            s.add(
                m.Timer(
                    step_run_id=sr.id,
                    due_at=now + parse_duration(d),
                    purpose=m.TimerPurpose.SLA_REMINDER,
                    payload={"after": d},
                )
            )
        esc = step.sla.escalate_delay
        if esc is not None:
            s.add(
                m.Timer(
                    step_run_id=sr.id,
                    due_at=now + esc,
                    purpose=m.TimerPurpose.SLA_ESCALATE,
                    payload={"to": step.sla.escalate_to},
                )
            )

    def _fire_timers(self) -> int:
        now = self.deps.clock()
        fired = 0
        with self.sessions.begin() as s:
            timers = list(
                s.scalars(
                    select(m.Timer)
                    .where(m.Timer.fired_at.is_(None), m.Timer.due_at <= now)
                    .order_by(m.Timer.due_at)
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            )
            for t in timers:
                t.fired_at = now
                fired += 1
                sr = s.get(m.StepRun, t.step_run_id, with_for_update=True)
                if sr is None or sr.status in TERMINAL:
                    continue
                run = s.get(m.WorkflowRun, sr.run_id, with_for_update=True)
                assert run is not None
                wf = self.definitions.get(s, run.definition_name, run.definition_version)
                step = wf.step(sr.step_id)
                if t.purpose is m.TimerPurpose.WAIT and sr.status is m.StepStatus.WAITING_TIMER:
                    self._finish(s, run, wf, step, sr, {"waited_until": t.due_at.isoformat()})
                elif t.purpose is m.TimerPurpose.SLA_REMINDER:
                    team = step.sla.escalate_to if step.sla and step.sla.escalate_to else "hr_ops"
                    self.deps.notifier.notify(
                        team,
                        "sla_reminder",
                        {
                            "run_id": str(run.id),
                            "step": step.id,
                            "after": (t.payload or {}).get("after"),
                        },
                    )
                    sla = dict(run.context.get("sla", {}))
                    sla["reminders"] = int(sla.get("reminders", 0)) + 1
                    run.context = {**run.context, "sla": sla}
                    self._audit(s, wf, run, sr, "sla.reminder", data={"team": team})
                elif t.purpose is m.TimerPurpose.SLA_ESCALATE:
                    role = (t.payload or {}).get("to") or "hr_ops"
                    s.add(
                        m.HumanTask(
                            run_id=run.id,
                            step_run_id=sr.id,
                            kind=m.HumanTaskKind.EXCEPTION,
                            action=ESCALATION_ACTION,
                            assignee_role=role,
                            options=list(ESCALATION_OPTIONS),
                            context_packet={"step": step.id, "reason": "SLA exceeded"},
                        )
                    )
                    self.deps.notifier.notify(
                        role, "sla_escalation", {"run_id": str(run.id), "step": step.id}
                    )
                    self._audit(s, wf, run, sr, "sla.escalated", data={"to": role})
        return fired

    # ================================================================ state

    def _set_status(
        self,
        s: Session,
        wf: WorkflowDefinition,
        run: m.WorkflowRun,
        sr: m.StepRun,
        status: m.StepStatus,
        *,
        decision: Decision | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        sr.status = status
        if status in TERMINAL:
            if sr.finished_at is None:
                sr.finished_at = self.deps.clock()
            s.flush()  # a task decided in this transaction must be DECIDED before the update
            s.execute(
                update(m.HumanTask)
                .where(m.HumanTask.step_run_id == sr.id, m.HumanTask.status.in_(OPEN_TASK))
                .values(status=m.HumanTaskStatus.CANCELLED)
            )
        self._audit(
            s, wf, run, sr, f"step.{status.value.lower()}", decision=decision, data=dict(data or {})
        )

    def _advance(self, s: Session, run: m.WorkflowRun, wf: WorkflowDefinition) -> None:
        """Promote PENDING steps whose needs are met; complete or fail the run."""
        s.execute(select(m.WorkflowRun.id).where(m.WorkflowRun.id == run.id).with_for_update())
        if run.status in (m.RunStatus.CANCELLED, m.RunStatus.COMPLETED, m.RunStatus.FAILED):
            return
        now = self.deps.clock()
        rows = {
            sr.step_id: sr for sr in s.scalars(select(m.StepRun).where(m.StepRun.run_id == run.id))
        }
        changed = True
        while changed:
            changed = False
            for step in wf.flat_steps():
                sr = rows[step.id]
                if sr.status is not m.StepStatus.PENDING:
                    continue
                if not all(rows[n].status in TERMINAL_OK for n in step.needs):
                    continue
                if step.when and not cel.evaluate_bool(step.when, self._activation(run, now)):
                    sr.finished_at = now
                    self._set_status(s, wf, run, sr, m.StepStatus.SKIPPED)
                else:
                    self._set_status(s, wf, run, sr, m.StepStatus.READY)
                    if step.kind == "wait" and step.for_subject_action:
                        sr.started_at = now  # subject wait window starts (excluded from cycle time)
                    self._arm_sla(s, sr, step, now)
                changed = True
        statuses = {sr.status for sr in rows.values()}
        if m.StepStatus.FAILED in statuses:
            run.status = m.RunStatus.FAILED
            run.completed_at = now
            self._stop_remaining(s, run, wf, "run failed")
            self._audit(s, wf, run, None, "run.failed")
        elif statuses <= TERMINAL_OK:
            run.status = m.RunStatus.COMPLETED
            run.completed_at = now
            cfg = get_config()
            allocate_infra(s, run.id, cfg.infra_costs, cfg.volumes)
            self._audit(s, wf, run, None, "run.completed")
            act = self._activation(run, now)
            for child in wf.on_complete:
                payload = {k: cel.evaluate_py(expr, act) for k, expr in child.payload.items()}
                child_id = self._start_run_in(
                    s, child.workflow, payload, actor=f"run:{run.id}", subject_id=run.subject_id,
                    parent_run_id=run.id,
                )  # fmt: skip
                self._audit(s, wf, run, None, "run.child_started",
                            data={"workflow": child.workflow, "child_run_id": str(child_id)})  # fmt: skip
        elif statuses & {m.StepStatus.READY, m.StepStatus.RUNNING, m.StepStatus.RETRY_SCHEDULED}:
            if run.status is not m.RunStatus.PAUSED or m.StepStatus.PAUSED not in statuses:
                run.status = m.RunStatus.RUNNING
        elif m.StepStatus.PAUSED in statuses:
            run.status = m.RunStatus.PAUSED
        else:
            run.status = m.RunStatus.WAITING

    # ================================================================ helpers

    def _activation(self, run: m.WorkflowRun, now: datetime) -> dict[str, Any]:
        return {
            "ctx": run.context.get("ctx", {}),
            "trigger": run.context.get("trigger", {}),
            "now": now.isoformat(),
            "run_id": str(run.id),
        }

    def _eval_map(
        self, exprs: Mapping[str, str], run: m.WorkflowRun, now: datetime
    ) -> dict[str, Any]:
        act = self._activation(run, now)
        return {k: cel.evaluate_py(e, act) for k, e in exprs.items()}

    def _audit(
        self,
        s: Session,
        wf: WorkflowDefinition,
        run: m.WorkflowRun,
        sr: m.StepRun | None,
        action: str,
        *,
        actor: Actor | None = None,
        effect: str | None = None,
        decision: Decision | None = None,
        data: Mapping[str, Any] | None = None,
        reasoning: str | None = None,
    ) -> None:
        who = actor or ENGINE_ACTOR
        append_event(
            s,
            AuditEntry(
                actor_type=m.ActorType(who.type),
                actor_id=who.id,
                on_behalf_of=DEFAULT_POOL if who.type != "human" else None,
                action=action,
                subject_type=run.subject_type,
                subject_id=run.subject_id,
                run_id=run.id,
                step_run_id=sr.id if sr else None,
                effect=effect or (decision.effect if decision else None),
                workflow_def_version=f"{wf.name}@{wf.version}",
                policy_version=decision.policy_version if decision else None,
                reasoning=reasoning or (decision.reason if decision else None),
                data={**({"step": sr.step_id} if sr else {}), **dict(data or {})},
            ),
        )
