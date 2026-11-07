"""SQLAlchemy models for the full PRD Section 10 data model.

Conventions:
- Every table has a UUID primary key (`gen_random_uuid()`) and `created_at`; mutable tables also
  have `updated_at`. Append-only tables (`audit_events`, `lifecycle_events`, `model_payloads`)
  have no `updated_at`, and a DB trigger rejects UPDATE/DELETE/TRUNCATE on them.
- Columns marked sensitive in the PRD use `EncryptedString` / `EncryptedJSON` and must never be
  logged.
- Enums are stored as constrained strings (`native_enum=False`) so adding a value is a small
  migration.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from relay.db.crypto import EncryptedJSON, EncryptedString

# Tables that must never be updated or deleted (PRD 6.6, 10.3).
APPEND_ONLY_TABLES = ("audit_events", "lifecycle_events", "model_payloads")


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(
        e,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda x: [m.value for m in x],
        length=32,
    )


class Base(DeclarativeBase):
    """Declarative base with a UUID primary key and creation timestamp."""

    type_annotation_map = {dict[str, Any]: JSONB, list[str]: ARRAY(Text)}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Timestamped:
    """Mixin for mutable tables."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------- enums


class EmployeeState(enum.StrEnum):
    PRE_HIRE = "pre_hire"
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    NOTICE = "notice"
    EXITED = "exited"


class DocumentCategory(enum.StrEnum):
    PAN = "pan"
    AADHAAR = "aadhaar"
    BANK_PROOF = "bank_proof"
    EDUCATION = "education"
    PHOTO = "photo"
    OTHER = "other"


class DataRequestType(enum.StrEnum):
    ACCESS = "access"
    CORRECTION = "correction"
    ERASURE = "erasure"


class DefinitionStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class RunStatus(enum.StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepKind(enum.StrEnum):
    RULE = "rule"
    CONNECTOR = "connector"
    SKILL = "skill"
    HUMAN = "human"
    WAIT = "wait"
    PARALLEL = "parallel"


class StepStatus(enum.StrEnum):
    """Step state machine, PRD 11.3."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    WAITING_HUMAN = "WAITING_HUMAN"
    WAITING_TIMER = "WAITING_TIMER"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    PARKED = "PARKED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    SKIPPED = "SKIPPED"


class TimerPurpose(enum.StrEnum):
    WAIT = "wait"
    SLA_REMINDER = "sla_reminder"
    SLA_ESCALATE = "sla_escalate"
    RETRY = "retry"


class LedgerStatus(enum.StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED_PERMANENT = "failed_permanent"


class HumanTaskKind(enum.StrEnum):
    RESERVED_DECISION = "reserved_decision"
    APPROVAL = "approval"
    CONFLICT = "conflict"
    EXCEPTION = "exception"
    LOW_CONFIDENCE = "low_confidence"
    OUTAGE = "outage"
    ROUTE_ONLY = "route_only"


class HumanTaskStatus(enum.StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    DECIDED = "decided"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Effect(enum.StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    DENY = "DENY"


class KillScope(enum.StrEnum):
    GLOBAL = "global"
    WORKFLOW = "workflow"
    STEP = "step"
    SKILL = "skill"
    CONNECTOR = "connector"


class ActorType(enum.StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class PayloadDirection(enum.StrEnum):
    OUT = "out"
    IN = "in"


class CostKind(enum.StrEnum):
    LLM = "llm"
    OCR_CPU = "ocr_cpu"
    EMBEDDING_CPU = "embedding_cpu"
    INFRA_ALLOC = "infra_alloc"


# ---------------------------------------------------------------- 10.1 system of record


class Person(Timestamped, Base):
    __tablename__ = "persons"

    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(EncryptedString)
    dob: Mapped[str | None] = mapped_column(EncryptedString)  # ISO date, encrypted
    pan: Mapped[str | None] = mapped_column(EncryptedString)
    aadhaar_last4: Mapped[str | None] = mapped_column(String(4))
    bank_account: Mapped[str | None] = mapped_column(EncryptedString)
    ifsc: Mapped[str | None] = mapped_column(String(11))
    photo_ref: Mapped[str | None] = mapped_column(String(500))


class Employee(Timestamped, Base):
    __tablename__ = "employees"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("persons.id"))
    employee_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    state: Mapped[EmployeeState] = mapped_column(
        _enum(EmployeeState, "employee_state"), default=EmployeeState.PRE_HIRE
    )
    grade: Mapped[str | None] = mapped_column(String(16))
    role_title: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(100))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"))
    joining_date: Mapped[date | None] = mapped_column(Date)
    probation_end: Mapped[date | None] = mapped_column(Date)
    ctc_approved: Mapped[Any] = mapped_column(EncryptedJSON, nullable=True)
    last_working_day: Mapped[date | None] = mapped_column(Date)


class Candidate(Timestamped, Base):
    __tablename__ = "candidates"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("persons.id"))
    status: Mapped[str] = mapped_column(String(32))
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", use_alter=True, name="fk_candidates_offer_id")
    )
    external_ref: Mapped[str | None] = mapped_column(String(64), unique=True)


class Offer(Timestamped, Base):
    __tablename__ = "offers"

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"))
    ctc: Mapped[Any] = mapped_column(EncryptedJSON)
    grade: Mapped[str] = mapped_column(String(16))
    role_title: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(100))
    joining_date: Mapped[date] = mapped_column(Date)
    approved_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("human_tasks.id", use_alter=True, name="fk_offers_approved_decision_id")
    )


class LifecycleEvent(Base):
    """Append-only employee lifecycle history."""

    __tablename__ = "lifecycle_events"

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))


class Document(Timestamped, Base):
    __tablename__ = "documents"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("persons.id"))
    category: Mapped[DocumentCategory] = mapped_column(_enum(DocumentCategory, "document_category"))
    storage_ref: Mapped[str] = mapped_column(String(500))
    extracted: Mapped[Any] = mapped_column(EncryptedJSON, nullable=True)
    validation: Mapped[dict[str, Any] | None]
    retention_until: Mapped[date | None] = mapped_column(Date)


class Consent(Timestamped, Base):
    __tablename__ = "consents"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("persons.id"))
    purpose_id: Mapped[str] = mapped_column(String(64))
    notice_version: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str] = mapped_column(String(32))


class DataRequest(Timestamped, Base):
    __tablename__ = "data_requests"

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("persons.id"))
    type: Mapped[DataRequestType] = mapped_column(_enum(DataRequestType, "data_request_type"))
    status: Mapped[str] = mapped_column(String(32), default="open")
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))


class User(Timestamped, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    slack_user_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("persons.id"))
    roles: Mapped[list[str]] = mapped_column(default=list)


# ---------------------------------------------------------------- 10.2 runtime


class WorkflowDefinition(Timestamped, Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (UniqueConstraint("name", "version"),)

    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    yaml: Mapped[str] = mapped_column(Text)
    status: Mapped[DefinitionStatus] = mapped_column(
        _enum(DefinitionStatus, "definition_status"), default=DefinitionStatus.DRAFT
    )
    activated_by: Mapped[str | None] = mapped_column(String(200))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRun(Timestamped, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (Index("ix_workflow_runs_subject", "subject_type", "subject_id"),)

    definition_name: Mapped[str] = mapped_column(String(100))
    definition_version: Mapped[int] = mapped_column(Integer)
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[RunStatus] = mapped_column(
        _enum(RunStatus, "run_status"), default=RunStatus.RUNNING
    )
    context: Mapped[dict[str, Any]] = mapped_column(default=dict)
    started_by_actor: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))


class StepRun(Timestamped, Base):
    __tablename__ = "step_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "step_id"),
        Index("ix_step_runs_ready", "status", "next_attempt_at"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"))
    step_id: Mapped[str] = mapped_column(String(100))
    kind: Mapped[StepKind] = mapped_column(_enum(StepKind, "step_kind"))
    status: Mapped[StepStatus] = mapped_column(
        _enum(StepStatus, "step_status"), default=StepStatus.PENDING
    )
    paused_from: Mapped[StepStatus | None] = mapped_column(_enum(StepStatus, "step_paused_from"))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input: Mapped[dict[str, Any] | None]
    output: Mapped[dict[str, Any] | None]
    error: Mapped[dict[str, Any] | None]
    human_touched: Mapped[bool] = mapped_column(Boolean, default=False)
    subject_actions: Mapped[int] = mapped_column(Integer, default=0)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("decisions.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Timer(Timestamped, Base):
    __tablename__ = "timers"
    __table_args__ = (Index("ix_timers_due", "due_at", postgresql_where=text("fired_at IS NULL")),)

    step_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("step_runs.id"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purpose: Mapped[TimerPurpose] = mapped_column(_enum(TimerPurpose, "timer_purpose"))
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any] | None]


class IdempotencyLedger(Timestamped, Base):
    __tablename__ = "idempotency_ledger"

    key: Mapped[str] = mapped_column(String(64), unique=True)
    connector: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[LedgerStatus] = mapped_column(
        _enum(LedgerStatus, "ledger_status"), default=LedgerStatus.PENDING
    )
    external_id: Mapped[str | None] = mapped_column(String(200))
    response: Mapped[dict[str, Any] | None]
    first_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HumanTask(Timestamped, Base):
    __tablename__ = "human_tasks"
    __table_args__ = (Index("ix_human_tasks_queue", "status", "assignee_role"),)

    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("step_runs.id"))
    kind: Mapped[HumanTaskKind] = mapped_column(_enum(HumanTaskKind, "human_task_kind"))
    action: Mapped[str] = mapped_column(String(100))
    assignee_role: Mapped[str] = mapped_column(String(64))
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    context_packet: Mapped[dict[str, Any]] = mapped_column(default=dict)
    options: Mapped[list[str]] = mapped_column(default=list)
    status: Mapped[HumanTaskStatus] = mapped_column(
        _enum(HumanTaskStatus, "human_task_status"), default=HumanTaskStatus.OPEN
    )
    decision: Mapped[dict[str, Any] | None]
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(Base):
    """Output of authorize(); one row per call (PRD 12.1)."""

    __tablename__ = "decisions"

    request: Mapped[dict[str, Any]]
    effect: Mapped[Effect] = mapped_column(_enum(Effect, "effect"))
    approver_role: Mapped[str | None] = mapped_column(String(64))
    rule_ids: Mapped[list[str]] = mapped_column(default=list)
    policy_version: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(Text)


class KillSwitch(Timestamped, Base):
    __tablename__ = "kill_switches"
    __table_args__ = (UniqueConstraint("scope", "target"),)

    scope: Mapped[KillScope] = mapped_column(_enum(KillScope, "kill_scope"))
    target: Mapped[str] = mapped_column(String(100), default="*")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    set_by: Mapped[str] = mapped_column(String(200))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note: Mapped[str | None] = mapped_column(Text)


class PolicyVersion(Timestamped, Base):
    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("pack", "version"),)

    pack: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    tests_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_by: Mapped[str | None] = mapped_column(String(200))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PolicyDraft(Timestamped, Base):
    """An HR admin's unactivated edit of a pack. One per pack; deleted on activation."""

    __tablename__ = "policy_drafts"

    pack: Mapped[str] = mapped_column(String(64), unique=True)
    content: Mapped[str] = mapped_column(Text)
    tests: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(200))


class UploadLink(Timestamped, Base):
    """A signed one-time upload link. Only the random part is kept, never the signed token."""

    __tablename__ = "upload_links"

    nonce: Mapped[str] = mapped_column(String(64), unique=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("persons.id"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SlackPost(Timestamped, Base):
    """An approval card posted to Slack for a task, so a restart never posts it twice (PRD 16.2)."""

    __tablename__ = "slack_posts"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("human_tasks.id"), unique=True)
    channel: Mapped[str] = mapped_column(String(64))
    ts: Mapped[str | None] = mapped_column(String(32))


class SlackNotice(Timestamped, Base):
    """An SLA reminder posted to a team channel, keyed by its audit event (PRD 16.2)."""

    __tablename__ = "slack_notices"

    audit_seq: Mapped[int] = mapped_column(BigInteger, unique=True)
    channel: Mapped[str] = mapped_column(String(64))
    ts: Mapped[str | None] = mapped_column(String(32))


class Conversation(Timestamped, Base):
    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(16))  # slack | web
    external_ref: Mapped[str | None] = mapped_column(String(100))


class Message(Base):
    """Chat message; only the masked copy is stored."""

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    masked_text: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))


# ---------------------------------------------------------------- 10.3 audit and cost


class AuditEvent(Base):
    """Append-only, hash-chained audit log (PRD 6.6). Writer and verifier live in WP2."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_run", "run_id"),
        Index("ix_audit_events_subject", "subject_type", "subject_id"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_type: Mapped[ActorType] = mapped_column(_enum(ActorType, "actor_type"))
    actor_id: Mapped[str] = mapped_column(String(200))
    on_behalf_of: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(100))
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    effect: Mapped[str | None] = mapped_column(String(32))
    workflow_def_version: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[str | None] = mapped_column(String(500))
    skill_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(100))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    masked_payload_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reasoning: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class ModelPayload(Base):
    """Append-only record of exactly what left (or came back into) our infrastructure."""

    __tablename__ = "model_payloads"

    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    direction: Mapped[PayloadDirection] = mapped_column(
        _enum(PayloadDirection, "payload_direction")
    )
    masked_text: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer, default=0)


class CostEvent(Base):
    __tablename__ = "cost_events"
    __table_args__ = (
        Index("ix_cost_events_run", "run_id"),
        Index(
            "uq_cost_events_infra_alloc_run",
            "run_id",
            unique=True,
            postgresql_where=text("kind = 'infra_alloc'"),
        ),
    )

    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("step_runs.id"))
    kind: Mapped[CostKind] = mapped_column(_enum(CostKind, "cost_kind"))
    provider: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(100))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    unit_prices: Mapped[dict[str, Any]] = mapped_column(default=dict)
    cpu_seconds: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
