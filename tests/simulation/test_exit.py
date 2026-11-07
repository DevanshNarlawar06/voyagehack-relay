"""WP11 acceptance (issue #8): the five seed exits run end to end on exit.v1.yaml.

Real engine, real rules, the comms skill on the offline provider, real HttpConnectors to the mock
service in process, real Postgres. Only the clock is fake. The leavers' laptops, cards, accounts and
payroll records are created in the mocks first, as onboarding would have left them.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import date
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
from relay.db import models as m
from relay.rules.exit import add_working_days, completed_years, decide_last_day, notice_days
from relay.seeding.demo import checklist
from relay.seeding.exits import (
    ACCEPTANCE,
    SYNTHETIC_CTC_INR,
    ExitOutcome,
    ExitPlayer,
    prepare_leavers,
    provision,
)
from relay.seeding.generate import read
from relay.seeding.load import load
from tests.simulation.harness import FakeClock, clean

pytestmark = pytest.mark.db

ANNUAL_CTC = SYNTHETIC_CTC_INR


@pytest.fixture(scope="module")
def played(
    db_url: str,
) -> Iterator[tuple[dict[str, ExitOutcome], sessionmaker[Session], TestClient]]:
    sa: SAEngine = create_engine(db_url, pool_size=5)
    clean(sa)
    sessions = sessionmaker(bind=sa, expire_on_commit=False)
    with sessions.begin() as s:
        load(s, read())
    prepare_leavers(sessions)
    app = create_app(MockSettings(slow_seconds=0, timeout_seconds=0))
    transport = httpx.ASGITransport(app=app)
    connectors = load_connectors(transport=transport)
    asyncio.run(provision(connectors, list(ACCEPTANCE)))
    asyncio.run(provision(connectors, list(ACCEPTANCE)))  # twice: finds, never duplicates
    clock = FakeClock()
    engine = build_engine(sessions, connectors=connectors, clock=clock)
    player = ExitPlayer(
        engine, sessions, clock, HttpFaultControl("http://mocks", transport=transport)
    )
    outcomes = {o.code: o for o in player.play_all(read().exits).outcomes}
    yield outcomes, sessions, TestClient(app)
    prepare_leavers(sessions, with_ctc=False)  # leave the seed org as other tests expect it
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
        assert "employment.resignation.accept" in o.task_actions
        assert "payroll.fnf.signoff" in o.task_actions
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
    others = {a for c, o in outcomes.items() if c != "TBO1036" for a in o.task_actions}
    assert set(outcomes["TBO1036"].task_actions) - others  # the outage task, only for this leaver


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


def test_the_demo_checklist_sees_the_exits_and_names_what_is_missing(played) -> None:  # type: ignore[no-untyped-def]
    _, sessions, _ = played
    with sessions() as s:
        steps = {step.title: step for step in checklist(s)}
    assert len(steps) == 7
    assert steps["Extensibility"].ready and "5 completed exits" in steps["Extensibility"].detail
    assert steps["Governance"].ready  # no kill switch left active
    assert not steps["Dashboard"].ready  # this database has no onboarding runs
    assert not steps["Chaos"].ready and "relay simulate" in steps["Chaos"].detail
