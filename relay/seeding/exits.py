"""Play the five seed exits (PRD 22) through exit.v1 on the real engine.

The player acts as the people the scenario needs: the leaver's own manager accepts (agreeing a
release date where the scenario says so), finance signs off F&F, and someone in the routed role
retries after an asset outage. Every decision goes through `Engine.decide_task`, so it is
authorized and audited like a real click. Before playing, each leaver gets the laptop, card,
account and payroll record onboarding would have left, created through the real connectors.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relay.connectors.base import FaultControl
from relay.core.engine.engine import Engine, TaskDecisionRefused
from relay.core.engine.ports import ConnectorPort
from relay.db import models as m

SYNTHETIC_CTC_INR = 1_800_000  # synthetic, so the F&F inputs have a figure to work from
# What each seed leaver's scenario needs (PRD 22: 5 exits for V2).
ACCEPTANCE: dict[str, dict[str, Any]] = {
    "TBO1019": {"release_date": "2026-12-18"},  # early release agreed
    "TBO1005": {},  # standard notice
    "TBO1045": {},  # resigns on probation: shorter notice
    "TBO1025": {},  # never had a laptop: recovery is skipped
    "TBO1036": {},  # asset system down at recovery: outage, then retry
}
NO_LAPTOP = frozenset({"TBO1025"})
ON_PROBATION_UNTIL = {"TBO1045": date(2026, 12, 30)}
OUTAGE = frozenset({"TBO1036"})
AFTER_EVERY_LAST_DAY = datetime(2027, 2, 1, tzinfo=UTC)


class MovableClock(Protocol):
    """A clock the player can set and move forward (FakeClock, or the CLI's fast clock)."""

    now: datetime

    def advance(self, **kwargs: float) -> None:
        """Move time forward."""


@dataclass
class ExitOutcome:
    """Where one exit ended and the decisions it needed."""

    code: str
    run_id: uuid.UUID
    status: str = ""
    task_actions: list[str] = field(default_factory=list)
    refused_wrong_signer: bool = False


@dataclass
class ExitReport:
    """Every exit's outcome."""

    outcomes: list[ExitOutcome]

    def summary(self) -> dict[str, Any]:
        """A JSON-friendly summary for the CLI."""
        return {
            "exits": len(self.outcomes),
            "end_states": dict(Counter(o.status for o in self.outcomes)),
            "hr_admin_refused_for_fnf": all(o.refused_wrong_signer for o in self.outcomes),
            "not_completed": [o.code for o in self.outcomes if o.status != "completed"],
        }


def prepare_leavers(sessions: sessionmaker[Session], *, with_ctc: bool = True) -> None:
    """Put the seed leavers back to active, with the probation dates their scenarios need."""
    with sessions.begin() as s:
        for code in ACCEPTANCE:
            emp = s.scalar(select(m.Employee).where(m.Employee.employee_code == code))
            if emp is None:
                raise LookupError(f"seed employee {code} is missing: run `relay seed load`")
            emp.state = m.EmployeeState.ACTIVE
            emp.last_working_day = None
            emp.probation_end = ON_PROBATION_UNTIL.get(code)
            emp.ctc_approved = {"annual_inr": SYNTHETIC_CTC_INR} if with_ctc else None


async def provision(connectors: Mapping[str, ConnectorPort], codes: Sequence[str]) -> None:
    """Create what onboarding would have left for each leaver, through the real connectors.

    Keyed per employee, so running it twice finds the earlier records instead of duplicating.
    """
    for code in codes:
        ref = f"seed-onboarding-{code}"
        calls: list[tuple[str, str, dict[str, Any]]] = [
            ("directory", "create_account",
             {"employee_code": code, "display_name": code, "email": f"{code}@tbo.test"}),
            ("access", "issue_card", {"employee_code": code, "photo_ref": f"photos/{code}.jpg"}),
            ("payroll", "setup_employee", {"employee_code": code, "ctc_inr": SYNTHETIC_CTC_INR}),
        ]  # fmt: skip
        if code not in NO_LAPTOP:
            calls.append(
                ("assets", "allocate_laptop", {"employee_code": code, "sku": "LAT-14-PLUS"})
            )
        for system, op, payload in calls:
            key, connector = f"{ref}-{system}", connectors[system]
            # Some systems take no idempotency header: look for our reference first, as the
            # engine does before any retry.
            if await connector.reconcile(op, key, payload) is None:
                await connector.call(op, payload, idempotency_key=key)


class ExitPlayer:
    """Plays one resignation at a time: the manager accepts, time passes, finance signs off."""

    def __init__(
        self,
        engine: Engine,
        sessions: sessionmaker[Session],
        clock: MovableClock,
        faults: FaultControl | None = None,
    ) -> None:
        """Bind the engine, a movable clock and, for the outage scenario, the fault control."""
        self.engine, self.sessions, self.clock, self.faults = engine, sessions, clock, faults

    def _user(self, email: str) -> uuid.UUID:
        with self.sessions() as s:
            user = s.scalar(select(m.User).where(m.User.email == email))
            if user is None:
                raise LookupError(f"no demo user {email}")
            return user.id

    def _role_user(self, role: str) -> uuid.UUID:
        with self.sessions() as s:
            for user in s.scalars(select(m.User).order_by(m.User.email)):
                if role in user.roles:
                    return user.id
        raise LookupError(f"no demo user holds {role}")

    def _settle(self) -> None:
        for _ in range(8):
            self.engine.run_until_idle()
            self.clock.advance(minutes=10)
        self.engine.run_until_idle()

    def _open(self, run_id: uuid.UUID) -> list[m.HumanTask]:
        with self.sessions() as s:
            return list(
                s.scalars(
                    select(m.HumanTask)
                    .where(
                        m.HumanTask.run_id == run_id, m.HumanTask.status == m.HumanTaskStatus.OPEN
                    )
                    .order_by(m.HumanTask.created_at)
                )
            )

    def _status(self, run_id: uuid.UUID) -> str:
        with self.sessions() as s:
            run = s.get(m.WorkflowRun, run_id)
            assert run is not None
            return run.status.value

    def _fault(self, add: bool) -> None:
        if self.faults is not None:
            asyncio.run(self.faults.add("assets", "503") if add else self.faults.clear())

    def play(self, exit_: Mapping[str, Any]) -> ExitOutcome:
        """Take one seed resignation to its end state."""
        code = str(exit_["employee_code"])
        self.clock.now = datetime.fromisoformat(f"{exit_['resignation_date']}T04:00:00+00:00")
        run_id = self.engine.start_run(
            "exit", {**exit_, "subject_label": code}, actor="human:hr.ops@relay.test",
            subject_id=code,
        )  # fmt: skip
        outcome = ExitOutcome(code, run_id)
        finance = self._user("finance.approver@relay.test")
        hr_admin = self._user("hr.admin@relay.test")
        jumped = False
        for _ in range(12):
            self._settle()
            tasks = self._open(run_id)
            if not tasks:
                if self._status(run_id) == "waiting" and not jumped:
                    jumped = True  # waiting for the last working day: let it come
                    self.clock.now = AFTER_EVERY_LAST_DAY
                    if code in OUTAGE:
                        self._fault(add=True)
                    continue
                break
            for task in tasks:
                outcome.task_actions.append(task.action)
                if task.action == "employment.resignation.accept":
                    assert task.assignee_user_id is not None  # the leaver's own manager
                    self.engine.decide_task(
                        task.id, user_id=task.assignee_user_id, option="accept",
                        output=ACCEPTANCE.get(code, {}),
                    )  # fmt: skip
                elif task.action == "payroll.fnf.signoff":
                    try:  # HR admin holds "*", and still may not sign off F&F
                        self.engine.decide_task(task.id, user_id=hr_admin, option="sign_off")
                    except TaskDecisionRefused:
                        outcome.refused_wrong_signer = True
                    self.engine.decide_task(task.id, user_id=finance, option="sign_off")
                else:  # the asset outage: the window ends, someone in the role retries
                    self._fault(add=False)
                    self.engine.decide_task(
                        task.id, user_id=self._role_user(task.assignee_role), option="retry"
                    )
        self._fault(add=False)
        outcome.status = self._status(run_id)
        return outcome

    def play_all(self, exits: Sequence[Mapping[str, Any]]) -> ExitReport:
        """Every exit in the dataset."""
        return ExitReport([self.play(e) for e in exits])
