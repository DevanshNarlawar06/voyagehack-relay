"""Load the synthetic dataset into Postgres. Idempotent: running it twice changes nothing.

Approved offers get a recorded human decision (`hiring.offer.approve`, decided by the demo offer
approver), which the onboarding trigger requires (PRD 5.1 step 0). The `unapproved_offer`
scenario deliberately has none, so starting its onboarding is refused and audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.core.audit.writer import AuditEntry, append_event
from relay.db import models as m
from relay.seeding.generate import Dataset

OFFER_APPROVER_EMAIL = "offer.approver@relay.test"


@dataclass(frozen=True)
class LoadReport:
    users: int
    employees: int
    candidates: int
    approved_offers: int


def _person(s: Session, data: dict[str, object]) -> m.Person:
    email = str(data["email"])
    person = s.scalar(select(m.Person).where(m.Person.email == email))
    if person is None:
        aadhaar = str(data["aadhaar"])
        person = m.Person(
            full_name=str(data["full_name"]),
            email=email,
            phone=str(data["phone"]),
            dob=str(data["dob"]),
            pan=str(data["pan"]),
            aadhaar_last4=aadhaar[-4:],  # minimisation: only the last 4 digits are stored
            bank_account=str(data["bank_account"]),
            ifsc=str(data["ifsc"]),
        )
        s.add(person)
        s.flush()
    return person


def load(s: Session, ds: Dataset) -> LoadReport:
    """Upsert users, employees, candidates and offers."""
    users: dict[str, m.User] = {}
    for u in ds.users:
        user = s.scalar(select(m.User).where(m.User.email == u["email"]))
        if user is None:
            user = m.User(email=u["email"], display_name=u["display_name"], roles=list(u["roles"]))
            s.add(user)
        user.slack_user_id = u.get("slack_user_id")
        users[u["email"]] = user
    s.flush()

    employees: dict[str, m.Employee] = {}
    for e in ds.employees:
        emp = s.scalar(select(m.Employee).where(m.Employee.employee_code == e["employee_code"]))
        if emp is None:
            emp = m.Employee(
                person_id=_person(s, e).id,
                employee_code=e["employee_code"],
                state=m.EmployeeState.ACTIVE,
                grade=e["grade"],
                role_title=e["role_title"],
                department=e["department"],
                location=e["location"],
                joining_date=date.fromisoformat(e["joining_date"]),
            )
            s.add(emp)
        employees[e["employee_code"]] = emp
    s.flush()
    for e in ds.employees:
        if e["manager_code"]:
            employees[e["employee_code"]].manager_id = employees[e["manager_code"]].id
    for u in ds.users:
        if u.get("employee_code"):
            users[u["email"]].person_id = employees[u["employee_code"]].person_id

    approver = users[OFFER_APPROVER_EMAIL]
    approved = 0
    for c in ds.candidates:
        cand = s.scalar(select(m.Candidate).where(m.Candidate.external_ref == c["candidate_ref"]))
        if cand is None:
            cand = m.Candidate(
                person_id=_person(s, c).id, status="offer_accepted", external_ref=c["candidate_ref"]
            )
            s.add(cand)
            s.flush()
        if cand.offer_id is None:
            o = c["offer"]
            offer = m.Offer(
                candidate_id=cand.id,
                ctc={"annual_inr": o["ctc"]},
                grade=o["grade"],
                role_title=o["role_title"],
                department=o["department"],
                location=o["location"],
                joining_date=date.fromisoformat(o["joining_date"]),
            )
            s.add(offer)
            s.flush()
            cand.offer_id = offer.id
            if o["approved"]:
                task = m.HumanTask(
                    kind=m.HumanTaskKind.RESERVED_DECISION,
                    action="hiring.offer.approve",
                    assignee_role="approver_offer",
                    assignee_user_id=approver.id,
                    context_packet={"candidate_ref": c["candidate_ref"], "source": "seed"},
                    options=["approve", "reject"],
                    status=m.HumanTaskStatus.DECIDED,
                    decision={"option": "approve"},
                    decided_by=approver.id,
                    decided_at=datetime.now(UTC),
                    reason="Offer approved in hiring (synthetic seed)",
                )
                s.add(task)
                s.flush()
                offer.approved_decision_id = task.id
        if cand.offer_id is not None:
            offer_row = s.get(m.Offer, cand.offer_id)
            approved += bool(offer_row and offer_row.approved_decision_id)
    s.flush()
    report = LoadReport(len(users), len(employees), len(ds.candidates), approved)
    append_event(
        s,
        AuditEntry(
            actor_type=m.ActorType.SYSTEM,
            actor_id="seed",
            action="seed.loaded",
            data={
                "users": report.users,
                "employees": report.employees,
                "candidates": report.candidates,
            },
        ),
    )
    return report
