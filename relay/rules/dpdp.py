"""DPDP data request rules (PRD 14, issue #78), added through the extension points only.

A data principal asks to see, correct or erase their data. These rules check the request, build
the access package (sealed in ctx, opened only for delivery) and record the outcome. Correction
and erasure are decided by people in the workflow, never here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relay.core.engine.sealed import seal_value
from relay.core.errors import PermanentError
from relay.db import models as m

OPEN, IN_PROGRESS = "open", "in_progress"
OUTCOMES = {
    "exported": ("completed", "Your data export is ready. HR will share it with you securely."),
    "approve": ("completed", "Your correction was approved and your record has been updated."),
    "reject": (
        "rejected",
        "Your correction request was reviewed and not made. HR will explain why.",
    ),
    "erase": ("completed", "The data that is not required to be kept by law has been erased."),
    "refuse_retention": (
        "rejected",
        "Your erasure request was reviewed. The data must be kept by law for now; HR will explain.",
    ),
}
# Fields held encrypted: the export says they are held, never their values.
_SENSITIVE = ("phone", "dob", "pan", "bank_account")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class DpdpRules:
    """Data request rule functions bound to the database."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        """Bind the database."""
        self.sessions = sessions

    def functions(self) -> dict[str, Callable[..., Mapping[str, Any]]]:
        """Name -> function, as workflows reference them in `uses`."""
        return {
            "rules.dpdp.verify_requester": self.verify_requester,
            "rules.dpdp.build_export": self.build_export,
            "rules.dpdp.close_request": self.close_request,
        }

    @staticmethod
    def _request(s: Session, data_request_id: str) -> m.DataRequest:
        row = s.get(m.DataRequest, uuid.UUID(data_request_id))
        if row is None:
            raise PermanentError(f"no data request {data_request_id}")
        return row

    def verify_requester(self, data_request_id: str) -> dict[str, Any]:
        """The request exists, is open and names a person; it moves to in progress (idempotent)."""
        with self.sessions.begin() as s:
            row = self._request(s, data_request_id)
            if row.status not in (OPEN, IN_PROGRESS):
                raise PermanentError(f"data request {data_request_id} is already {row.status}")
            person = s.get(m.Person, row.person_id)
            if person is None:
                raise PermanentError("the request names no person")
            row.status = IN_PROGRESS
            return {
                "person_id": str(person.id),
                "type": row.type.value,
                "full_name": person.full_name,
                "email": person.email,
            }

    def build_export(self, data_request_id: str) -> dict[str, Any]:
        """The person's own record, sealed: what Relay holds about them and why (access right)."""
        with self.sessions() as s:
            row = self._request(s, data_request_id)
            person = s.get(m.Person, row.person_id)
            assert person is not None
            employee = s.scalar(select(m.Employee).where(m.Employee.person_id == person.id))
            consents = s.scalars(select(m.Consent).where(m.Consent.person_id == person.id)).all()
            documents = s.scalars(select(m.Document).where(m.Document.person_id == person.id)).all()
            lifecycle = (
                s.scalars(
                    select(m.LifecycleEvent)
                    .where(m.LifecycleEvent.employee_id == employee.id)
                    .order_by(m.LifecycleEvent.created_at)
                ).all()
                if employee
                else []
            )
            package: dict[str, Any] = {
                "person": {"full_name": person.full_name, "email": person.email},
                "identifiers_held": [f for f in _SENSITIVE if getattr(person, f, None)],
                "employment": {
                    "employee_code": employee.employee_code,
                    "state": employee.state.value,
                    "role_title": employee.role_title,
                    "department": employee.department,
                    "joining_date": employee.joining_date.isoformat()
                    if employee.joining_date
                    else None,
                }
                if employee
                else None,
                "consents": [
                    {
                        "purpose": c.purpose_id,
                        "notice": c.notice_version,
                        "withdrawn_at": _iso(c.withdrawn_at),
                    }
                    for c in consents
                ],
                "documents": [
                    {
                        "category": d.category.value,
                        "kept_until": d.retention_until.isoformat() if d.retention_until else None,
                    }
                    for d in documents
                ],
                "lifecycle": [{"event": e.event_type, "at": _iso(e.created_at)} for e in lifecycle],
            }
        summary = {
            "consents": len(package["consents"]),
            "documents": len(package["documents"]),
            "lifecycle_events": len(package["lifecycle"]),
        }
        return {"package": seal_value(package), "summary": summary}

    def close_request(self, data_request_id: str, outcome: str) -> dict[str, Any]:
        """Record how the request ended and the message the person gets (idempotent)."""
        if outcome not in OUTCOMES:
            raise PermanentError(f"unknown outcome {outcome}")
        status, message = OUTCOMES[outcome]
        with self.sessions.begin() as s:
            row = self._request(s, data_request_id)
            row.status = status
        return {"status": status, "outcome": outcome, "message": message}
