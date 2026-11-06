"""`POST /documents`, `POST /documents/links` and `POST /chat` (PRD 16.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from relay.api import chat as chat_logic
from relay.api import uploads
from relay.api.deps import Auth, Db, Services
from relay.api.ratelimit import chat_limiter
from relay.core.policy import ActionRequest, Actor, SubjectRef
from relay.db import models as m
from relay.models.pii_gateway import PiiGateway

router = APIRouter(prefix="/api/v1")
_UPLOADED = "documents.uploaded"


class LinkIn(BaseModel):
    """Who is uploading, and for which run."""

    person_id: UUID
    run_id: UUID


class LinkOut(BaseModel):
    """The token to put in the joiner's link. Show it once; it is a credential."""

    token: str


class UploadedDocument(BaseModel):
    """A stored document, with nothing from inside it."""

    id: UUID
    category: str


class UploadOut(BaseModel):
    """What was stored."""

    documents: list[UploadedDocument]
    resumed: bool


class ChatIn(BaseModel):
    """One message."""

    text: str = Field(min_length=1, max_length=2000)


class ChatOut(BaseModel):
    """The answer."""

    intent: str
    reply: str
    citations: list[dict[str, Any]]
    routed_to_hr: bool


@router.post("/documents/links", status_code=status.HTTP_201_CREATED, tags=["documents"])
def create_link(body: LinkIn, auth: Auth, session: Db) -> LinkOut:
    """Issue a one-time upload link for a joiner."""
    auth.require("documents.request")
    if (
        session.get(m.Person, body.person_id) is None
        or session.get(m.WorkflowRun, body.run_id) is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That person or run was not found.")
    token = uploads.issue_link(session, body.person_id, body.run_id, datetime.now(UTC))
    session.commit()
    return LinkOut(token=token)


def _read(upload: UploadFile) -> bytes:
    return upload.file.read(uploads.MAX_BYTES + 1)


def _parse_sidecars(raw: str | None) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Sidecars must be JSON."
        ) from None
    if not isinstance(parsed, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Sidecars must be an object.")
    return parsed


@router.post("/documents", status_code=status.HTTP_201_CREATED, tags=["documents"])
def upload_documents(
    session: Db,
    services: Services,
    token: Annotated[str, Form()],
    categories: Annotated[list[str], Form()],
    consents: Annotated[list[str], Form()],
    files: Annotated[list[UploadFile], File()],
    notice_version: Annotated[str, Form()] = "dpdp_onboarding_v1",
    sidecars: Annotated[str | None, Form()] = None,
) -> UploadOut:
    """Accept a joiner's documents and consent, then tell the waiting run.

    The signed link is the credential: there is no account. A failure before the engine answers
    leaves the link usable, so the joiner can try again.
    """
    now = datetime.now(UTC)
    engine = services.engine
    if engine is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Uploads are not available right now."
        )
    try:
        link = uploads.claim_link(session, token, now)
    except uploads.InvalidLink as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None

    decision, _ = services.policy.authorize(
        session,
        ActionRequest(
            action="documents.upload",
            actor=Actor(type="human", id=f"subject:{link.person_id}", roles=["new_joiner"]),
            subject=SubjectRef(type="person", id=str(link.person_id)),
        ),
    )
    if decision.effect != "ALLOW":
        session.commit()  # keep the refusal on record
        raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason)

    if len(categories) != len(files):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Each file needs a document type."
        )
    known = set(services.policy.policies(session).rbac.purposes)
    unknown = [purpose for purpose in consents if purpose not in known]
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown purpose: {unknown[0][:40]}."
        )
    parsed = _parse_sidecars(sidecars)
    incoming = [
        uploads.IncomingFile(category, _read(upload), parsed.get(category))
        for category, upload in zip(categories, files, strict=True)
    ]
    try:
        for item in incoming:
            uploads.check_file(item)
    except uploads.UploadRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None

    rows = uploads.save_upload(
        session, link, incoming, consents, notice_version, services.storage_dir, now
    )
    documents = [UploadedDocument(id=row.id, category=row.category.value) for row in rows]
    run_id, link_id, person_id = link.run_id, link.id, link.person_id
    session.commit()  # the engine works in its own transaction

    resumed = engine.record_subject_action(
        run_id,
        _UPLOADED,
        {
            "documents": [d.model_dump(mode="json") for d in documents],
            "consented_purposes": consents,
        },
        actor=f"subject:{person_id}",
    )
    if not resumed:
        # The run is not waiting yet, or has moved on: keep the files, and let the link be reused.
        reopened = session.get(m.UploadLink, link_id)
        assert reopened is not None
        reopened.used_at = None
        session.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "We saved your documents, but your onboarding isn't ready for them yet. Please try again shortly.",
        )
    return UploadOut(documents=documents, resumed=True)


class AcknowledgeIn(BaseModel):
    """The joiner's link, and which required policies they read (PRD 5.1 step 17)."""

    token: str
    policies: list[str] = Field(
        default_factory=lambda: ["code_of_conduct", "posh", "it_acceptable_use"]
    )


@router.post("/policies/acknowledgements", tags=["documents"])
def acknowledge_policies(body: AcknowledgeIn, session: Db, services: Services) -> dict[str, Any]:
    """The joiner acknowledges the required policies, using the link from their invitation."""
    engine = services.engine
    if engine is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Not available right now.")
    try:
        link = uploads.claim_link(session, body.token, datetime.now(UTC), allow_used=True)
    except uploads.InvalidLink as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    decision, _ = services.policy.authorize(
        session,
        ActionRequest(
            action="policies.acknowledge",
            actor=Actor(type="human", id=f"subject:{link.person_id}", roles=["new_joiner"]),
            subject=SubjectRef(type="person", id=str(link.person_id)),
        ),
    )
    run_id, person_id = link.run_id, link.person_id
    session.commit()  # the decision is on record; the engine works in its own transaction
    if decision.effect != "ALLOW":
        raise HTTPException(status.HTTP_403_FORBIDDEN, decision.reason)
    resumed = engine.record_subject_action(
        run_id,
        "policies.acknowledged",
        {"policies": list(body.policies)},
        actor=f"subject:{person_id}",
    )
    if not resumed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your onboarding isn't ready for this yet. Please try again shortly.",
        )
    return {"resumed": True}


@router.post("/chat", tags=["chat"])
def post_chat(body: ChatIn, auth: Auth, session: Db, services: Services) -> ChatOut:
    """Web chat: the same intake path Slack will use (PRD 27.4)."""
    if not chat_limiter.allow(auth.user.email):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages. Try again soon.")
    auth.require("chat.send")
    if services.intake is None or services.helpdesk is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Chat is not available right now.")
    reply = chat_logic.handle_message(
        session, auth.user, body.text, services.intake, services.helpdesk, PiiGateway()
    )
    session.commit()
    return ChatOut(
        intent=reply.intent,
        reply=reply.reply,
        citations=reply.citations,
        routed_to_hr=reply.routed_to_hr,
    )
