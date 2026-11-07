"""Slack Block Kit (PRD 16.2): approval cards, the reason modal and button values."""

from __future__ import annotations

import uuid

from relay.db import models as m
from relay.slack import cards


def _task(**packet: object) -> m.HumanTask:
    return m.HumanTask(
        id=uuid.uuid4(),
        kind=m.HumanTaskKind.CONFLICT,
        action="employee.verify",
        assignee_role="hr_ops",
        options=["override", "fail", "retry"],
        context_packet=dict(packet),
    )


def test_card_has_title_analysis_facts_rule_and_one_button_per_option() -> None:
    task = _task(
        title="PAN name does not match the offer",
        analysis={"summary": "Names differ by a middle name.", "likely_cause": "Initials",
                  "suggested_option": "override"},
        facts={"Offer name": "Asha R", "PAN name": "Asha Rao"},
        policy_reason="identity cross-check failed",
    )  # fmt: skip
    blocks = cards.approval_card(task)
    text = str(blocks)
    assert blocks[0]["text"]["text"] == "PAN name does not match the offer"
    assert "Names differ by a middle name." in text and "Likely cause: Initials" in text
    assert "*PAN name:* Asha Rao" in text and "Policy: identity cross-check failed" in text
    buttons = blocks[-1]["elements"]
    assert [cards.parse_button_value(b["value"]) for b in buttons] == [
        (str(task.id), "override"), (str(task.id), "fail"), (str(task.id), "retry"),
    ]  # fmt: skip
    assert buttons[0]["style"] == "danger" and buttons[1]["style"] == "danger"
    assert all(b["action_id"].startswith(cards.DECIDE_ACTION + ":") for b in buttons)


def test_card_without_a_title_or_analysis_still_renders() -> None:
    task = _task(context_0="2026-11-09")
    blocks = cards.approval_card(task)
    assert blocks[0]["text"]["text"] == "Conflict: employee.verify"
    assert "*context_0:* 2026-11-09" in str(blocks)


def test_long_text_is_cut_to_slack_limits() -> None:
    blocks = cards.approval_card(_task(title="x" * 400, summary="y" * 5000))
    assert len(blocks[0]["text"]["text"]) <= 150
    assert len(blocks[1]["text"]["text"]) <= 2900


def test_reason_modal_carries_the_task_and_option_back() -> None:
    view = cards.reason_modal("t-1", "override", "Conflict")
    assert view["callback_id"] == cards.REASON_VIEW
    assert cards.parse_button_value(view["private_metadata"]) == ("t-1", "override")
    assert view["blocks"][1]["block_id"] == cards.REASON_BLOCK


def test_decided_card_replaces_the_buttons() -> None:
    blocks = cards.decided_card_from_title("Conflict", "*Override* by Asha")
    assert all(b["type"] != "actions" for b in blocks)
    assert "Override" in str(blocks)
