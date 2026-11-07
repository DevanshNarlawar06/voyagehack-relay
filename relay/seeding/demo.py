"""The 5-minute demo (PRD 22), as a checklist read from the live database.

Each step says what to show and whether the data it needs is there, so the demo can be rehearsed
with one command (`relay demo`) and nothing is discovered missing on stage. Read-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relay.db import models as m


@dataclass(frozen=True)
class DemoStep:
    """One step of the script and whether it is ready."""

    at: str
    title: str
    show: str
    ready: bool
    detail: str


def _count(session: Session, query: object) -> int:
    return int(session.scalar(query) or 0)  # type: ignore[call-overload]


def checklist(session: Session) -> list[DemoStep]:
    """Every PRD 22 demo step, checked against what is in the database now."""
    completed = _count(
        session,
        select(func.count())
        .select_from(m.WorkflowRun)
        .where(
            m.WorkflowRun.definition_name == "onboarding",
            m.WorkflowRun.status == m.RunStatus.COMPLETED,
        ),
    )
    steps_done = _count(
        session,
        select(func.count())
        .select_from(m.StepRun)
        .where(m.StepRun.status == m.StepStatus.SUCCEEDED),
    )
    untouched = _count(
        session,
        select(func.count())
        .select_from(m.StepRun)
        .where(m.StepRun.status == m.StepStatus.SUCCEEDED, m.StepRun.human_touched.is_(False)),
    )
    rate = f"{untouched / steps_done:.1%}" if steps_done else "n/a"
    started_refs = select(m.WorkflowRun.subject_id).where(
        m.WorkflowRun.definition_name == "onboarding"
    )
    fresh_offers = _count(
        session,
        select(func.count()).select_from(m.Candidate).join(m.Offer, m.Offer.id == m.Candidate.offer_id)
        .where(m.Offer.approved_decision_id.is_not(None), m.Candidate.external_ref.not_in(started_refs)),
    )  # fmt: skip
    laptop = session.scalar(
        select(m.IdempotencyLedger)
        .where(m.IdempotencyLedger.connector == "assets", m.IdempotencyLedger.operation == "allocate_laptop")
        .order_by(m.IdempotencyLedger.created_at.desc())
        .limit(1)
    )  # fmt: skip
    analysed = session.scalar(
        select(m.HumanTask)
        .where(m.HumanTask.kind == m.HumanTaskKind.CONFLICT)
        .order_by(m.HumanTask.created_at.desc())
        .limit(1)
    )
    active_kills = _count(
        session, select(func.count()).select_from(m.KillSwitch).where(m.KillSwitch.active.is_(True))
    )
    model_events = _count(
        session,
        select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.model_id.is_not(None)),
    )
    exits = _count(
        session,
        select(func.count())
        .select_from(m.WorkflowRun)
        .where(
            m.WorkflowRun.definition_name == "exit", m.WorkflowRun.status == m.RunStatus.COMPLETED
        ),
    )
    answered = _count(
        session,
        select(func.count())
        .select_from(m.DataRequest)
        .where(m.DataRequest.status.in_(("completed", "rejected"))),
    )
    has_analysis = bool(analysed and (analysed.context_packet or {}).get("analysis"))
    return [
        DemoStep("0:00", "Dashboard", "Automation rate, cost per onboarding in INR, cycle time.",
                 completed > 0, f"{completed} completed onboardings, {rate} of steps untouched"),
        DemoStep("0:40", "Trigger a new hire", "Start one from an approved offer; watch the DAG fill.",
                 fresh_offers > 0,
                 f"{fresh_offers} approved offers not yet started" if fresh_offers
                 else "every approved offer has a run: `relay seed generate` and `relay seed load`"),
        DemoStep("1:40", "Chaos", "`docker compose kill worker` mid laptop order, then "
                 "`docker compose up -d worker`: exactly one laptop. Show the ledger row.",
                 laptop is not None,
                 f"latest laptop ledger row {laptop.key[:12]}… -> {laptop.external_id}" if laptop
                 else "no laptop order yet: run `relay simulate`"),
        DemoStep("2:20", "Conflict card", "A PAN conflict in HR's inbox or Slack with the analyst "
                 "summary; resolve it and the run continues.",
                 has_analysis, "latest conflict carries the analyst summary" if has_analysis
                 else "no analysed conflict yet: run `relay simulate --scenario pan_name_mismatch`"),
        DemoStep("3:00", "Governance", "Lower the laptop threshold in approvals.yaml, test, activate; "
                 "then press the kill switch and release it.",
                 active_kills == 0, "no kill switch active" if active_kills == 0
                 else f"{active_kills} kill switch(es) still active: release them first"),
        DemoStep("3:50", "Explainability", "Click an agent action: policy version, rule, model, "
                 "prompt version and the masked payload.",
                 model_events > 0, f"{model_events} audit events carry a model id"),
        DemoStep("4:30", "Extensibility", "The Exit PR: YAML, policies and rules only, zero "
                 "relay/core diff, already running. Then a DPDP erasure request from the Data "
                 "requests page, waiting for HR Admin.",
                 exits > 0, (f"{exits} completed exits" if exits
                 else "no exits yet: run `relay simulate --workflow exit`")
                 + f"; {answered} data requests answered"),
    ]  # fmt: skip
