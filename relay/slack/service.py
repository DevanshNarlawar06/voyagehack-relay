"""What the Slack app does, without Slack (PRD 16.2).

Messages go through the same intake path as web chat, and clicks through the same task decision
path as the console, with the Slack user mapped to a Relay user. A Slack id that maps to nobody
can do nothing. The Bolt wiring in `relay.slack.app` only moves text and blocks in and out.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker

from relay.api import chat
from relay.api import task_decisions as decisions
from relay.api.deps import ApiServices, Authorizer
from relay.db import models as m
from relay.models.pii_gateway import PiiGateway
from relay.slack import cards

# Where a task goes when it is not assigned to a named person who is on Slack.
DEFAULT_CHANNELS: dict[str, str] = {
    "hr_ops": "#hr-ops",
    "hr_admin": "#hr-ops",
    "it_ops": "#it-fulfilment",
    "admin_ops": "#admin-fulfilment",
    "finance_ops": "#finance-ops",
    "approver_offer": "#approvals",
    "approver_it_spend": "#approvals",
    "approver_finance": "#approvals",
}
_OPEN = (m.HumanTaskStatus.OPEN, m.HumanTaskStatus.CLAIMED)
UNKNOWN_USER = "I can't match your Slack account to anyone in Relay, so I can't help here yet."


@dataclass(frozen=True)
class CardPost:
    """An approval card waiting to be posted."""

    task_id: uuid.UUID
    channel: str
    text: str
    blocks: list[dict[str, object]]


@dataclass(frozen=True)
class ClickResult:
    """What a button click led to: decided, a reason is needed first, or refused."""

    kind: Literal["decided", "needs_reason", "refused"]
    text: str
    title: str = ""


class SlackService:
    """Relay's side of the Slack app."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        services: ApiServices,
        gateway: PiiGateway | None = None,
        channels: Mapping[str, str] | None = None,
    ) -> None:
        """Bind the database, the API's services (policy, engine, skills) and role channels."""
        self.sessions, self.services = sessions, services
        self.gateway = gateway or PiiGateway()
        self.channels = dict(DEFAULT_CHANNELS if channels is None else channels)

    # ------------------------------------------------------------ people

    @staticmethod
    def user_for(session: Session, slack_user_id: str) -> m.User | None:
        """The Relay user behind a Slack id, or None."""
        return session.scalar(select(m.User).where(m.User.slack_user_id == slack_user_id))

    # ------------------------------------------------------------ DMs

    def on_message(self, slack_user_id: str, text: str) -> str:
        """Answer a DM through the web chat's intake path, keeping only masked copies."""
        if self.services.intake is None or self.services.helpdesk is None:
            return "Chat is not available right now."
        with self.sessions() as s:
            user = self.user_for(s, slack_user_id)
            if user is None:
                return UNKNOWN_USER
            reply = chat.handle_message(
                s,
                user,
                text,
                self.services.intake,
                self.services.helpdesk,
                self.gateway,
                channel="slack",
                engine=self.services.engine,
                policy=self.services.policy,
            )
            s.commit()
        lines = [reply.reply]
        for citation in reply.citations:
            source = citation.get("source") or citation.get("title")
            if source:
                lines.append(f"> Source: {source}")
        return "\n".join(lines)

    # ------------------------------------------------------------ clicks

    def click(self, slack_user_id: str, task_id: str, option: str) -> ClickResult:
        """A button press: decide now, or ask for a reason first when one is required."""
        with self.sessions() as s:
            user = self.user_for(s, slack_user_id)
            task = s.get(m.HumanTask, uuid.UUID(task_id))
            if user is None:
                return ClickResult("refused", UNKNOWN_USER)
            if task is None:
                return ClickResult("refused", "That task no longer exists.")
            title = cards.card_title(task)
            if decisions.reason_required(task, option):
                return ClickResult("needs_reason", "", title)
        return self.decide(slack_user_id, task_id, option, "")

    def decide(self, slack_user_id: str, task_id: str, option: str, reason: str) -> ClickResult:
        """Record the decision as the mapped user, with every check the console applies."""
        with self.sessions() as s:
            user = self.user_for(s, slack_user_id)
            if user is None:
                return ClickResult("refused", UNKNOWN_USER)
            auth = Authorizer(session=s, user=user, policy=self.services.policy)
            try:
                task = decisions.decide_task(
                    auth, self.services.engine, uuid.UUID(task_id), option, reason
                )
            except decisions.TaskNotFound:
                return ClickResult("refused", "That task no longer exists.")
            except decisions.TaskRefused as exc:
                s.rollback()
                return ClickResult("refused", f"You can't decide this one. {exc}")
            except decisions.TaskConflict:
                s.rollback()
                return ClickResult("refused", "Someone has already decided this task.")
            except decisions.InvalidDecision as exc:
                s.rollback()
                return ClickResult("refused", str(exc))
            except decisions.EngineUnavailable:
                s.rollback()
                return ClickResult("refused", "Relay's engine is not running; try again shortly.")
            title, name = cards.card_title(task), user.display_name
            s.commit()
        done = f"*{option.replace('_', ' ').capitalize()}* by {name}"
        return ClickResult(
            "decided", done + (f": {reason.strip()}" if reason.strip() else ""), title
        )

    # ------------------------------------------------------------ cards out

    def channel_for(self, session: Session, task: m.HumanTask) -> str | None:
        """A named assignee on Slack gets a DM; otherwise the role's channel, if it has one."""
        if task.assignee_user_id is not None:
            user = session.get(m.User, task.assignee_user_id)
            if user is not None and user.slack_user_id:
                return user.slack_user_id
        return self.channels.get(task.assignee_role)

    def cards_to_post(self) -> list[CardPost]:
        """Open tasks that have no card yet, oldest first."""
        posted = exists().where(m.SlackPost.task_id == m.HumanTask.id)
        with self.sessions() as s:
            tasks = s.scalars(
                select(m.HumanTask)
                .where(m.HumanTask.status.in_(_OPEN), ~posted)
                .order_by(m.HumanTask.created_at)
            ).all()
            out = []
            for task in tasks:
                channel = self.channel_for(s, task)
                if channel is not None:
                    out.append(
                        CardPost(
                            task.id, channel, cards.card_title(task), cards.approval_card(task)
                        )
                    )
            return out

    def mark_posted(self, task_id: uuid.UUID, channel: str, ts: str | None) -> None:
        """Remember a posted card so it is never posted twice."""
        with self.sessions.begin() as s:
            if s.scalar(select(m.SlackPost).where(m.SlackPost.task_id == task_id)) is None:
                s.add(m.SlackPost(task_id=task_id, channel=channel, ts=ts))

    # ------------------------------------------------------------ manager digest

    def digest(self, slack_user_id: str) -> str | None:
        """The manager's daily onboarding digest (templated), or None if they have no joiners."""
        with self.sessions() as s:
            user = self.user_for(s, slack_user_id)
            if user is None or user.person_id is None or "manager" not in user.roles:
                return None
            code = s.scalar(
                select(m.Employee.employee_code).where(m.Employee.person_id == user.person_id)
            )
            if code is None:
                return None
            runs = [
                r
                for r in s.scalars(
                    select(m.WorkflowRun)
                    .where(m.WorkflowRun.definition_name == "onboarding")
                    .order_by(m.WorkflowRun.started_at)
                )
                if (r.context or {}).get("ctx", {}).get("manager_code") == code
            ]
            if not runs:
                return None
            lines = [f"Your team's onboarding today: {len(runs)} joiner(s)."]
            for run in runs:
                steps = s.scalars(select(m.StepRun.status).where(m.StepRun.run_id == run.id)).all()
                done = sum(1 for st in steps if st is m.StepStatus.SUCCEEDED)
                label = (run.context or {}).get("subject_label") or run.subject_id
                lines.append(f"• {label}: {run.status.value}, {done} of {len(steps)} steps done")
            return "\n".join(lines)

    def digests(self) -> list[tuple[str, str]]:
        """(slack id, digest) for every manager on Slack who has joiners."""
        with self.sessions() as s:
            ids = s.scalars(select(m.User.slack_user_id).where(m.User.slack_user_id.is_not(None)))
            slack_ids = [i for i in ids if i]
        out = []
        for slack_id in slack_ids:
            text = self.digest(slack_id)
            if text:
                out.append((slack_id, text))
        return out
