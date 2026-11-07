"""Labour rule functions (PRD 12.5). Pure: every value comes from the `labour_rules` tables.

Each result carries `sources`, the `source` citation of every table it read, so an audit event
for the step shows which clause produced the number.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any


def add_months(start: date, months: int) -> date:
    """`start` plus `months` calendar months, clamped to the month's last day (31 Jan + 1 = 28/29 Feb)."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def next_working_day(day: date, holidays: Iterable[date] = ()) -> date:
    """`day` itself if it is a working day, else the next one (Saturday, Sunday, holidays skipped)."""
    closed = set(holidays)
    while day.weekday() >= 5 or day in closed:
        day += timedelta(days=1)
    return day


def _by_grade(table: Mapping[str, Any], grade: str) -> Any:
    rows = table.get("rows", table.get("by_grade", table))
    return rows.get(grade, rows["default"])


def compute_onboarding_terms(
    joining_date: str, grade: str, tables: Mapping[str, Any]
) -> dict[str, Any]:
    """Joining terms: first working day, probation end, notice terms and leave accrual start.

    The offer's joining date is never changed (it is part of the approved offer); if it falls on a
    weekend or holiday, `first_working_day` says when the joiner actually starts.
    """
    joining = date.fromisoformat(joining_date)
    holidays = [date.fromisoformat(d) for d in tables.get("holidays", {}).get("dates", [])]
    probation = tables["probation_months_by_grade"]
    notice = tables["notice_days"]
    leave = tables["leave"]
    months = int(_by_grade(probation, grade))
    first_day = next_working_day(joining, holidays)
    sources = [probation["source"], notice["source"], leave["source"]]
    if holidays:
        sources.append(tables["holidays"]["source"])
    return {
        "joining_date": joining.isoformat(),
        "first_working_day": first_day.isoformat(),
        "probation_months": months,
        "probation_end": add_months(joining, months).isoformat(),
        "notice_days_during_probation": int(notice["during_probation"]),
        "notice_days_after_probation": int(_by_grade(notice, grade)),
        "leave_accrual_starts": joining.isoformat(),
        "earned_leave_usable_during_probation": bool(leave["earned_leave_usable_during_probation"]),
        "leave_entitlement_days": dict(leave["annual_entitlement_days"]),
        "sources": sources,
    }


def gratuity_inr(
    last_drawn_basic_da_inr: int, completed_years: int, tables: Mapping[str, Any]
) -> dict[str, Any]:
    """Gratuity as an F&F input (Payment of Gratuity Act s.4). Never paid without F&F sign-off."""
    rule = tables["gratuity"]
    eligible = completed_years >= int(rule["min_continuous_service_years"])
    amount = (
        min(last_drawn_basic_da_inr * 15 * completed_years // 26, int(rule["ceiling_inr"]))
        if eligible
        else 0
    )
    return {
        "eligible": eligible,
        "amount_inr": amount,
        "paid_only_after": rule["paid_only_after"],
        "sources": [rule["source"]],
    }
