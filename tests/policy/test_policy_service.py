"""DB-backed policy service: decisions persisted, hot activation, rollback, kill switches."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.core.policy import ActionRequest, Actor, PolicyService, PolicyTestFailure
from relay.core.policy.service import DEFAULT_POLICY_DIR
from relay.db import models as m

pytestmark = pytest.mark.db

APPROVALS = (DEFAULT_POLICY_DIR / "approvals.yaml").read_text(encoding="utf-8")
LAPTOP = ActionRequest(
    action="assets.laptop.allocate",
    actor=Actor(type="system", id="engine", roles=["system"]),
    attributes={"amount_inr": 90000},
    workflow="onboarding",
    connector="assets",
)


def _approvals(version: int, threshold: int) -> str:
    return APPROVALS.replace("version: 1", f"version: {version}").replace(
        "> 150000", f"> {threshold}"
    )


def test_authorize_persists_decision_and_audits(db_session: Session) -> None:
    events: list[Mapping[str, Any]] = []
    svc = PolicyService(audit=lambda _s, e: events.append(e))
    decision, row_id = svc.authorize(db_session, LAPTOP)
    row = db_session.get(m.Decision, row_id)
    assert decision.effect == "ALLOW"
    assert row is not None and row.effect is m.Effect.ALLOW
    assert row.policy_version == decision.policy_version
    assert events and events[0]["action"] == "policy.authorize:assets.laptop.allocate"


def test_activation_applies_to_next_call_without_restart(db_session: Session) -> None:
    svc = PolicyService()
    assert svc.authorize(db_session, LAPTOP)[0].effect == "ALLOW"
    start = time.monotonic()
    svc.activate(db_session, _approvals(2, 50000), actor="u-hradmin")
    decision, _ = svc.authorize(db_session, LAPTOP)
    assert time.monotonic() - start < 1.0
    assert decision.effect == "REQUIRE_HUMAN"
    assert "approvals@2" in decision.policy_version


def test_activation_refused_when_tests_fail(db_session: Session) -> None:
    svc = PolicyService()
    # Threshold 500000 breaks "expensive laptop needs approval" (210000).
    with pytest.raises(PolicyTestFailure, match="expensive laptop"):
        svc.activate(db_session, _approvals(2, 500000), actor="u-hradmin")
    assert db_session.scalar(select(m.PolicyVersion)) is None


def test_version_must_increase(db_session: Session) -> None:
    with pytest.raises(ValueError, match="must increase"):
        PolicyService().activate(db_session, APPROVALS, actor="u")


def test_rollback_reactivates_previous_version(db_session: Session) -> None:
    svc = PolicyService()
    svc.activate(db_session, _approvals(2, 50000), actor="u")
    svc.activate(db_session, _approvals(3, 100000), actor="u")
    assert "approvals@3" in svc.policies(db_session).version
    svc.rollback(db_session, "approvals", 2, actor="u")
    assert "approvals@2" in svc.policies(db_session).version


def test_kill_switch_takes_effect_within_one_second(db_session: Session) -> None:
    svc = PolicyService()
    assert svc.authorize(db_session, LAPTOP)[0].effect == "ALLOW"
    svc.set_kill_switch(db_session, "connector", "assets", active=True, actor="u-hradmin")
    start = time.monotonic()
    while svc.authorize(db_session, LAPTOP)[0].effect != "DENY":
        assert time.monotonic() - start < 1.0
    decision, _ = svc.authorize(db_session, LAPTOP)
    assert decision.is_kill_switch
    svc.set_kill_switch(db_session, "connector", "assets", active=False, actor="u-hradmin")
    assert svc.authorize(db_session, LAPTOP)[0].effect == "ALLOW"


def test_other_service_instance_sees_kill_switch_within_one_second(db_session: Session) -> None:
    writer, reader = PolicyService(), PolicyService()
    assert reader.authorize(db_session, LAPTOP)[0].effect == "ALLOW"
    writer.set_kill_switch(db_session, "global", "*", active=True, actor="u")
    time.sleep(0.6)
    assert reader.authorize(db_session, LAPTOP)[0].is_kill_switch


def test_activation_with_edited_test_cases(db_session: Session) -> None:
    """The live demo: HR lowers the threshold and updates its test cases in one go."""
    tests = (DEFAULT_POLICY_DIR / "tests" / "approvals.yaml").read_text(encoding="utf-8")
    edited_tests = tests.replace("amount_inr: 40000", "amount_inr: 20000")
    svc = PolicyService()
    with pytest.raises(PolicyTestFailure):
        svc.activate(db_session, _approvals(2, 30000), actor="u")
    svc.activate(db_session, _approvals(2, 30000), actor="u", draft_tests=edited_tests)
    assert svc.authorize(db_session, LAPTOP)[0].effect == "REQUIRE_HUMAN"
