"""What a person sees on a task, from packets shaped exactly as the engine writes them (#90)."""

from __future__ import annotations

from typing import Any

import pytest

from relay.api.task_decisions import reason_required
from relay.api.task_packet import describe, suggested_option
from relay.core.engine.sealed import seal_value
from relay.db import models as m


def task(kind: m.HumanTaskKind, action: str, packet: dict[str, Any]) -> m.HumanTask:
    return m.HumanTask(
        kind=kind, action=action, assignee_role="hr_ops", options=["a", "b"], context_packet=packet
    )


ANALYSIS = {
    "summary": "cross_check needs a decision. Documents disagree.",
    "likely_cause": "The documents and the record disagree",
    "suggested_option": "a",
}


def test_analysis_reaches_the_person_and_is_marked_as_analysis() -> None:
    packet = {
        "step": "cross_check",
        "workflow": "onboarding@2",
        "reason": "Documents disagree",
        "fields": ["pan.name"],
        "analysis": ANALYSIS,
    }
    title, out = describe(task(m.HumanTaskKind.CONFLICT, "employee.verify", packet), [])
    assert title == "Conflict: employee verify"
    assert out.summary == ANALYSIS["summary"]
    assert out.from_analysis is True
    assert out.likely_cause == "The documents and the record disagree"
    assert out.suggested_option == "a"
    assert out.facts == {"Fields in question": "pan.name"}


def test_a_reason_the_analysis_left_out_stays_on_the_card() -> None:
    packet = {"reason": "HTTP 503", "analysis": {"summary": "Assets looks down."}}
    _, out = describe(task(m.HumanTaskKind.OUTAGE, "assets.laptop.lookup", packet), [])
    assert out.facts["Recorded reason"] == "HTTP 503"


def test_without_analysis_the_recorded_reason_is_the_summary() -> None:
    packet = {
        "reason": "Laptops above the limit need approval",
        "rule_ids": ["laptop_high_spend"],
        "attributes": {"amount_inr": 210000},
    }
    title, out = describe(task(m.HumanTaskKind.APPROVAL, "assets.laptop.allocate", packet), [])
    assert title == "Approval: assets laptop allocate"
    assert out.summary == "Laptops above the limit need approval"
    assert out.from_analysis is False
    assert out.rule == "laptop_high_spend"
    assert out.facts == {"Amount inr": "210000"}


def test_a_reserved_decision_shows_its_figures_with_names() -> None:
    packet = {
        "step": "fnf_signoff",
        "workflow": "exit@1",
        "context_0": 6,
        "context_1": True,
        "context_2": 21,
        "context_3": "2026-12-22",
        "policy_reason": "payroll.fnf.signoff is a reserved decision for approver_finance",
    }
    labels = [
        "ctx.fnf.completed_years",
        "ctx.fnf.gratuity.eligible",
        "ctx.fnf.notice_shortfall_days",
        "ctx.exit_terms.fnf_settle_by",
    ]
    _, out = describe(
        task(m.HumanTaskKind.RESERVED_DECISION, "payroll.fnf.signoff", packet), labels
    )
    assert out.summary == packet["policy_reason"]
    assert out.facts == {
        "Fnf completed years": "6",
        "Fnf gratuity eligible": "Yes",
        "Fnf notice shortfall days": "21",
        "Exit terms fnf settle by": "2026-12-22",
    }


def test_context_with_no_known_name_is_still_shown() -> None:
    packet = {"context_0": "relocation", "context_1": None}
    _, out = describe(task(m.HumanTaskKind.APPROVAL, "employment.x", packet), [])
    assert out.facts == {"Detail 1": "relocation"}


def test_sealed_values_never_reach_the_person() -> None:
    sealed = seal_value({"pan": "ABCDE1234F"})
    packet = {
        "reason": "confidence 0.41 below threshold 0.8",
        "context_0": sealed,
        "proposed": {
            "fields": {"pan": {"pan": "••••••234F", "name": "Joseph Khan"}},
            "sealed": {"pan": "v1:ciphertext"},
        },
    }
    _, out = describe(task(m.HumanTaskKind.LOW_CONFIDENCE, "documents.extract", packet), [])
    assert out.facts == {"Pan pan": "••••••234F", "Pan name": "Joseph Khan"}
    assert "ciphertext" not in out.model_dump_json()
    assert sealed["$sealed"] not in out.model_dump_json()


def test_a_packet_with_nothing_in_it_says_so() -> None:
    title, out = describe(task(m.HumanTaskKind.EXCEPTION, "sla.escalation", {}), [])
    assert title == "Exception: sla escalation"
    assert out.summary == "No summary was recorded for this task."
    assert out.facts == {}


def test_a_title_the_engine_gave_is_kept() -> None:
    title, out = describe(
        task(
            m.HumanTaskKind.APPROVAL, "x.y", {"title": "Offer", "summary": "S", "facts": {"A": 1}}
        ),
        [],
    )
    assert (title, out.summary, out.facts) == ("Offer", "S", {"A": "1"})


@pytest.mark.parametrize(
    ("packet", "expected"),
    [
        ({"analysis": {"suggested_option": "a"}}, "a"),
        ({"suggested_option": "b"}, "b"),
        ({"analysis": {"suggested_option": None}}, None),
        ({"analysis": "not a mapping"}, None),
        ({}, None),
    ],
)
def test_suggested_option_is_read_from_either_place(
    packet: dict[str, Any], expected: str | None
) -> None:
    assert suggested_option(packet) == expected


def test_going_against_the_analysts_suggestion_needs_a_reason() -> None:
    conflict = task(m.HumanTaskKind.CONFLICT, "employee.verify", {"analysis": ANALYSIS})
    assert reason_required(conflict, "b") is True
    assert reason_required(conflict, "a") is False
