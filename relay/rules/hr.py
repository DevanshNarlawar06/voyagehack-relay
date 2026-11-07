"""HR rule functions for onboarding (PRD 5.1 steps 1, 6, 7, 14, 18).

Rules are deterministic code, never prompts (PRD 6.8). The ones that write the system of record
open their own transaction and are idempotent, keyed by the candidate or employee, so a step that
is retried after a crash finds its earlier write instead of repeating it.
"""

from __future__ import annotations

import os
import re
import uuid
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from relay.core.engine.sealed import is_sealed, seal_value
from relay.core.errors import ConflictError, PermanentError
from relay.db import models as m
from relay.skills.document_extractor import open_sealed

BUDDY_MIN_TENURE_DAYS = 183  # "tenure > 6 months" (PRD 5.1 step 14)
CODE_PREFIX = "TBO"
CORPORATE_DOMAIN = "tbo.test"
CONSENT_NOTICE = "dpdp_onboarding_v1"
WEB_URL = os.environ.get("RELAY_WEB_URL", "http://localhost:3000")


def _norm(name: str) -> str:
    return " ".join(name.casefold().split())


def _today(now: str) -> date:
    return datetime.fromisoformat(now).date()


class HrRules:
    """Rule functions bound to a session factory. `functions()` is what the engine registers."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        """Bind the database."""
        self.sessions = sessions

    def functions(self) -> dict[str, Any]:
        """Name -> function, as workflows reference them in `uses`."""
        return {
            "rules.hr.create_prehire": self.create_prehire,
            "rules.hr.issue_upload_link": self.issue_upload_link,
            "rules.hr.collect_documents": self.collect_documents,
            "rules.hr.cross_check_identity": self.cross_check_identity,
            "rules.hr.allocate_employee_code": self.allocate_employee_code,
            "rules.hr.assign_buddy": self.assign_buddy,
            "rules.hr.readiness_gate": self.readiness_gate,
        }

    # ------------------------------------------------------------ step 1

    def create_prehire(self, candidate_ref: str, manager_code: str | None = None) -> dict[str, Any]:
        """Create the employee in `pre_hire` from the candidate and the approved offer.

        The approved CTC is copied verbatim into `employees.ctc_approved` and into the run as a
        sealed value, so payroll uses exactly what was approved (PRD 5.1 step 12).
        """
        with self.sessions.begin() as s:
            cand = s.scalar(select(m.Candidate).where(m.Candidate.external_ref == candidate_ref))
            if cand is None or cand.offer_id is None:
                raise PermanentError(f"no candidate with an offer: {candidate_ref}")
            offer = s.get(m.Offer, cand.offer_id)
            person = s.get(m.Person, cand.person_id)
            assert offer is not None and person is not None
            manager = (
                s.scalar(select(m.Employee).where(m.Employee.employee_code == manager_code))
                if manager_code
                else None
            )
            emp = s.scalar(select(m.Employee).where(m.Employee.person_id == person.id))
            if emp is None:
                emp = m.Employee(
                    person_id=person.id,
                    state=m.EmployeeState.PRE_HIRE,
                    grade=offer.grade,
                    role_title=offer.role_title,
                    department=offer.department,
                    location=offer.location,
                    joining_date=offer.joining_date,
                    manager_id=manager.id if manager else None,
                    ctc_approved=offer.ctc,
                )
                s.add(emp)
                s.flush()
                s.add(
                    m.LifecycleEvent(employee_id=emp.id, event_type="pre_hire.created", payload={})
                )
            manager_person = s.get(m.Person, manager.person_id) if manager else None
            manager_user = (
                s.scalar(select(m.User).where(m.User.person_id == manager.person_id))
                if manager
                else None
            )
            return {
                "employee_id": str(emp.id),
                "full_name": person.full_name,
                "personal_email": person.email,
                "grade": offer.grade,
                "role_title": offer.role_title,
                "department": offer.department,
                "location": offer.location,
                "joining_date": offer.joining_date.isoformat(),
                "manager_name": manager_person.full_name if manager_person else None,
                "manager_email": manager_person.email if manager_person else None,
                "manager_user_id": str(manager_user.id) if manager_user else None,
                "ctc": seal_value(int(offer.ctc["annual_inr"])),
                "ctc_matches_offer": True,
            }

    # ------------------------------------------------------------ step 3 (input)

    def issue_upload_link(self, employee_id: str, run_id: str, now: str) -> dict[str, Any]:
        """A signed, one-time upload link for the joiner, and the invitation that carries it.

        Both are sealed: the token is a credential, so ctx never holds it in plain text. The
        notify connector opens the message only for the outgoing call.
        """
        from relay.api.uploads import LINK_LIFETIME, issue_link

        with self.sessions.begin() as s:
            emp = s.get(m.Employee, uuid.UUID(employee_id))
            if emp is None:
                raise PermanentError(f"no employee {employee_id}")
            person = s.get(m.Person, emp.person_id)
            assert person is not None
            token = issue_link(s, emp.person_id, uuid.UUID(run_id), datetime.fromisoformat(now))
            url = f"{WEB_URL}/upload?token={token}"
            hours = int(LINK_LIFETIME.total_seconds() // 3600)
            message = (
                f"Welcome to TBO, {person.full_name}. Please upload your PAN, Aadhaar and a bank "
                f"proof here within {hours} hours: {url} . Notice {CONSENT_NOTICE}: we use these "
                "documents only to verify your identity (onboarding_verification) and to pay your "
                "salary (payroll_setup). You can withdraw consent at any time from the same page."
            )
            return {"token": seal_value(token), "message": seal_value(message)}

    # ------------------------------------------------------------ step 5 (input)

    def collect_documents(self, documents: Any) -> dict[str, Any]:
        """Gather what the joiner uploaded into one sealed bundle for `document_extractor`.

        The upload page stores each file's text-layer sidecar encrypted in `documents.extracted`
        and reports `[{id, category}]`; those rows are read here. An already sealed list is
        passed through. The bundle stays sealed in ctx (PRD 10.1).
        """
        if is_sealed(documents):
            return {"documents": documents, "count": None, "submitted_joining_date": None}
        bundle: list[dict[str, Any]] = []
        joining: str | None = None
        with self.sessions() as s:
            for ref in documents:
                row = s.get(m.Document, uuid.UUID(str(ref["id"])))
                if row is None:
                    raise PermanentError(f"no uploaded document {ref['id']}")
                sidecar = dict(row.extracted or {})
                # The signed acceptance form states the joining date the joiner agreed to.
                joining = str(sidecar.pop("joining_date", "")) or joining
                confidence = float(sidecar.pop("ocr_confidence", 1.0))
                if row.category is m.DocumentCategory.OTHER:
                    continue
                bundle.append(
                    {
                        "category": row.category.value,
                        "fields": sidecar,
                        "ocr_confidence": confidence,
                    }
                )
        return {
            "documents": seal_value(bundle),
            "count": len(bundle),
            "submitted_joining_date": joining,
        }

    # ------------------------------------------------------------ step 6

    def cross_check_identity(
        self,
        employee_id: str,
        sealed: Mapping[str, str],
        submitted_joining_date: str | None = None,
    ) -> dict[str, Any]:
        """Name, DOB and joining date across the documents, the record and the offer.

        A mismatch raises ConflictError, which opens a `conflict` task for HR with the fields that
        disagree. Raw values are opened here and never returned: only field names and verdicts.
        On success the verified identity is written to the (encrypted) person record.
        """
        with self.sessions.begin() as s:
            emp = s.get(m.Employee, uuid.UUID(employee_id))
            if emp is None:
                raise PermanentError(f"no employee {employee_id}")
            person = s.get(m.Person, emp.person_id)
            assert person is not None
            docs = {category: open_sealed(blob) for category, blob in sealed.items()}
            mismatches: list[str] = []
            for category, fields in docs.items():
                if "name" in fields and _norm(fields["name"]) != _norm(person.full_name):
                    mismatches.append(f"{category}.name")
            dobs = {fields["dob"] for fields in docs.values() if fields.get("dob")}
            if person.dob:
                dobs.add(person.dob)
            if len(dobs) > 1:
                mismatches.append("dob")
            pan = docs.get("pan", {}).get("pan")
            surname = person.full_name.split()[-1][:1].upper()
            if pan and pan[4:5] != surname:
                mismatches.append("pan.surname_initial")
            if (
                submitted_joining_date
                and emp.joining_date
                and date.fromisoformat(submitted_joining_date) != emp.joining_date
            ):
                mismatches.append("joining_date")
            if mismatches:
                raise ConflictError(
                    "Documents disagree with the record or the offer", fields=sorted(mismatches)
                )
            if pan:
                person.pan = pan
            bank = docs.get("bank_proof", {})
            if bank.get("account"):
                person.bank_account = bank["account"]
                person.ifsc = bank.get("ifsc")
            aadhaar = docs.get("aadhaar", {}).get("aadhaar")
            if aadhaar:
                person.aadhaar_last4 = re.sub(r"\D", "", aadhaar)[-4:]
            return {"verified": True, "checked": sorted(docs)}

    # ------------------------------------------------------------ step 7

    def allocate_employee_code(self, employee_id: str) -> dict[str, Any]:
        """Allocate the next employee code and move to `onboarding`. Repeat calls return the same."""
        with self.sessions.begin() as s:
            # Serialise allocation so two workers never hand out the same number.
            s.execute(text("SELECT pg_advisory_xact_lock(hashtext('relay.employee_code'))"))
            emp = s.get(m.Employee, uuid.UUID(employee_id))
            if emp is None:
                raise PermanentError(f"no employee {employee_id}")
            person = s.get(m.Person, emp.person_id)
            assert person is not None
            if emp.employee_code is None:
                codes = s.scalars(
                    select(m.Employee.employee_code).where(
                        m.Employee.employee_code.like(f"{CODE_PREFIX}%")
                    )
                )
                numbers = [int(c[len(CODE_PREFIX) :]) for c in codes if c and c[3:].isdigit()]
                emp.employee_code = f"{CODE_PREFIX}{max(numbers, default=999) + 1}"
                emp.state = m.EmployeeState.ONBOARDING
                s.add(
                    m.LifecycleEvent(
                        employee_id=emp.id,
                        event_type="employee_code.allocated",
                        payload={"employee_code": emp.employee_code},
                    )
                )
            first, *_, last = person.full_name.lower().split()
            return {
                "employee_code": emp.employee_code,
                "work_email": f"{first}.{last}.{emp.employee_code.lower()}@{CORPORATE_DOMAIN}",
            }

    # ------------------------------------------------------------ step 14

    def assign_buddy(self, employee_id: str, now: str) -> dict[str, Any]:
        """Same department, tenure over six months, lowest current buddy load (ties: oldest code)."""
        with self.sessions.begin() as s:
            emp = s.get(m.Employee, uuid.UUID(employee_id))
            if emp is None:
                raise PermanentError(f"no employee {employee_id}")
            existing = s.scalar(
                select(m.LifecycleEvent).where(
                    m.LifecycleEvent.employee_id == emp.id,
                    m.LifecycleEvent.event_type == "buddy.assigned",
                )
            )
            if existing is not None:
                buddy_id = existing.payload.get("buddy_employee_id")
                buddy = s.get(m.Employee, uuid.UUID(buddy_id)) if buddy_id else None
            else:
                cutoff = _today(now) - timedelta(days=BUDDY_MIN_TENURE_DAYS)
                pool = list(
                    s.scalars(
                        select(m.Employee).where(
                            m.Employee.department == emp.department,
                            m.Employee.state == m.EmployeeState.ACTIVE,
                            m.Employee.joining_date <= cutoff,
                            m.Employee.id != emp.id,
                        )
                    )
                )
                loads = Counter(
                    str(payload.get("buddy_employee_id"))
                    for payload in s.scalars(
                        select(m.LifecycleEvent.payload).where(
                            m.LifecycleEvent.event_type == "buddy.assigned"
                        )
                    )
                )
                pool.sort(key=lambda e: (loads.get(str(e.id), 0), e.employee_code or ""))
                buddy = pool[0] if pool else None
                s.add(
                    m.LifecycleEvent(
                        employee_id=emp.id,
                        event_type="buddy.assigned",
                        payload={"buddy_employee_id": str(buddy.id) if buddy else None},
                    )
                )
            person = s.get(m.Person, buddy.person_id) if buddy else None
            return {
                "buddy_employee_id": str(buddy.id) if buddy else None,
                "buddy_name": person.full_name if person else None,
                "buddy_email": person.email if person else None,
            }

    # ------------------------------------------------------------ step 18

    def readiness_gate(self, employee_id: str, probation_end: str) -> dict[str, Any]:
        """Every provisioning step has succeeded (the workflow's `needs` guarantee it): go active."""
        with self.sessions.begin() as s:
            emp = s.get(m.Employee, uuid.UUID(employee_id))
            if emp is None:
                raise PermanentError(f"no employee {employee_id}")
            if emp.state is not m.EmployeeState.ACTIVE:
                emp.state = m.EmployeeState.ACTIVE
                emp.probation_end = date.fromisoformat(probation_end)
                s.add(m.LifecycleEvent(employee_id=emp.id, event_type="activated", payload={}))
            return {"state": emp.state.value}
