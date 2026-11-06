"""Play the PRD 22 seed scenarios through the real onboarding workflow.

Each candidate's lifecycle runs on its own against the engine. The player acts as the joiner
(uploads, acknowledgements) and as the humans the scenario calls for, making the decision a person
would make (override a verified conflict, approve a high-spec laptop, retry after an outage, chase
a silent joiner). Every human decision goes through `Engine.decide_task`, so it is authorized and
audited like a real click.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relay.connectors.base import FaultControl
from relay.core.engine.engine import Engine, TriggerRefused
from relay.core.engine.sealed import seal_value
from relay.db import models as m
from relay.seeding.generate import Dataset

CONSENTED = ["onboarding_verification", "payroll_setup"]
# Where each scenario should end up (PRD 22). `refused` means the trigger was refused and audited.
EXPECTED_END: dict[str, str] = {
    "clean": "completed",
    "pan_name_mismatch": "completed",
    "joining_date_conflict": "completed",
    "blurry_aadhaar": "completed",
    "high_spec_laptop": "completed",
    "asset_outage": "completed",
    "ignores_document_request": "waiting",
    "unapproved_offer": "refused",
}
# The human decision each scenario is expected to need, by task kind.
EXPECTED_TASKS: dict[str, set[str]] = {
    "clean": set(),
    "pan_name_mismatch": {"conflict"},
    "joining_date_conflict": {"conflict"},
    "blurry_aadhaar": {"low_confidence"},
    "high_spec_laptop": {"reserved_decision", "approval"},
    "asset_outage": {"outage"},
    "ignores_document_request": {"exception"},
    "unapproved_offer": set(),
}


class Clock(Protocol):
    """A clock the player can move forward (tests use FakeClock)."""

    def advance(self, **kwargs: float) -> None:
        """Move time forward."""


@dataclass
class Outcome:
    candidate_ref: str
    scenario: str
    end_state: str
    run_id: uuid.UUID | None = None
    task_kinds: list[str] = field(default_factory=list)

    @property
    def as_expected(self) -> bool:
        """End state matches PRD 22, and so do the kinds of human task it needed."""
        kinds, expected = set(self.task_kinds), EXPECTED_TASKS[self.scenario]
        tasks_ok = (kinds <= expected and bool(kinds)) if expected else not kinds
        return self.end_state == EXPECTED_END[self.scenario] and tasks_ok


@dataclass
class SimulationReport:
    outcomes: list[Outcome]
    steps_total: int
    steps_untouched: int

    @property
    def automation_rate(self) -> float:
        """Share of finished steps with no human touch (PRD 3, objective 1)."""
        return self.steps_untouched / self.steps_total if self.steps_total else 0.0

    def summary(self) -> dict[str, Any]:
        """A JSON-friendly summary for the CLI and the demo."""
        return {
            "candidates": len(self.outcomes),
            "as_expected": sum(o.as_expected for o in self.outcomes),
            "end_states": dict(Counter(o.end_state for o in self.outcomes)),
            "automation_rate": round(self.automation_rate, 4),
            "unexpected": [
                {"candidate": o.candidate_ref, "scenario": o.scenario, "end": o.end_state,
                 "tasks": o.task_kinds}
                for o in self.outcomes
                if not o.as_expected
            ],
        }  # fmt: skip


def upload_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """What the upload page sends for a seed candidate: sealed documents plus consent."""
    doc = candidate["documents"]
    documents = [
        {
            "category": "pan",
            "fields": {"pan": doc["pan"], "name": doc.get("pan_name", doc["full_name"]),
                       "dob": doc["dob"]},
            "ocr_confidence": doc["ocr_confidence"],
        },
        {
            "category": "aadhaar",
            "fields": {"aadhaar": doc["aadhaar"], "name": doc["full_name"], "dob": doc["dob"]},
            "ocr_confidence": doc["ocr_confidence"],
        },
        {
            "category": "bank_proof",
            "fields": {"account": doc["bank_account"], "ifsc": doc["ifsc"]},
            "ocr_confidence": doc["ocr_confidence"],
        },
    ]  # fmt: skip
    return {
        "documents": seal_value(documents),
        "consented_purposes": list(CONSENTED),
        "joining_date": doc["joining_date"],
    }


class ScenarioPlayer:
    """Runs one candidate at a time through onboarding and records where it ended."""

    def __init__(
        self,
        engine: Engine,
        sessions: sessionmaker[Session],
        clock: Clock,
        faults: FaultControl | None = None,
    ) -> None:
        """Bind the engine, a movable clock and (for the outage scenario) the mock fault control."""
        self.engine, self.sessions, self.clock, self.faults = engine, sessions, clock, faults

    # ------------------------------------------------------------ helpers

    def _user_with_role(self, role: str) -> uuid.UUID:
        with self.sessions() as s:
            users = s.scalars(select(m.User)).all()
            for user in users:
                if role in user.roles:
                    return user.id
        raise LookupError(f"no demo user holds {role}")

    def _run(self, run_id: uuid.UUID) -> m.WorkflowRun:
        with self.sessions() as s:
            run = s.get(m.WorkflowRun, run_id)
            assert run is not None
            return run

    def _open_tasks(self, run_id: uuid.UUID) -> list[m.HumanTask]:
        with self.sessions() as s:
            return list(
                s.scalars(
                    select(m.HumanTask)
                    .where(
                        m.HumanTask.run_id == run_id,
                        m.HumanTask.status.in_((m.HumanTaskStatus.OPEN, m.HumanTaskStatus.CLAIMED)),
                    )
                    .order_by(m.HumanTask.created_at)
                )
            )

    def _waiting_for(self, run_id: uuid.UUID, step_id: str) -> bool:
        with self.sessions() as s:
            status = s.scalar(
                select(m.StepRun.status).where(
                    m.StepRun.run_id == run_id, m.StepRun.step_id == step_id
                )
            )
        return status is m.StepStatus.WAITING_TIMER

    def _settle(self) -> None:
        """Run until idle, letting scheduled retries come due."""
        for _ in range(8):
            self.engine.run_until_idle()
            self.clock.advance(minutes=10)
        self.engine.run_until_idle()

    # ------------------------------------------------------------ deciding like a person

    def _decide(self, task: m.HumanTask, kinds: list[str]) -> bool:
        """Make the decision PRD 22 expects. Returns False for a task it should leave open."""
        kinds.append(task.kind.value)
        decide: Callable[..., None] = self.engine.decide_task
        if task.action == "sla.escalation":
            decide(task.id, user_id=self._user_with_role(task.assignee_role), option="chase")
            return False  # the joiner still has to act
        if task.kind is m.HumanTaskKind.CONFLICT:
            decide(
                task.id,
                user_id=self._user_with_role("hr_ops"),
                option="override",
                reason="Checked with the joiner and the offer letter; record corrected",
                output={"verified": True, "resolved_by_hr": True},
            )
        elif task.kind is m.HumanTaskKind.LOW_CONFIDENCE:
            decide(task.id, user_id=self._user_with_role(task.assignee_role), option="accept")
        elif task.kind in (m.HumanTaskKind.APPROVAL, m.HumanTaskKind.RESERVED_DECISION):
            decide(task.id, user_id=self._user_with_role(task.assignee_role), option="approve")
        elif task.kind in (m.HumanTaskKind.OUTAGE, m.HumanTaskKind.EXCEPTION):
            if self.faults is not None:
                asyncio.run(self.faults.clear())  # the outage window ends
            decide(task.id, user_id=self._user_with_role("hr_ops"), option="retry")
        else:
            return False
        return True

    # ------------------------------------------------------------ one candidate

    def play(self, candidate: Mapping[str, Any]) -> Outcome:
        """Take one seed candidate from offer accepted to its end state."""
        ref, scenario = str(candidate["candidate_ref"]), str(candidate["scenario"])
        with self.sessions() as s:
            cand = s.scalar(select(m.Candidate).where(m.Candidate.external_ref == ref))
            offer = s.get(m.Offer, cand.offer_id) if cand and cand.offer_id else None
        payload = {
            "candidate_ref": ref,
            "manager_code": candidate["offer"]["manager_code"],
            "decision_task_id": str(offer.approved_decision_id)
            if offer and offer.approved_decision_id
            else None,
            "subject_label": candidate["full_name"],
        }
        try:
            run_id = self.engine.start_run(
                "onboarding", payload, actor="human:hr.ops@relay.test", subject_id=ref
            )
        except TriggerRefused:
            return Outcome(ref, scenario, "refused")
        if scenario == "asset_outage" and self.faults is not None:
            asyncio.run(self.faults.add("assets", "503"))
        kinds: list[str] = []
        self._settle()
        if candidate["documents"] is None:
            self.clock.advance(hours=73)  # the joiner never uploads: chase, then escalate
            self._settle()
            for task in self._open_tasks(run_id):
                self._decide(task, kinds)
            return Outcome(ref, scenario, self._run(run_id).status.value, run_id, kinds)
        self.clock.advance(hours=float(candidate["upload_after_hours"] or 1))
        self.engine.record_subject_action(
            run_id, "documents.uploaded", upload_payload(candidate), actor=f"subject:{ref}"
        )
        for _ in range(12):
            self._settle()
            progressed = False
            for task in self._open_tasks(run_id):
                progressed |= self._decide(task, kinds)
            if self._waiting_for(run_id, "policy_acknowledgements"):
                self.engine.record_subject_action(
                    run_id, "policies.acknowledged", {"policies": ["code_of_conduct", "posh", "it_acceptable_use"]},
                    actor=f"subject:{ref}",
                )  # fmt: skip
                progressed = True
            if self._run(run_id).status in (m.RunStatus.COMPLETED, m.RunStatus.FAILED):
                break
            if not progressed:
                break
        if self.faults is not None:
            asyncio.run(self.faults.clear())
        return Outcome(ref, scenario, self._run(run_id).status.value, run_id, kinds)

    # ------------------------------------------------------------ the whole seed

    def play_all(self, ds: Dataset) -> SimulationReport:
        """Every candidate in the dataset, then the step automation rate over all their runs."""
        outcomes = [self.play(c) for c in ds.candidates]
        run_ids = [o.run_id for o in outcomes if o.run_id is not None]
        with self.sessions() as s:
            steps = list(
                s.scalars(
                    select(m.StepRun).where(
                        m.StepRun.run_id.in_(run_ids),
                        m.StepRun.status == m.StepStatus.SUCCEEDED,
                    )
                )
            )
        return SimulationReport(
            outcomes, len(steps), sum(1 for sr in steps if not sr.human_touched)
        )
