"""Web chat (PRD 16.1, 27.4): the same intake path Slack will use.

Skills decide what was asked; this decides what to say back. Anything sensitive or unclear goes to
a person, as a real task, so "I'll route this to HR" is true. Only masked text is stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relay.core.engine.ports import SkillPort
from relay.db import models as m
from relay.models.pii_gateway import PiiGateway, PiiVault

_ROUTE_ONLY = {
    "posh_report": ("posh.case.route", "hr_admin"),
    "grievance_report": ("grievance.case.route", "hr_admin"),
    "whistleblower_report": ("whistleblower.case.route", "hr_admin"),
}
_SENSITIVE_REPLY = (
    "I've passed this to a named person in HR, in confidence. I can't advise on it or decide "
    "anything about it, but they will contact you."
)
_ROUTED_REPLY = "I've passed this to HR, and someone will get back to you."
_RESIGN_REPLY = (
    "I've let HR know you'd like to resign. A person will confirm the next steps with you; nothing "
    "has been accepted or decided yet."
)
_DATA_REPLY = (
    "I've passed your data request to HR. You can also raise it directly under your data requests."
)
_UPLOAD_REPLY = "Thanks. Please use the upload link we sent you; I can see your documents once they arrive there."
# These open a task for a person; everything else is answered here.
_TO_HR = {
    "unknown": _ROUTED_REPLY,
    "resign": _RESIGN_REPLY,
    "data_request": _DATA_REPLY,
}


@dataclass(frozen=True)
class ChatReply:
    """What the person sees."""

    intent: str
    reply: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    routed_to_hr: bool = False


def _status_reply(session: Session, user: m.User) -> str:
    """Describe the signed-in person's own onboarding, from their own run only."""
    if user.person_id is None:
        return "I can't find an onboarding linked to your account."
    employee_id = session.scalar(
        select(m.Employee.id).where(m.Employee.person_id == user.person_id)
    )
    run = session.scalar(
        select(m.WorkflowRun)
        .where(
            m.WorkflowRun.subject_type == "employee",
            m.WorkflowRun.subject_id == str(employee_id),
            m.WorkflowRun.definition_name == "onboarding",
        )
        .order_by(m.WorkflowRun.started_at.desc())
        .limit(1)
    )
    if run is None:
        return "I can't find an onboarding in progress for you yet."
    total, done = session.execute(
        select(
            func.count(),
            func.count().filter(m.StepRun.status == m.StepStatus.SUCCEEDED),
        ).where(m.StepRun.run_id == run.id)
    ).one()
    return f"Your onboarding is {run.status.value}: {done} of {total} steps are done."


def _open_task(
    session: Session, *, action: str, role: str, title: str, masked: str, intent: str
) -> None:
    session.add(
        m.HumanTask(
            kind=m.HumanTaskKind.ROUTE_ONLY if intent in _ROUTE_ONLY else m.HumanTaskKind.EXCEPTION,
            action=action,
            assignee_role=role,
            options=["acknowledged"],
            context_packet={"title": title, "summary": masked, "facts": {"Intent": intent}},
        )
    )


def handle_message(
    session: Session,
    user: m.User,
    text: str,
    intake: SkillPort,
    helpdesk: SkillPort,
    gateway: PiiGateway,
) -> ChatReply:
    """Answer one message, keeping only masked copies (PRD 10.2)."""
    masked = gateway.mask(text, PiiVault()).text
    conversation = session.scalar(
        select(m.Conversation)
        .where(m.Conversation.user_id == user.id, m.Conversation.channel == "web")
        .order_by(m.Conversation.created_at.desc())
        .limit(1)
    )
    if conversation is None:
        conversation = m.Conversation(user_id=user.id, channel="web")
        session.add(conversation)
        session.flush()

    intent = str(intake.run({"text": text}, run_id=None, step_run_id=None).output["intent"])
    reply = _route(session, user, text, masked, intent, helpdesk)
    session.add(
        m.Message(conversation_id=conversation.id, role="user", masked_text=masked, intent=intent)
    )
    session.add(
        m.Message(
            conversation_id=conversation.id,
            role="assistant",
            masked_text=gateway.mask(reply.reply, PiiVault()).text,
            intent=intent,
        )
    )
    session.flush()
    return reply


def _route(
    session: Session, user: m.User, text: str, masked: str, intent: str, helpdesk: SkillPort
) -> ChatReply:
    if intent in _ROUTE_ONLY:
        action, role = _ROUTE_ONLY[intent]
        # Route only: no summary, no suggestion, and the message itself stays with the named role.
        _open_task(
            session,
            action=action,
            role=role,
            title="Confidential report",
            masked=masked,
            intent=intent,
        )
        return ChatReply(intent, _SENSITIVE_REPLY, routed_to_hr=True)
    if intent == "policy_question":
        answer = helpdesk.run({"question": text}, run_id=None, step_run_id=None).output
        citations = list(answer["citations"])
        if not citations:
            _open_task(
                session,
                action="chat.question",
                role="hr_ops",
                title="Question from chat",
                masked=masked,
                intent=intent,
            )
        return ChatReply(intent, str(answer["answer"]), citations, routed_to_hr=not citations)
    if intent == "onboarding_status":
        return ChatReply(intent, _status_reply(session, user))
    if intent == "documents_uploaded":
        return ChatReply(intent, _UPLOAD_REPLY)
    _open_task(
        session,
        action="chat.question",
        role="hr_ops",
        title="Question from chat",
        masked=masked,
        intent=intent,
    )
    return ChatReply(intent, _TO_HR.get(intent, _ROUTED_REPLY), routed_to_hr=True)
