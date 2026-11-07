"""The Slack app (PRD 16.2): Bolt in Socket Mode, so the demo needs no public URL.

This module only moves text and blocks between Slack and `SlackService`. Every decision a click
makes goes through the same checks as the console. Start it with `relay slack` once
SLACK_BOT_TOKEN and SLACK_APP_TOKEN are set (issue #21).
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from slack_bolt import App

from relay.slack import cards
from relay.slack.service import SlackService

log = logging.getLogger(__name__)
_DECIDE = re.compile(rf"^{cards.DECIDE_ACTION}:")


# The poster and digests take any object with Slack's `chat_postMessage` (WebClient, or a fake).
SlackClient = Any


def post_pending_cards(service: SlackService, client: SlackClient) -> int:
    """Post every open task's card once; returns how many were posted."""
    posted = 0
    for card in service.cards_to_post():
        try:
            response = client.chat_postMessage(
                channel=card.channel, text=card.text, blocks=card.blocks
            )
        except Exception:  # a Slack outage must not stop the loop; the card is retried next pass
            log.warning("could not post the card for task %s", card.task_id, exc_info=True)
            continue
        ts = response.get("ts") if hasattr(response, "get") else None
        service.mark_posted(card.task_id, card.channel, ts)
        posted += 1
    return posted


def post_pending_notices(service: SlackService, client: SlackClient) -> int:
    """Post each SLA reminder to its team's channel once; returns how many were posted."""
    posted = 0
    for notice in service.notices_to_post():
        try:
            response = client.chat_postMessage(channel=notice.channel, text=notice.text)
        except Exception:  # retried next pass
            log.warning("could not post SLA reminder %s", notice.audit_seq, exc_info=True)
            continue
        ts = response.get("ts") if hasattr(response, "get") else None
        service.mark_notice_posted(notice.audit_seq, notice.channel, ts)
        posted += 1
    return posted


def post_digests(service: SlackService, client: SlackClient) -> int:
    """Send each manager on Slack their onboarding digest; returns how many were sent."""
    sent = 0
    for slack_id, text in service.digests():
        client.chat_postMessage(channel=slack_id, text=text)
        sent += 1
    return sent


def reply_to_dm(service: SlackService, event: dict[str, Any]) -> str | None:
    """The reply to a direct message, or None for anything that is not a person's DM."""
    if event.get("channel_type") != "im" or event.get("bot_id") or event.get("subtype"):
        return None
    return service.on_message(str(event["user"]), str(event.get("text", "")))


def handle_click(service: SlackService, client: Any, body: dict[str, Any]) -> None:
    """A card button: open the reason modal if one is needed, else decide and update the card."""
    task_id, option = cards.parse_button_value(body["actions"][0]["value"])
    result = service.click(body["user"]["id"], task_id, option)
    if result.kind == "needs_input":
        modal = cards.MODALS[result.action]
        client.views_open(trigger_id=body["trigger_id"], view=modal(task_id, option, result.title))
        return
    if result.kind == "needs_reason":
        client.views_open(
            trigger_id=body["trigger_id"], view=cards.reason_modal(task_id, option, result.title)
        )
        return
    _answer(client, body, result.kind, result.text, result.title)


def reason_errors(view: dict[str, Any]) -> dict[str, str] | None:
    """Errors to show in the reason modal, or None when a reason was given."""
    reason = view["state"]["values"][cards.REASON_BLOCK][cards.REASON_INPUT]["value"] or ""
    return None if reason.strip() else {cards.REASON_BLOCK: "Please give a reason."}


def handle_reason(
    service: SlackService, client: Any, body: dict[str, Any], view: dict[str, Any]
) -> None:
    """The reason modal was submitted: decide with that reason and tell the person the outcome."""
    reason = view["state"]["values"][cards.REASON_BLOCK][cards.REASON_INPUT]["value"] or ""
    task_id, option = cards.parse_button_value(view["private_metadata"])
    result = service.decide(body["user"]["id"], task_id, option, reason)
    client.chat_postMessage(channel=body["user"]["id"], text=result.text)


def handle_input(
    service: SlackService, client: Any, body: dict[str, Any], view: dict[str, Any]
) -> None:
    """An input modal (accepting a resignation) was submitted: decide with its fields."""
    output, note = cards.read_resignation_modal(view)
    task_id, option = cards.parse_button_value(view["private_metadata"])
    result = service.decide(body["user"]["id"], task_id, option, note, output)
    client.chat_postMessage(channel=body["user"]["id"], text=result.text)


def build_app(service: SlackService, *, token: str, signing_secret: str | None = None) -> App:
    """Wire DMs, card buttons and the reason modal to the service."""
    app = App(token=token, signing_secret=signing_secret, token_verification_enabled=False)

    @app.event("message")
    def on_message(event: dict[str, Any], say: Any) -> None:
        reply = reply_to_dm(service, event)
        if reply is not None:
            say(reply)

    @app.action(_DECIDE)
    def on_click(ack: Any, body: dict[str, Any], client: Any) -> None:
        ack()
        handle_click(service, client, body)

    @app.view(cards.REASON_VIEW)
    def on_reason(ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any) -> None:
        errors = reason_errors(view)
        if errors:
            ack(response_action="errors", errors=errors)
            return
        ack()
        handle_reason(service, client, body, view)

    @app.view(cards.INPUT_VIEW)
    def on_input(ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any) -> None:
        ack()
        handle_input(service, client, body, view)

    return app


def _answer(client: Any, body: dict[str, Any], kind: str, text: str, title: str) -> None:
    """Replace the card's buttons after a decision; tell only the clicker about a refusal."""
    channel = body.get("channel", {}).get("id")
    ts = body.get("message", {}).get("ts")
    if kind == "decided" and channel and ts:
        client.chat_update(
            channel=channel, ts=ts, text=title,
            blocks=cards.decided_card_from_title(title, text),
        )  # fmt: skip
        return
    if channel:
        client.chat_postEphemeral(channel=channel, user=body["user"]["id"], text=text)


def run(
    service: SlackService, *, bot_token: str, app_token: str, poll_seconds: float = 5.0
) -> None:
    """Run the app in Socket Mode, posting new cards every `poll_seconds`."""
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = build_app(service, token=bot_token)
    stop = threading.Event()

    def poster() -> None:
        while not stop.wait(poll_seconds):
            try:
                post_pending_cards(service, app.client)
                post_pending_notices(service, app.client)
            except Exception:
                log.exception("card poster failed; retrying")

    threading.Thread(target=poster, name="relay-slack-cards", daemon=True).start()
    try:
        SocketModeHandler(app, app_token).start()  # type: ignore[no-untyped-call]
    finally:
        stop.set()
