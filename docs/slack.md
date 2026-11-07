# Slack app (PRD 16.2)

Relay's Slack app runs in Socket Mode, so the demo needs no public URL. It does three things:

- **DMs** go through the same intake path as web chat (`relay/api/chat.py`), so the answers and
  routing are the same as on the web, and only masked copies are stored. "I want to resign" starts
  the exit workflow for the signed-in employee; the manager then has to accept it.
- **Approval cards.** Every open human task gets one Block Kit card. It shows the title, the
  exception analyst's three lines, the key facts, the policy rule and one button per option. A task
  assigned to a named person who is on Slack goes to them as a DM; any other task goes to its role's
  channel (`#hr-ops`, `#it-fulfilment`, `#admin-fulfilment`, `#finance-ops`, `#approvals`). Each
  card is posted once; `slack_posts` remembers which. A click is decided as the Relay user mapped
  from the Slack id (`users.slack_user_id`), with every check the console applies. Reject, override,
  or going against the analyst's suggestion opens a modal that asks for a reason.
- **Manager digest.** `relay slack --digest` sends each manager on Slack a templated summary of
  their joiners' onboarding progress. Schedule it daily with cron or a CI job.

## Set it up (issue #21)

1. Create the app from `deploy/slack-manifest.yaml` (Create an app > From a manifest).
2. Generate an app-level token with `connections:write` and install the app to the workspace.
3. Put `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` in `.env` (never commit them).
4. Set `slack_user_id` on the demo users who should get DMs and cards (the seed gives managers
   `UTBO…` placeholders; replace them with real member ids from the workspace).
5. Run a worker and the app side by side:

```bash
relay worker
relay slack
```

A Slack id that maps to no Relay user gets a polite refusal and can do nothing.
