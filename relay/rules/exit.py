"""Exit rule functions (PRD 5.2), added through the extension points only.

Deterministic code, never prompts (PRD 6.8). Notice periods and the gratuity formula come from the
labour_rules pack; the rest from definitions/tables/exit.yaml. Rules that write the system of
record are idempotent, keyed by the employee, so a retried step finds its earlier write.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relay.config import REPO_ROOT
from relay.core.engine.sealed import seal_value
from relay.core.errors import PermanentError
from relay.db import models as m
from relay.rules.labour import gratuity_inr, next_working_day

TABLE = REPO_ROOT / "definitions" / "tables" / "exit.yaml"


@cache
def _table(path: Path = TABLE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle)
    return data


def notice_days(grade: str, on_probation: bool, tables: Mapping[str, Any]) -> int:
    """Notice period in days for a grade, or the probation notice while still on probation."""
    rule = tables["notice_days"]
    if on_probation:
        return int(rule["during_probation"])
    rows = rule["by_grade"]
    return int(rows.get(grade, rows["default"]))


def add_working_days(day: date, count: int, holidays: tuple[date, ...] = ()) -> date:
    """The date `count` working days after `day` (weekends and holidays skipped)."""
    current = day
    for _ in range(count):
        current = next_working_day(current + timedelta(days=1), holidays)
    return current


def decide_last_day(
    resignation: date,
    requested: date,
    notice: int,
    acceptance: Mapping[str, Any],
) -> dict[str, Any]:
    """Last working day from the notice period and what the manager agreed (pure).

    An agreed release date wins, then an agreed notice buyout (the requested day stands), else the
    later of the requested day and the end of notice. Never before the resignation itself.
    """
    notice_end = resignation + timedelta(days=notice)
    release = acceptance.get("release_date")
    if release:
        last, basis = date.fromisoformat(str(release)), "release_date_agreed"
    elif acceptance.get("buyout_agreed"):
        last, basis = requested, "notice_buyout_agreed"
    elif requested >= notice_end:
        last, basis = requested, "requested"
    else:
        last, basis = notice_end, "notice_period"
    last = max(last, resignation)
    return {
        "last_working_day": last.isoformat(),
        "notice_end": notice_end.isoformat(),
        "notice_shortfall_days": max(0, (notice_end - last).days),
        "basis": basis,
    }


def completed_years(joining: date, last: date) -> int:
    """Whole years of continuous service from joining to the last working day."""
    years = last.year - joining.year
    if (last.month, last.day) < (joining.month, joining.day):
        years -= 1
    return max(0, years)


class ExitRules:
    """Exit rule functions bound to the database and the active labour_rules tables."""

    def __init__(
        self, sessions: sessionmaker[Session], labour_tables: Callable[[], Mapping[str, Any]]
    ) -> None:
        """Bind the database and a reader for the labour_rules tables."""
        self.sessions = sessions
        self.labour_tables = labour_tables

    def functions(self) -> dict[str, Callable[..., Mapping[str, Any]]]:
        """Name -> function, as workflows reference them in `uses`."""
        return {
            "rules.exit.route_resignation": self.route_resignation,
            "rules.exit.compute_last_day": self.compute_last_day,
            "rules.exit.compute_fnf_inputs": self.compute_fnf_inputs,
            "rules.exit.issue_relieving_letter": self.issue_relieving_letter,
            "rules.exit.mark_exited": self.mark_exited,
        }

    def _employee(self, s: Session, employee_id: str) -> tuple[m.Employee, m.Person]:
        emp = s.get(m.Employee, uuid.UUID(employee_id))
        if emp is None:
            raise PermanentError(f"no employee {employee_id}")
        person = s.get(m.Person, emp.person_id)
        assert person is not None
        return emp, person

    def route_resignation(self, employee_code: str) -> dict[str, Any]:
        """Find the leaver and the manager who must accept the resignation (a reserved decision)."""
        with self.sessions() as s:
            emp = s.scalar(select(m.Employee).where(m.Employee.employee_code == employee_code))
            if emp is None or emp.state not in (m.EmployeeState.ACTIVE, m.EmployeeState.NOTICE):
                raise PermanentError(f"no active employee {employee_code}")
            person = s.get(m.Person, emp.person_id)
            assert person is not None
            manager = s.get(m.Employee, emp.manager_id) if emp.manager_id else None
            manager_person = s.get(m.Person, manager.person_id) if manager else None
            manager_user = (
                s.scalar(select(m.User).where(m.User.person_id == manager.person_id))
                if manager
                else None
            )
            if manager_user is None:
                raise PermanentError(f"{employee_code} has no manager who can accept")
            return {
                "employee_id": str(emp.id),
                "employee_code": employee_code,
                "full_name": person.full_name,
                "email": person.email,
                "grade": emp.grade or "default",
                "joining_date": emp.joining_date.isoformat() if emp.joining_date else None,
                "manager_user_id": str(manager_user.id),
                "manager_name": manager_person.full_name if manager_person else None,
            }

    def compute_last_day(
        self,
        employee_id: str,
        resignation_date: str,
        requested_last_day: str,
        acceptance: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Last working day and F&F due date; the employee moves to notice (idempotent)."""
        tables = self.labour_tables()
        resignation = date.fromisoformat(resignation_date)
        with self.sessions.begin() as s:
            emp, _ = self._employee(s, employee_id)
            on_probation = emp.probation_end is not None and resignation < emp.probation_end
            notice = notice_days(emp.grade or "default", on_probation, tables)
            terms = decide_last_day(
                resignation, date.fromisoformat(requested_last_day), notice, acceptance
            )
            last = date.fromisoformat(terms["last_working_day"])
            if emp.state is m.EmployeeState.ACTIVE:
                emp.state = m.EmployeeState.NOTICE
                s.add(
                    m.LifecycleEvent(
                        employee_id=emp.id, event_type="resignation_accepted", payload=terms
                    )
                )
            emp.last_working_day = last
        fnf = tables["full_and_final"]
        return {
            **terms,
            "notice_days": notice,
            "on_probation": on_probation,
            "fnf_settle_by": add_working_days(
                last, int(fnf["settle_within_working_days"])
            ).isoformat(),
            "sources": [tables["notice_days"]["source"], fnf["source"]],
        }

    def compute_fnf_inputs(
        self, employee_id: str, last_working_day: str, notice_shortfall_days: int
    ) -> dict[str, Any]:
        """F&F inputs only: service, gratuity and notice shortfall. Nothing is paid here.

        Amounts are sealed, since they derive from compensation. Payment waits for the reserved
        `payroll.fnf.signoff` by finance.
        """
        tables, exit_table = self.labour_tables(), _table()
        with self.sessions() as s:
            emp, _ = self._employee(s, employee_id)
            joining, ctc = emp.joining_date, emp.ctc_approved
        if joining is None:
            raise PermanentError(f"employee {employee_id} has no joining date")
        years = completed_years(joining, date.fromisoformat(last_working_day))
        annual = (
            int(ctc["annual_inr"]) if isinstance(ctc, Mapping) and "annual_inr" in ctc else None
        )
        if annual is None:
            rule = tables["gratuity"]
            gratuity: dict[str, Any] = {
                "eligible": years >= int(rule["min_continuous_service_years"]),
                "amount_inr": None,
                "note": "payroll to supply last drawn basic + DA",
                "paid_only_after": rule["paid_only_after"],
                "sources": [rule["source"]],
            }
        else:
            basic = int(annual * float(exit_table["basic_da_share_of_ctc"]) / 12)
            gratuity = gratuity_inr(basic, years, tables)
            gratuity["amount_inr"] = seal_value(gratuity["amount_inr"])
        return {
            "completed_years": years,
            "gratuity": gratuity,
            "notice_shortfall_days": int(notice_shortfall_days),
            "paid_only_after": "payroll.fnf.signoff",
            "sources": [*gratuity["sources"], exit_table["source"]],
        }

    def issue_relieving_letter(
        self, employee_id: str, last_working_day: str, signed_off_by: str
    ) -> dict[str, Any]:
        """The templated relieving letter, issued only after F&F sign-off, plus the survey invite."""
        with self.sessions() as s:
            emp, person = self._employee(s, employee_id)
            code, role = emp.employee_code, emp.role_title or "employee"
        letter = (
            f"This is to confirm that {person.full_name} ({code}) worked with TBO as {role} "
            f"until {last_working_day} and has been relieved of their duties. "
            "Full and final settlement has been signed off. We wish them well."
        )
        survey = (
            f"{person.full_name}, thank you for your time at TBO. Please tell us about it: "
            f"{_table()['survey_url']}"
        )
        return {"letter": letter, "survey_message": survey, "signed_off_by": signed_off_by}

    def mark_exited(self, employee_id: str) -> dict[str, Any]:
        """Close the record: the employee is exited (idempotent)."""
        with self.sessions.begin() as s:
            emp, _ = self._employee(s, employee_id)
            if emp.state is not m.EmployeeState.EXITED:
                emp.state = m.EmployeeState.EXITED
                s.add(m.LifecycleEvent(employee_id=emp.id, event_type="exited", payload={}))
            return {"state": emp.state.value}
