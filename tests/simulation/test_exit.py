"""WP11 acceptance (issue #8): the five seed exits run end to end on exit.v1.yaml.

Real engine, real rules, the comms skill on the offline provider, real HttpConnectors to the mock
service in process, real Postgres. Only the clock is fake. The leavers' laptops, cards, accounts and
payroll records are created in the mocks first, as onboarding would have left them.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from mocks.app import MockSettings, create_app
from relay.bootstrap import build_engine
from relay.connectors.faults import HttpFaultControl
from relay.connectors.http import load_connectors
from relay.core.audit.verifier import verify_chain
from relay.core.engine.engine import Engine, TaskDecisionRefused
from relay.db import models as m
from relay.rules.exit import add_working_days, completed_years, decide_last_day, notice_days
from relay.seeding.generate import read
from relay.seeding.load import load
from tests.simulation.harness import FakeClock, clean

pytestmark = pytest.mark.db

ANNUAL_CTC = 1_800_000
# What each manager agrees when accepting (PRD 5.2: release date or buyout).
ACCEPTANCE: dict[str, dict[str, Any]] = {
    "TBO1019": {"release_date": "2026-12-18", "buyout_agreed": True},  # early release
    "TBO1005": {},  # standard notice
    "TBO1045": {},  # resigns on probation: shorter notice
    "TBO1025": {},  # never had a laptop: recovery is skipped
    "TBO1036": {},  # asset system down at recovery: outage, then retry
}
NO_LAPTOP = {"TBO1025"}
ON_PROBATION_UNTIL = {"TBO1045": date(2026, 12, 30)}
OUTAGE = {"TBO1036"}


@dataclass
class ExitOutcome:
    code: str
    run_id: uuid.UUID
    status: str
    task_kinds: list[str] = field(default_factory=list)
    refused_wrong_signer: bool = False


def _user(sessions: sessionmaker[Session], email: str) -> uuid.UUID:
    with sessions() as s:
        user = s.scalar(select(m.User).where(m.User.email == email))
        assert user is not None
        return user.id


def _role_user(sessions: sessionmaker[Session], role: str) -> uuid.UUID:
    with sessions() as s:
        for user in s.scalars(select(m.User).order_by(m.User.email)):
            if role in user.roles:
                return user.id
    raise LookupError(role)


def _reset_leavers(sessions: sessionmaker[Session], *, with_ctc: bool) -> None:
    with sessions.begin() as s:
        for code in ACCEPTANCE:
            emp = s.scalar(select(m.Employee).where(m.Employee.employee_code == code))
            assert emp is not None
            emp.state = m.EmployeeState.ACTIVE
            emp.last_working_day = None
            emp.probation_end = ON_PROBATION_UNTIL.get(code)
            emp.ctc_approved = {"annual_inr": ANNUAL_CTC} if with_ctc else None


def _provision(mocks: TestClient, code: str) -> None:
    """The records onboarding would have created for this employee."""
    ref = f"onboarding-{code}"
    creates = {
        "/directory/accounts": {"employee_code": code, "display_name": code, "email": f"{code}@tbo.test"},
        "/access/cards": {"employee_code": code, "photo_ref": f"photos/{code}.jpg"},
        "/payroll/employees": {"employee_code": code, "ctc_inr": ANNUAL_CTC},
    }  # fmt: skip
    if code not in NO_LAPTOP:
        creates["/assets/laptops"] = {"employee_code": code, "sku": "LAT-14-PLUS"}
    for path, body in creates.items():
        response = mocks.post(path, json={"client_reference": ref, **body})
        assert response.status_code in (200, 201), response.text


class ExitPlayer:
    """Plays one resignation: the manager accepts, time passes, finance signs off."""

    def __init__(
        self,
        engine: Engine,
        sessions: sessionmaker[Session],
        clock: FakeClock,
        faults: HttpFaultControl,
    ) -> None:
        self.engine, self.sessions, self.clock, self.faults = engine, sessions, clock, faults

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

    def play(self, exit_: dict[str, Any]) -> ExitOutcome:
        code = str(exit_["employee_code"])
        self.clock.now = datetime.fromisoformat(exit_["resignation_date"] + "T04:00:00+00:00")
        run_id = self.engine.start_run(
            "exit",
            {**exit_, "subject_label": code},
            actor="human:hr.ops@relay.test",
            subject_id=code,
        )
        outcome = ExitOutcome(code, run_id, "")
        finance = _user(self.sessions, "finance.approver@relay.test")
        hr_admin = _user(self.sessions, "hr.admin@relay.test")
        jumped = False
        for _ in range(12):
            self._settle()
            tasks = self._open(run_id)
            if not tasks:
                if self._status(run_id) == "waiting" and not jumped:
                    # Waiting for the last working day: let it come.
                    jumped = True
                    self.clock.now = datetime(2027, 2, 1, tzinfo=UTC)
                    if code in OUTAGE:
                        asyncio.run(self.faults.add("assets", "503"))
                    continue
                break
            for task in tasks:
                outcome.task_kinds.append(task.action)
                if task.action == "employment.resignation.accept":
                    assert task.assignee_user_id is not None  # the leaver's own manager
                    self.engine.decide_task(
                        task.id, user_id=task.assignee_user_id, option="accept",
                        output=ACCEPTANCE[code],
                    )  # fmt: skip
                elif task.action == "payroll.fnf.signoff":
                    try:
                        self.engine.decide_task(task.id, user_id=hr_admin, option="sign_off")
                    except TaskDecisionRefused:
                        outcome.refused_wrong_signer = True
                    self.engine.decide_task(task.id, user_id=finance, option="sign_off")
                else:  # the asset outage
                    asyncio.run(self.faults.clear())
                    role = _role_user(self.sessions, task.assignee_role)
                    self.engine.decide_task(task.id, user_id=role, option="retry")
        asyncio.run(self.faults.clear())
        outcome.status = self._status(run_id)
        return outcome


@pytest.fixture(scope="module")
def played(
    db_url: str,
) -> Iterator[tuple[dict[str, ExitOutcome], sessionmaker[Session], TestClient]]:
    sa: SAEngine = create_engine(db_url, pool_size=5)
    clean(sa)
    sessions = sessionmaker(bind=sa, expire_on_commit=False)
    with sessions.begin() as s:
        load(s, read())
    _reset_leavers(sessions, with_ctc=True)
    app = create_app(MockSettings(slow_seconds=0, timeout_seconds=0))
    transport = httpx.ASGITransport(app=app)
    mocks = TestClient(app)
    for code in ACCEPTANCE:
        _provision(mocks, code)
    clock = FakeClock()
    engine = build_engine(sessions, connectors=load_connectors(transport=transport), clock=clock)
    player = ExitPlayer(
        engine, sessions, clock, HttpFaultControl("http://mocks", transport=transport)
    )
    outcomes = {str(e["employee_code"]): player.play(e) for e in read().exits}
    yield outcomes, sessions, mocks
    _reset_leavers(sessions, with_ctc=False)  # leave the seed org as other tests expect it
    sa.dispose()


def _ctx(sessions: sessionmaker[Session], run_id: uuid.UUID) -> dict[str, Any]:
    with sessions() as s:
        run = s.get(m.WorkflowRun, run_id)
        assert run is not None
        ctx: dict[str, Any] = run.context["ctx"]
        return ctx


def test_every_seed_exit_completes(played) -> None:  # type: ignore[no-untyped-def]
    outcomes, sessions, _ = played
    assert {c: o.status for c, o in outcomes.items()} == dict.fromkeys(ACCEPTANCE, "completed")
    with sessions() as s:
        states = {
            e.employee_code: e.state
            for e in s.scalars(select(m.Employee).where(m.Employee.employee_code.in_(ACCEPTANCE)))
        }
    assert set(states.values()) == {m.EmployeeState.EXITED}


def test_both_reserved_decisions_were_taken_by_named_people(played) -> None:  # type: ignore[no-untyped-def]
    outcomes, sessions, _ = played
    for o in outcomes.values():
        assert "employment.resignation.accept" in o.task_kinds
        assert "payroll.fnf.signoff" in o.task_kinds
    with sessions() as s:
        tasks = list(
            s.scalars(select(m.HumanTask).where(m.HumanTask.action == "payroll.fnf.signoff"))
        )
        finance = s.scalar(select(m.User).where(m.User.email == "finance.approver@relay.test"))
    assert len(tasks) == 5 and finance is not None
    assert {t.decided_by for t in tasks} == {finance.id}


def test_hr_admin_cannot_sign_off_fnf(played) -> None:  # type: ignore[no-untyped-def]
    outcomes, _, _ = played
    assert all(o.refused_wrong_signer for o in outcomes.values())


def test_last_day_follows_what_the_manager_agreed_and_the_notice_table(played) -> None:  # type: ignore[no-untyped-def]
    outcomes, sessions, _ = played
    terms = {c: _ctx(sessions, o.run_id)["exit_terms"] for c, o in outcomes.items()}
    assert terms["TBO1019"]["last_working_day"] == "2026-12-18"
    assert terms["TBO1019"]["basis"] == "release_date_agreed"
    assert terms["TBO1019"]["notice_shortfall_days"] == 21
    assert terms["TBO1005"]["last_working_day"] == "2027-01-08"  # G3: 60 days
    assert terms["TBO1045"]["on_probation"] and terms["TBO1045"]["notice_days"] == 15
    assert terms["TBO1045"]["last_working_day"] == "2026-12-24"  # requested, after the notice
    assert all(t["sources"] for t in terms.values())


def test_access_is_revoked_only_after_the_last_day(played) -> None:  # type: ignore[no-untyped-def]
    outcomes, sessions, mocks = played
    for code, o in outcomes.items():
        last = _ctx(sessions, o.run_id)["exit_terms"]["last_working_day"]
        with sessions() as s:
            started = s.scalar(
                select(m.StepRun.started_at).where(
                    m.StepRun.run_id == o.run_id, m.StepRun.step_id == "revoke_access_card"
                )
            )
        assert started is not None and started.date() >= date.fromisoformat(last), code
    for path, status in (
        ("/access/cards", "revoked"),
        ("/directory/accounts", "disabled"),
        ("/payroll/employees", "exiting"),
        ("/assets/laptops", "recovered"),
    ):
        items = mocks.get(path).json()["items"]
        expected = 4 if path == "/assets/laptops" else 5
        assert [i["status"] for i in items] == [status] * expected, path


def test_a_leaver_without_a_laptop_skips_recovery(played) -> None:  # type: ignore[no-untyped-def]
    outcomes, sessions, _ = played
    with sessions() as s:
        status = s.scalar(
            select(m.StepRun.status).where(
                m.StepRun.run_id == outcomes["TBO1025"].run_id,
                m.StepRun.step_id == "recover_assets",
            )
        )
    assert status is m.StepStatus.SKIPPED


def test_an_asset_outage_at_recovery_goes_to_a_person_then_completes(played) -> None:  # type: ignore[no-untyped-def]
    outcomes, _, _ = played
    others = {a for c, o in outcomes.items() if c != "TBO1036" for a in o.task_kinds}
    assert set(outcomes["TBO1036"].task_kinds) - others  # the outage task, only for this leaver


def test_gratuity_is_an_input_sealed_in_ctx_and_never_plain(played) -> None:  # type: ignore[no-untyped-def]
    outcomes, sessions, _ = played
    fnf = _ctx(sessions, outcomes["TBO1019"].run_id)["fnf"]
    assert fnf["completed_years"] == 6 and fnf["gratuity"]["eligible"]
    assert fnf["gratuity"]["paid_only_after"] == "payroll.fnf.signoff"
    amount = (ANNUAL_CTC // 2 // 12) * 15 * 6 // 26
    with sessions() as s:
        blobs = " ".join(
            [str(r.context) for r in s.scalars(select(m.WorkflowRun))]
            + [str(sr.output) for sr in s.scalars(select(m.StepRun))]
        )
    assert str(amount) not in blobs and str(ANNUAL_CTC) not in blobs
    assert not _ctx(sessions, outcomes["TBO1005"].run_id)["fnf"]["gratuity"]["eligible"]


def test_relieving_letter_and_survey_reach_every_leaver(played) -> None:  # type: ignore[no-untyped-def]
    _, _, mocks = played
    bodies = [x["body"] for x in mocks.get("/notify/messages").json()["items"]]
    assert sum("has been relieved" in b for b in bodies) == 5
    assert sum("exit-survey" in b for b in bodies) == 5
    assert sum("Knowledge transfer" in b for b in bodies) == 5


def test_the_audit_chain_verifies_after_every_exit(played) -> None:  # type: ignore[no-untyped-def]
    _, sessions, _ = played
    with sessions() as s:
        assert verify_chain(s).ok


# ---------------------------------------------------------------- pure rule functions


def test_notice_days_by_grade_and_probation() -> None:
    tables = {"notice_days": {"during_probation": 15, "by_grade": {"G3": 60, "default": 45}}}
    assert notice_days("G3", False, tables) == 60
    assert notice_days("X", False, tables) == 45
    assert notice_days("G3", True, tables) == 15


def test_decide_last_day_prefers_release_then_buyout_then_notice() -> None:
    res, req = date(2026, 11, 9), date(2026, 12, 1)
    assert decide_last_day(res, req, 60, {})["last_working_day"] == "2027-01-08"
    assert decide_last_day(res, req, 60, {"buyout_agreed": True})["notice_shortfall_days"] == 38
    released = decide_last_day(res, req, 60, {"release_date": "2026-11-20"})
    assert released["basis"] == "release_date_agreed"
    assert decide_last_day(res, date(2026, 11, 1), 0, {})["last_working_day"] == "2026-11-09"


def test_working_days_and_completed_years() -> None:
    assert add_working_days(date(2026, 11, 13), 2) == date(2026, 11, 17)  # Fri -> Tue
    assert completed_years(date(2020, 7, 5), date(2026, 7, 4)) == 5
    assert completed_years(date(2020, 7, 5), date(2026, 7, 5)) == 6
