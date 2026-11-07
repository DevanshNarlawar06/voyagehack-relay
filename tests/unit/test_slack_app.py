"""The Slack app's handlers (PRD 16.2), with a fake service and client: no Slack, no network."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from relay.slack import cards
from relay.slack.app import (
    build_app,
    handle_click,
    handle_reason,
    post_pending_cards,
    post_pending_notices,
    reason_errors,
    reply_to_dm,
)
from relay.slack.service import CardPost, ClickResult, Notice


def _click_body(option: str = "approve") -> dict[str, Any]:
    return {
        "user": {"id": "U1"},
        "trigger_id": "trig",
        "channel": {"id": "C1"},
        "message": {"ts": "9.9"},
        "actions": [{"value": cards.button_value("t-1", option)}],
    }


def _view(reason: str | None) -> dict[str, Any]:
    return {
        "private_metadata": cards.button_value("t-1", "override"),
        "state": {"values": {cards.REASON_BLOCK: {cards.REASON_INPUT: {"value": reason}}}},
    }


def test_only_a_persons_dm_gets_a_reply() -> None:
    service = MagicMock()
    service.on_message.return_value = "hello"
    assert reply_to_dm(service, {"channel_type": "im", "user": "U1", "text": "hi"}) == "hello"
    service.on_message.assert_called_once_with("U1", "hi")
    assert reply_to_dm(service, {"channel_type": "channel", "user": "U1", "text": "hi"}) is None
    assert reply_to_dm(service, {"channel_type": "im", "bot_id": "B1", "text": "hi"}) is None
    assert reply_to_dm(service, {"channel_type": "im", "subtype": "message_changed"}) is None


def test_a_decided_click_replaces_the_cards_buttons() -> None:
    service, client = MagicMock(), MagicMock()
    service.click.return_value = ClickResult("decided", "*Approve* by Ira", "Laptop approval")
    handle_click(service, client, _click_body())
    service.click.assert_called_once_with("U1", "t-1", "approve")
    kwargs = client.chat_update.call_args.kwargs
    assert kwargs["channel"] == "C1" and kwargs["ts"] == "9.9"
    assert "Approve" in str(kwargs["blocks"]) and "actions" not in str(kwargs["blocks"])


def test_a_refused_click_is_told_only_to_the_clicker() -> None:
    service, client = MagicMock(), MagicMock()
    service.click.return_value = ClickResult("refused", "You can't decide this one.")
    handle_click(service, client, _click_body())
    client.chat_update.assert_not_called()
    client.chat_postEphemeral.assert_called_once_with(
        channel="C1", user="U1", text="You can't decide this one."
    )


def test_an_override_click_opens_the_reason_modal() -> None:
    service, client = MagicMock(), MagicMock()
    service.click.return_value = ClickResult("needs_reason", "", "PAN mismatch")
    handle_click(service, client, _click_body("override"))
    view = client.views_open.call_args.kwargs["view"]
    assert view["callback_id"] == cards.REASON_VIEW
    assert cards.parse_button_value(view["private_metadata"]) == ("t-1", "override")


def test_the_reason_modal_needs_a_reason_then_decides_with_it() -> None:
    assert reason_errors(_view("  ")) == {cards.REASON_BLOCK: "Please give a reason."}
    assert reason_errors(_view(None))
    assert reason_errors(_view("checked")) is None
    service, client = MagicMock(), MagicMock()
    service.decide.return_value = ClickResult("decided", "*Override* by Asha: checked", "PAN")
    handle_reason(service, client, {"user": {"id": "U1"}}, _view("checked"))
    service.decide.assert_called_once_with("U1", "t-1", "override", "checked")
    client.chat_postMessage.assert_called_once_with(
        channel="U1", text="*Override* by Asha: checked"
    )


def test_a_slack_outage_leaves_the_card_to_retry() -> None:
    service, client = MagicMock(), MagicMock()
    card = CardPost(task_id=MagicMock(), channel="#hr-ops", text="t", blocks=[])
    service.cards_to_post.return_value = [card, card]
    client.chat_postMessage.side_effect = [RuntimeError("down"), {"ts": "1.2"}]
    assert post_pending_cards(service, client) == 1
    service.mark_posted.assert_called_once_with(card.task_id, "#hr-ops", "1.2")


def test_the_app_builds_without_calling_slack() -> None:
    assert build_app(MagicMock(), token="xoxb-test") is not None


def test_an_sla_reminder_is_marked_only_once_it_is_posted() -> None:
    service, client = MagicMock(), MagicMock()
    notice = Notice(audit_seq=7, channel="#it-fulfilment", text="Reminder")
    service.notices_to_post.return_value = [notice, notice]
    client.chat_postMessage.side_effect = [RuntimeError("down"), {"ts": "3.4"}]
    assert post_pending_notices(service, client) == 1
    service.mark_notice_posted.assert_called_once_with(7, "#it-fulfilment", "3.4")
