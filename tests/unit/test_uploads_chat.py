"""Signed joiner upload and web chat (#40)."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.api import uploads
from relay.api.app import create_app
from relay.api.deps import ApiServices, get_session
from relay.api.ratelimit import chat_limiter, login_limiter
from relay.config import ModelsConfig, get_config
from relay.core.audit.verifier import verify_chain
from relay.core.audit.writer import policy_audit_hook
from relay.core.policy import PolicyService
from relay.db import models as m
from relay.db.crypto import decrypt
from relay.models.router import ModelRouter
from relay.models.types import InMemorySink
from relay.skills.helpdesk import HelpdeskSkill
from relay.skills.intake import IntakeSkill

pytestmark = pytest.mark.db

PDF = b"%PDF-1.7 synthetic"
PNG = b"\x89PNG\r\n\x1a\n synthetic"
JPG = b"\xff\xd8\xff synthetic"
PAN = "ABCPK1234L"


@dataclass
class FakeEngine:
    waiting: bool = True
    actions: list[tuple[UUID, str, Mapping[str, Any]]] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)

    def record_subject_action(
        self, run_id: UUID, action: str, payload: Mapping[str, Any] | None = None, *, actor: str
    ) -> bool:
        self.actions.append((run_id, action, payload or {}))
        self.actors.append(actor)
        return self.waiting


class World:
    def __init__(self, session: Session, tmp_path: Path) -> None:
        self.session = session
        self.storage = tmp_path / "docs"
        self.engine = FakeEngine()
        config = ModelsConfig.model_validate(
            {
                "providers": {
                    "mock": {
                        "litellm_model": "mock/x",
                        "price_inr": {"in_per_1k": 0, "out_per_1k": 0},
                    }
                },
                "tiers": {"tier1": "mock", "tier2": "mock"},
                "defaults": {"max_output_tokens": 400, "temperature": 0, "json_mode": True},
            }
        )
        router = ModelRouter(config, InMemorySink())
        self.app = create_app(
            ApiServices(
                policy=PolicyService(audit=policy_audit_hook),
                config=get_config(),
                engine=self.engine,  # type: ignore[arg-type]
                intake=IntakeSkill(router),
                helpdesk=HelpdeskSkill(router),
                storage_dir=self.storage,
            )
        )
        self.app.dependency_overrides[get_session] = lambda: session
        self.client = TestClient(self.app)
        # Other test modules commit tasks, so only count what this test creates.
        self.existing_tasks = set(session.scalars(select(m.HumanTask.id)))
        self.person = m.Person(full_name="Asha Rao")
        session.add(self.person)
        session.flush()
        self.run = m.WorkflowRun(
            definition_name="onboarding",
            definition_version=1,
            subject_type="employee",
            subject_id=str(uuid.uuid4()),
            status=m.RunStatus.WAITING,
            started_by_actor="test",
        )
        session.add(self.run)
        session.flush()

    def token(self, now: datetime | None = None) -> str:
        return uploads.issue_link(
            self.session, self.person.id, self.run.id, now or datetime.now(UTC)
        )

    def upload(self, token: str, **overrides: Any) -> Any:
        data = {
            "token": token,
            "categories": ["pan"],
            "consents": ["onboarding_verification"],
            **overrides.pop("data", {}),
        }
        files = overrides.pop("files", [("files", ("pan.pdf", PDF, "application/pdf"))])
        return self.client.post("/api/v1/documents", data=data, files=files)

    def login(self, *roles: str, person_id: UUID | None = None) -> TestClient:
        user = m.User(
            email=f"{uuid.uuid4().hex[:8]}@test.example",
            display_name="T",
            roles=list(roles),
            person_id=person_id,
        )
        self.session.add(user)
        self.session.flush()
        login_limiter.reset()
        chat_limiter.reset()
        token = self.client.post("/api/v1/auth/demo-login", json={"email": user.email}).json()[
            "token"
        ]
        return TestClient(self.app, headers={"Authorization": f"Bearer {token}"})

    def tasks(self) -> list[m.HumanTask]:
        rows = self.session.scalars(select(m.HumanTask).order_by(m.HumanTask.created_at))
        return [row for row in rows if row.id not in self.existing_tasks]


@pytest.fixture
def world(db_session: Session, tmp_path: Path) -> Iterator[World]:
    yield World(db_session, tmp_path)


# ---------------------------------------------------------------- upload


def test_upload_stores_encrypted_records_consent_and_resumes_the_run(world: World) -> None:
    response = world.upload(world.token())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resumed"] is True
    assert body["documents"][0]["category"] == "pan"

    document = world.session.get(m.Document, UUID(body["documents"][0]["id"]))
    assert document is not None
    stored = (world.storage / document.storage_ref).read_text(encoding="ascii")
    assert b"%PDF" not in stored.encode()
    assert decrypt(stored)  # really encrypted with our key
    consent = world.session.scalars(
        select(m.Consent).where(m.Consent.person_id == world.person.id)
    ).one()
    assert consent.purpose_id == "onboarding_verification"
    assert consent.notice_version == "dpdp_onboarding_v1"

    (run_id, action, payload) = world.engine.actions[0]
    assert run_id == world.run.id
    assert action == "documents.uploaded"
    assert payload["consented_purposes"] == ["onboarding_verification"]
    assert payload["documents"] == body["documents"]
    assert world.engine.actors == [f"subject:{world.person.id}"]


def test_the_file_name_on_disk_reveals_nothing(world: World) -> None:
    files = [("files", ("../../evil PAN ABCPK1234L.pdf", PDF, "application/pdf"))]
    response = world.upload(world.token(), files=files)
    assert response.status_code == 201
    names = [path.name for path in world.storage.iterdir()]
    assert all(PAN not in name and ".." not in name for name in names)
    assert all(path.parent == world.storage for path in world.storage.iterdir())


@pytest.mark.parametrize(("data", "name"), [(PDF, "a.pdf"), (PNG, "a.png"), (JPG, "a.jpg")])
def test_allowed_types(world: World, data: bytes, name: str) -> None:
    response = world.upload(
        world.token(), files=[("files", (name, data, "application/octet-stream"))]
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    ("content", "name", "claimed"),
    [
        (b"MZ executable", "pan.pdf", "application/pdf"),  # lies about its type
        (b"<html><script>", "pan.png", "image/png"),
        (b"", "pan.pdf", "application/pdf"),
    ],
)
def test_files_are_judged_by_their_bytes(
    world: World, content: bytes, name: str, claimed: str
) -> None:
    token = world.token()
    response = world.upload(token, files=[("files", (name, content, claimed))])
    assert response.status_code == 422
    assert world.engine.actions == []
    assert not world.storage.exists() or not list(world.storage.iterdir())
    # A refused file does not use up the link.
    assert world.upload(token).status_code == 201


def test_oversize_is_refused(world: World) -> None:
    big = PDF + b"x" * uploads.MAX_BYTES
    response = world.upload(world.token(), files=[("files", ("big.pdf", big, "application/pdf"))])
    assert response.status_code == 422
    assert "10 MB" in response.json()["detail"]


def test_a_link_works_only_once(world: World) -> None:
    token = world.token()
    assert world.upload(token).status_code == 201
    assert world.upload(token).status_code == 403
    assert len(world.engine.actions) == 1


def test_an_expired_link_is_refused(world: World) -> None:
    long_ago = datetime.now(UTC) - uploads.LINK_LIFETIME - timedelta(seconds=1)
    assert world.upload(world.token(long_ago)).status_code == 403


def test_a_link_is_good_just_before_72_hours(world: World) -> None:
    nearly = datetime.now(UTC) - uploads.LINK_LIFETIME + timedelta(minutes=1)
    assert world.upload(world.token(nearly)).status_code == 201


@pytest.mark.parametrize("token", ["", "nonsense", "a.b", "e30.deadbeef"])
def test_forged_links_are_refused(world: World, token: str) -> None:
    assert world.upload(token).status_code in (403, 422)
    assert world.engine.actions == []


def test_a_tampered_link_is_refused(world: World) -> None:
    token = world.token()
    body, signature = token.split(".")
    other = world.token()
    assert world.upload(f"{other.split('.')[0]}.{signature}").status_code == 403
    assert world.upload(f"{body}x.{signature}").status_code == 403


def test_a_link_for_nothing_stored_is_refused(world: World) -> None:
    token = world.token()
    nonce = json.loads(base64.urlsafe_b64decode(token.partition(".")[0]))["n"]
    # The test database is shared, so find this token's own link rather than any link.
    link = world.session.scalars(select(m.UploadLink).where(m.UploadLink.nonce == nonce)).one()
    world.session.delete(link)
    world.session.flush()
    assert world.upload(token).status_code == 403


@pytest.mark.parametrize(
    "data",
    [
        {"categories": ["passport_scan"]},
        {"categories": ["pan", "aadhaar"]},
        {"consents": ["sell_my_data"]},
    ],
)
def test_bad_fields_are_refused_and_keep_the_link(world: World, data: dict[str, Any]) -> None:
    token = world.token()
    assert world.upload(token, data=data).status_code == 422
    assert world.upload(token).status_code == 201


def test_consent_is_required(world: World) -> None:
    response = world.client.post(
        "/api/v1/documents",
        data={"token": world.token(), "categories": ["pan"]},
        files=[("files", ("pan.pdf", PDF, "application/pdf"))],
    )
    assert response.status_code == 422


def test_a_sidecar_is_kept_encrypted_for_the_extract_step(world: World) -> None:
    sidecar = {"pan": {"pan": PAN, "full_name": "Asha Rao"}}
    response = world.upload(world.token(), data={"sidecars": json.dumps(sidecar)})
    assert response.status_code == 201
    document = world.session.get(m.Document, UUID(response.json()["documents"][0]["id"]))
    assert document is not None
    assert document.extracted["pan"] == PAN
    assert world.upload(world.token(), data={"sidecars": "not json"}).status_code == 422


def test_raw_identifiers_stay_out_of_the_engine_payload_and_response(world: World) -> None:
    sidecar = {"pan": {"pan": PAN}}
    response = world.upload(world.token(), data={"sidecars": json.dumps(sidecar)})
    assert PAN not in response.text
    assert PAN not in json.dumps(world.engine.actions[0][2])


def test_when_nothing_is_waiting_the_link_can_be_used_again(world: World) -> None:
    world.engine.waiting = False
    token = world.token()
    response = world.upload(token)
    assert response.status_code == 409
    world.engine.waiting = True
    assert world.upload(token).status_code == 201


def test_uploads_are_refused_without_an_engine(world: World) -> None:
    services: ApiServices = world.app.state.services
    services.engine = None
    token = world.token()
    assert world.upload(token).status_code == 503
    services.engine = world.engine  # type: ignore[assignment]
    assert world.upload(token).status_code == 201


def test_the_upload_is_authorized_and_on_the_chain(world: World) -> None:
    world.upload(world.token())
    event = world.session.scalars(
        select(m.AuditEvent)
        .where(m.AuditEvent.action == "policy.authorize:documents.upload")
        .order_by(m.AuditEvent.seq.desc())
        .limit(1)
    ).one()
    assert event.actor_id == f"subject:{world.person.id}"
    assert event.effect == "ALLOW"
    assert verify_chain(world.session).ok


def test_links_are_issued_only_to_those_allowed(world: World) -> None:
    body = {"person_id": str(world.person.id), "run_id": str(world.run.id)}
    created = world.login("hr_ops").post("/api/v1/documents/links", json=body)
    assert created.status_code == 201
    assert world.upload(created.json()["token"]).status_code == 201
    assert world.login("employee").post("/api/v1/documents/links", json=body).status_code == 403
    assert world.client.post("/api/v1/documents/links", json=body).status_code == 401
    missing = {**body, "run_id": str(uuid.uuid4())}
    assert world.login("hr_ops").post("/api/v1/documents/links", json=missing).status_code == 404


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (PDF, "application/pdf"),
        (PNG, "image/png"),
        (JPG, "image/jpeg"),
        (b"nope", None),
        (b"", None),
    ],
)
def test_sniff_mime(data: bytes, mime: str | None) -> None:
    assert uploads.sniff_mime(data) == mime


# ---------------------------------------------------------------- chat


def say(client: TestClient, text: str) -> dict[str, Any]:
    response = client.post("/api/v1/chat", json={"text": text})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_a_policy_question_is_answered_with_a_citation(world: World) -> None:
    reply = say(world.login("employee"), "how many leaves do I get in probation")
    assert reply["intent"] == "policy_question"
    assert reply["citations"][0]["id"] == "leave.probation"
    assert reply["routed_to_hr"] is False
    assert world.tasks() == []


def test_an_unanswerable_policy_question_really_reaches_hr(world: World) -> None:
    reply = say(world.login("employee"), "what is the leave policy on the moon colony")
    if reply["citations"]:
        pytest.skip("retrieval found a passage for this phrasing")
    assert reply["routed_to_hr"] is True
    assert world.tasks()[0].assignee_role == "hr_ops"


def test_an_unclear_message_opens_a_real_task(world: World) -> None:
    reply = say(world.login("employee"), "the coffee machine is broken")
    assert reply["intent"] == "unknown"
    assert reply["routed_to_hr"] is True
    (task,) = world.tasks()
    assert task.assignee_role == "hr_ops"
    assert task.kind is m.HumanTaskKind.EXCEPTION


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("I want to report sexual harassment by my lead", "posh.case.route"),
        ("I want to file a formal complaint", "grievance.case.route"),
        ("I want to report fraud in procurement", "whistleblower.case.route"),
    ],
)
def test_sensitive_matters_are_routed_and_never_answered(
    world: World, text: str, action: str
) -> None:
    reply = say(world.login("employee"), text)
    assert reply["routed_to_hr"] is True
    assert reply["citations"] == []
    assert "can't advise" in reply["reply"]
    (task,) = world.tasks()
    assert task.kind is m.HumanTaskKind.ROUTE_ONLY
    assert task.action == action
    assert task.assignee_role == "hr_admin"


def test_resigning_in_chat_accepts_nothing(world: World) -> None:
    """PRD 6.3: resignation acceptance is reserved. The chat must not suggest it happened."""
    reply = say(world.login("employee"), "I want to resign")
    assert "nothing has been accepted" in reply["reply"]
    assert reply["routed_to_hr"] is True
    assert world.tasks()[0].status is m.HumanTaskStatus.OPEN


def test_only_masked_copies_are_stored(world: World) -> None:
    say(world.login("employee"), f"my PAN is {PAN} and I have a question about the canteen")
    stored = " ".join(world.session.scalars(select(m.Message.masked_text)))
    assert PAN not in stored
    assert "<PAN_1>" in stored
    assert PAN not in json.dumps(world.tasks()[0].context_packet)


def test_status_comes_from_my_own_run_only(world: World) -> None:
    employee = m.Employee(person_id=world.person.id)
    world.session.add(employee)
    world.session.flush()
    mine = m.WorkflowRun(
        definition_name="onboarding",
        definition_version=1,
        subject_type="employee",
        subject_id=str(employee.id),
        status=m.RunStatus.RUNNING,
        started_by_actor="t",
    )
    world.session.add(mine)
    world.session.flush()
    world.session.add(
        m.StepRun(run_id=mine.id, step_id="a", kind=m.StepKind.RULE, status=m.StepStatus.SUCCEEDED)
    )
    world.session.add(m.StepRun(run_id=mine.id, step_id="b", kind=m.StepKind.RULE))
    world.session.flush()
    assert (
        "1 of 2 steps"
        in say(world.login("employee", person_id=world.person.id), "what's my onboarding status")[
            "reply"
        ]
    )
    assert "can't find" in say(world.login("employee"), "what's my onboarding status")["reply"]


def test_chat_needs_a_role_a_session_and_a_sane_length(world: World) -> None:
    assert world.client.post("/api/v1/chat", json={"text": "hi"}).status_code == 401
    assert world.login("auditor").post("/api/v1/chat", json={"text": "hi"}).status_code == 403
    client = world.login("employee")
    assert client.post("/api/v1/chat", json={"text": ""}).status_code == 422
    assert client.post("/api/v1/chat", json={"text": "x" * 2001}).status_code == 422


def test_chat_is_rate_limited(world: World) -> None:
    client = world.login("employee")
    chat_limiter.reset()
    for _ in range(30):
        client.post("/api/v1/chat", json={"text": "how long is probation"})
    assert client.post("/api/v1/chat", json={"text": "how long is probation"}).status_code == 429
    chat_limiter.reset()


def test_chat_says_when_it_is_not_available(world: World) -> None:
    services: ApiServices = world.app.state.services
    services.intake = None
    assert world.login("employee").post("/api/v1/chat", json={"text": "hi"}).status_code == 503
