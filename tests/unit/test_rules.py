"""Rule functions (PRD 12.5): date math, decision tables, citations; sealed values; agent actors."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from relay.core.engine.sealed import is_sealed, seal_value, unseal
from relay.core.policy import ActionRequest, Actor, PolicyService
from relay.core.policy.evaluate import evaluate
from relay.rules.it import select_laptop
from relay.rules.labour import add_months, compute_onboarding_terms, gratuity_inr, next_working_day

POLICIES = Path(__file__).resolve().parents[2] / "definitions" / "policies"
TABLES = yaml.safe_load((POLICIES / "labour_rules.yaml").read_text(encoding="utf-8"))["tables"]
days = st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31))


@given(days, st.integers(min_value=0, max_value=36))
def test_add_months_lands_in_the_right_month_and_never_overflows(start: date, months: int) -> None:
    end = add_months(start, months)
    assert (end.year * 12 + end.month) - (start.year * 12 + start.month) == months
    assert end.day <= start.day
    assert (
        end.day == start.day or (end + timedelta(days=1)).month != end.month
    )  # clamped to month end


def test_add_months_clamps_month_ends_and_leap_years() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2027, 8, 31), 6) == date(2028, 2, 29)  # leap year
    assert add_months(date(2026, 10, 31), 6) == date(2027, 4, 30)


@given(days)
def test_next_working_day_is_a_weekday_within_three_days(day: date) -> None:
    nxt = next_working_day(day)
    assert nxt.weekday() < 5 and 0 <= (nxt - day).days <= 2


def test_next_working_day_skips_holidays() -> None:
    friday = date(2026, 11, 13)
    assert next_working_day(friday, [friday]) == date(2026, 11, 16)


@pytest.mark.parametrize(
    ("grade", "months", "notice"), [("G2", 6, 30), ("G4", 3, 90), ("X9", 6, 60)]
)
def test_terms_come_from_the_tables_and_cite_them(grade: str, months: int, notice: int) -> None:
    terms = compute_onboarding_terms("2026-11-14", grade, TABLES)  # a Saturday
    assert terms["probation_months"] == months
    assert terms["notice_days_after_probation"] == notice
    assert terms["first_working_day"] == "2026-11-16"
    assert terms["joining_date"] == "2026-11-14"  # the approved offer date is never changed
    assert terms["probation_end"] == add_months(date(2026, 11, 14), months).isoformat()
    assert TABLES["probation_months_by_grade"]["source"] in terms["sources"]


def test_changing_the_table_changes_the_answer() -> None:
    tables = {**TABLES, "probation_months_by_grade": {"source": "x", "rows": {"default": 2}}}
    assert compute_onboarding_terms("2026-11-16", "G1", tables)["probation_months"] == 2


def test_gratuity_needs_five_years_and_respects_the_ceiling() -> None:
    assert gratuity_inr(50000, 4, TABLES)["amount_inr"] == 0
    five = gratuity_inr(52000, 5, TABLES)
    assert five["eligible"] and five["amount_inr"] == 52000 * 15 * 5 // 26
    assert gratuity_inr(10_000_000, 30, TABLES)["amount_inr"] == TABLES["gratuity"]["ceiling_inr"]
    assert five["paid_only_after"] == "payroll.fnf.signoff"


def test_laptop_spec_by_grade_with_a_default() -> None:
    assert select_laptop("G5")["price_inr"] > 150000  # crosses the approvals threshold
    assert select_laptop("G2")["price_inr"] < 150000
    assert select_laptop("unknown") == select_laptop("G1")
    assert select_laptop("G3")["sources"]


def test_sealed_values_round_trip_and_hide_the_value() -> None:
    sealed = seal_value(1200000)
    assert is_sealed(sealed) and "1200000" not in str(sealed)
    nested = {"ctc_inr": sealed, "docs": [seal_value({"pan": "ABCPS1234K"})], "plain": 1}
    assert unseal(nested) == {"ctc_inr": 1200000, "docs": [{"pan": "ABCPS1234K"}], "plain": 1}


@pytest.mark.db
def test_an_agent_gets_only_principal_and_skill_allowlist(db_session) -> None:  # type: ignore[no-untyped-def]
    policy = PolicyService()
    agent = Actor(type="agent", id="skill:comms_drafter", allowed_actions=["comms.manager_brief"])

    def decide(action: str) -> str:
        req = ActionRequest(action=action, actor=agent, principal_roles=["system"])
        return evaluate(req, policy.context(db_session)).effect

    assert decide("comms.manager_brief") == "ALLOW"
    assert decide("payroll.employee.setup") == "DENY"  # the principal may, the skill may not
