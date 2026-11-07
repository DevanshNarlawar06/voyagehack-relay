# Microsoft Teams adapter (design)

V1 ships Slack only (PRD 2). This page shows how Teams would be added as an adapter, without
changing anything under `relay/core/` and without a second copy of any business logic.

## Why it is a small change

Everything the Slack app decides lives in `relay/slack/service.py` (`SlackService`), and none of
it is Slack-specific except the user lookup and the card format:

| What | Where it happens | Channel-specific? |
|---|---|---|
| Answer a DM | `on_message` → `relay/api/chat.py` (the web chat's intake path) | No |
| Decide a task | `click` / `decide` → `relay/api/task_decisions.py` (the console's path) | No |
| Which tasks need a card | `cards_to_post`, recorded in `slack_posts` | No |
| Which SLA reminders to post | `notices_to_post`, read from the audit log | No |
| Manager digest | `digest` / `digests` | No |
| Who is this person? | `user_for`: `users.slack_user_id` | **Yes** |
| What a card looks like | `relay/slack/cards.py` (Block Kit) | **Yes** |
| Sending and receiving | `relay/slack/app.py` (Bolt, Socket Mode) | **Yes** |

So a Teams adapter is three new pieces, and the channel-neutral middle is shared.

## The three new pieces

1. **Identity.** Add `users.teams_object_id` (the Azure AD object id), in one migration under
   `relay/db/`. A message or a click from an id that maps to no Relay user does nothing, exactly as
   with Slack.
2. **Cards.** `relay/teams/cards.py` renders the same content as Adaptive Cards:
   - a title, the exception analyst's three lines, the key facts and the policy rule;
   - one `Action.Submit` per option, carrying only the task id and option, so who may decide is
     checked again when the click arrives;
   - an `Input.Text` reason field, shown and required for reject, override, or going against the
     suggestion (the same rule as `task_decisions.reason_required`);
   - for accepting a resignation, an `Input.Date` release date and an `Input.Toggle` buyout, sent as
     the decision output that the API validates (issue #69).
3. **Transport.** `relay/teams/app.py` uses the Bot Framework SDK:
   - incoming messages and card submits go to the shared service methods;
   - cards, digests and SLA reminders are sent as proactive messages, using a stored conversation
     reference per user or channel;
   - Bot Framework requests are authenticated by validating the JWT that Teams sends. Unlike Slack's
     Socket Mode, Teams needs a public HTTPS endpoint (a tunnel is enough for a demo).

A `relay teams` CLI command, added in `relay/bootstrap.py` next to `relay slack`, starts it.

## Refactor to make first

Rename `SlackService` to a channel-neutral `ChatService` that takes an identity resolver
(`slack_user_id` or `teams_object_id`), and keep the posted-card and posted-notice tables keyed by
channel. The Slack app then becomes one adapter of two, and its tests keep passing unchanged.

## What does not change

The engine, the policy engine and its reserved-decision floor, the audit chain, connectors, skills
and every workflow. A decision made in Teams is recorded, authorized and audited exactly like one
made in Slack or the console, because it goes through the same `decide_task`.
