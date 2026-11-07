"""Block Kit for Slack (PRD 16.2): approval cards, the reason modal and plain replies.

Pure functions over Relay rows, so they are tested without Slack. Values in a button carry only
the task id and option; who may decide is checked again when the click arrives.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from relay.db import models as m

DECIDE_ACTION = "relay_decide"
REASON_VIEW = "relay_reason"
REASON_BLOCK = "reason"
REASON_INPUT = "reason_text"
INPUT_VIEW = "relay_input"
RELEASE_BLOCK, RELEASE_INPUT = "release_date", "release_date_pick"
BUYOUT_BLOCK, BUYOUT_INPUT = "buyout", "buyout_check"
_MAX_FACTS = 8
_DANGER = ("reject", "override", "fail", "refer")


def _text(value: str, limit: int = 2900) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _title(task: m.HumanTask) -> str:
    packet = task.context_packet or {}
    title = packet.get("title")
    if isinstance(title, str) and title:
        return title
    return f"{task.kind.value.replace('_', ' ').capitalize()}: {task.action}"


def _facts(packet: Mapping[str, Any]) -> list[str]:
    facts = packet.get("facts")
    pairs: list[tuple[str, Any]] = list(facts.items()) if isinstance(facts, Mapping) else []
    pairs += [(k, v) for k, v in packet.items() if k.startswith("context_")]
    return [f"*{k}:* {v}" for k, v in pairs[:_MAX_FACTS] if v is not None]


def button_value(task_id: str, option: str) -> str:
    """What a button carries back: the task and the option, nothing else."""
    return json.dumps({"task_id": task_id, "option": option})


def parse_button_value(value: str) -> tuple[str, str]:
    """Read a button's value back into (task_id, option)."""
    data = json.loads(value)
    return str(data["task_id"]), str(data["option"])


def approval_card(task: m.HumanTask) -> list[dict[str, Any]]:
    """The one-click card: title, the analyst's 3 lines, key facts, the rule, one button per option."""
    packet = task.context_packet or {}
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": _text(_title(task), 150)}}
    ]
    analysis = packet.get("analysis")
    if isinstance(analysis, Mapping):
        lines = [
            str(analysis.get("summary") or ""),
            f"Likely cause: {analysis['likely_cause']}" if analysis.get("likely_cause") else "",
            f"Suggested: {analysis['suggested_option']}"
            if analysis.get("suggested_option")
            else "",
        ]
        text = "\n".join(line for line in lines if line)
        if text:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _text(text)}})
    elif packet.get("summary"):
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": _text(str(packet["summary"]))}}
        )
    facts = _facts(packet)
    if facts:
        blocks.append(
            {
                "type": "section",
                "fields": [{"type": "mrkdwn", "text": _text(f, 1900)} for f in facts],
            }
        )
    reason = packet.get("policy_reason")
    if reason:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": _text(f"Policy: {reason}", 1900)}],
            }
        )
    suggested = analysis.get("suggested_option") if isinstance(analysis, Mapping) else None
    buttons: list[dict[str, Any]] = []
    for option in task.options:
        button: dict[str, Any] = {
            "type": "button",
            "action_id": f"{DECIDE_ACTION}:{option}",
            "text": {"type": "plain_text", "text": option.replace("_", " ").capitalize()},
            "value": button_value(str(task.id), option),
        }
        if any(word in option for word in _DANGER):
            button["style"] = "danger"
        elif option == suggested or len(task.options) == 1:
            button["style"] = "primary"
        buttons.append(button)
    if buttons:
        blocks.append({"type": "actions", "block_id": f"task:{task.id}", "elements": buttons})
    return blocks


def reason_modal(task_id: str, option: str, title: str) -> dict[str, Any]:
    """The modal that asks why, for a reject, an override or going against the suggestion."""
    return {
        "type": "modal",
        "callback_id": REASON_VIEW,
        "private_metadata": button_value(task_id, option),
        "title": {"type": "plain_text", "text": "Give a reason"},
        "submit": {"type": "plain_text", "text": option.replace("_", " ").capitalize()},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": _text(title)}},
            {
                "type": "input",
                "block_id": REASON_BLOCK,
                "label": {"type": "plain_text", "text": "Reason"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": REASON_INPUT,
                    "multiline": True,
                },
            },
        ],
    }


def resignation_modal(task_id: str, option: str, title: str) -> dict[str, Any]:
    """Accepting a resignation: optionally agree an earlier release date or a notice buyout."""
    return {
        "type": "modal",
        "callback_id": INPUT_VIEW,
        "private_metadata": button_value(task_id, option),
        "title": {"type": "plain_text", "text": "Accept resignation"},
        "submit": {"type": "plain_text", "text": "Accept"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": _text(title)}},
            {
                "type": "input",
                "block_id": RELEASE_BLOCK,
                "optional": True,
                "label": {
                    "type": "plain_text",
                    "text": "Agreed release date (leave empty for notice)",
                },
                "element": {"type": "datepicker", "action_id": RELEASE_INPUT},
            },
            {
                "type": "input",
                "block_id": BUYOUT_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "Notice buyout"},
                "element": {
                    "type": "checkboxes",
                    "action_id": BUYOUT_INPUT,
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": "Buyout of the notice period agreed",
                            },
                            "value": "yes",
                        }
                    ],
                },
            },
            {
                "type": "input",
                "block_id": REASON_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "Note"},
                "element": {"type": "plain_text_input", "action_id": REASON_INPUT},
            },
        ],
    }


def read_resignation_modal(view: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """The decision output and note from a submitted resignation modal."""
    values = view["state"]["values"]
    release = values.get(RELEASE_BLOCK, {}).get(RELEASE_INPUT, {}).get("selected_date")
    ticked = values.get(BUYOUT_BLOCK, {}).get(BUYOUT_INPUT, {}).get("selected_options") or []
    note = values.get(REASON_BLOCK, {}).get(REASON_INPUT, {}).get("value") or ""
    output: dict[str, Any] = {}
    if release:
        output["release_date"] = release
    if ticked:
        output["buyout_agreed"] = True
    return output, note


MODALS = {"employment.resignation.accept": resignation_modal}


def decided_card_from_title(title: str, outcome: str) -> list[dict[str, Any]]:
    """The card after a decision: same title, the buttons replaced by what happened."""
    return [
        {"type": "header", "text": {"type": "plain_text", "text": _text(title, 150)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _text(outcome, 1900)}]},
    ]


def card_title(task: m.HumanTask) -> str:
    """The card's title, also used as the notification text."""
    return _title(task)
