"""Request and response bodies. These mirror `web/lib/types.ts`, the console's contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class UserOut(BaseModel):
    """A person, without anything sensitive."""

    model_config = ConfigDict(from_attributes=True)

    email: str
    display_name: str
    roles: list[str]


class LoginIn(BaseModel):
    """Demo sign-in picks a seeded user."""

    email: str


class SessionOut(BaseModel):
    """A signed-in session."""

    token: str
    user: UserOut


class RunOut(BaseModel):
    """One row of the runs table."""

    id: UUID
    definition_name: str
    definition_version: int
    subject_label: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    steps_total: int
    steps_done: int
    cost_inr: Decimal

    @field_serializer("cost_inr")
    def _money(self, value: Decimal) -> str:
        return f"{value:.4f}"


class StepDecisionOut(BaseModel):
    """Why the policy engine let a step run, or did not."""

    effect: str
    rule_ids: list[str]
    policy_version: str
    reason: str


class StepOut(BaseModel):
    """One node of the run graph, with what explains it."""

    step_id: str
    kind: str
    status: str
    needs: list[str]
    attempt: int
    human_touched: bool
    started_at: datetime | None
    finished_at: datetime | None
    cost_inr: Decimal
    action: str | None
    decision: StepDecisionOut | None
    model_id: str | None
    prompt_version: str | None
    error: str | None

    @field_serializer("cost_inr")
    def _money(self, value: Decimal) -> str:
        return f"{value:.4f}"


class TimelineEventOut(BaseModel):
    """One audit event on a run."""

    seq: int
    ts: datetime
    actor_type: str
    actor_id: str
    action: str
    effect: str | None
    step_id: str | None
    reasoning: str | None


class RunDetailOut(RunOut):
    """A run with its graph and timeline."""

    steps: list[StepOut]
    timeline: list[TimelineEventOut]


class ContextPacketOut(BaseModel):
    """What a person needs to make a decision."""

    summary: str
    likely_cause: str | None = None
    suggested_option: str | None = None
    facts: dict[str, str] = Field(default_factory=dict)
    rule: str | None = None


class TaskOut(BaseModel):
    """A human task."""

    id: UUID
    run_id: UUID | None
    step_id: str | None
    kind: str
    action: str
    title: str
    assignee_role: str
    status: str
    options: list[str]
    sla_due_at: datetime | None
    context_packet: ContextPacketOut
    decision: str | None
    reason: str | None


class DecideIn(BaseModel):
    """A person's decision. Whether `reason` is required depends on the option chosen."""

    option: str
    reason: str = ""


class PolicySummaryOut(BaseModel):
    """A policy pack in the list."""

    pack: str
    version: int
    owner: str
    activated_by: str | None
    activated_at: datetime | None


class PolicyHistoryOut(BaseModel):
    """One stored version of a pack."""

    version: int
    activated_by: str | None
    activated_at: datetime | None


class PolicyOut(PolicySummaryOut):
    """A pack with its source, the current draft and its history."""

    content: str
    draft: str | None
    draft_tests: str | None
    history: list[PolicyHistoryOut]


class DraftIn(BaseModel):
    """An edited pack, and optionally its edited test cases."""

    content: str = Field(min_length=1)
    tests: str | None = None


class TestResultOut(BaseModel):
    """One policy test case."""

    __test__ = False  # not a pytest class

    name: str
    passed: bool
    detail: str | None


class RollbackIn(BaseModel):
    """Which stored version to make active again."""

    version: int = Field(ge=1)


class KillSwitchIn(BaseModel):
    """Set or release a kill switch."""

    scope: Literal["global", "workflow", "step", "skill", "connector"]
    target: str = "*"
    active: bool
    note: str | None = None


class KillSwitchOut(BaseModel):
    """A kill switch's state."""

    model_config = ConfigDict(from_attributes=True)

    scope: str
    target: str
    active: bool
    set_by: str
    set_at: datetime
    note: str | None


class FaultIn(BaseModel):
    """Start injecting a fault into a mock system."""

    system: str
    fault: Literal["503", "timeout", "timeout_after_commit", "slow", "duplicate_on_retry"]


class AuditEventOut(BaseModel):
    """One audit event, everything explainability needs (PRD 16.3)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    ts: datetime
    actor_type: str
    actor_id: str
    on_behalf_of: str | None
    action: str
    subject_type: str | None
    subject_id: str | None
    run_id: UUID | None
    step_run_id: UUID | None
    effect: str | None
    workflow_def_version: str | None
    policy_version: str | None
    skill_version: str | None
    prompt_version: str | None
    model_id: str | None
    input_hash: str | None
    masked_payload_ref: UUID | None
    reasoning: str | None
    data: dict[str, Any]
    prev_hash: str
    hash: str


class ModelPayloadOut(BaseModel):
    """What actually left our infrastructure, byte for byte, always masked."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    direction: str
    masked_text: str
    provider: str
    model_id: str | None
    region: str | None
    token_count: int


class AuditEventDetailOut(AuditEventOut):
    """An audit event plus the payload it links to."""

    payload: ModelPayloadOut | None


class DefinitionOut(BaseModel):
    """A workflow definition in the registry."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    version: int
    status: str
    content_hash: str
    activated_by: str | None
    activated_at: datetime | None


class EventIn(BaseModel):
    """A domain event, for example `candidate.offer_accepted`."""

    event: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class EventOut(BaseModel):
    """The runs an event started."""

    run_ids: list[UUID]


class EmployeeOut(BaseModel):
    """One row of the employees list. No 🔒 field appears in any employee response."""

    id: UUID
    full_name: str
    employee_code: str | None
    state: str
    role_title: str | None
    department: str | None


class LifecycleOut(BaseModel):
    """One lifecycle event."""

    id: UUID
    event_type: str
    at: datetime


class DocumentOut(BaseModel):
    """That a document exists, never what it says."""

    id: UUID
    category: str
    retention_until: date | None


class ConsentOut(BaseModel):
    """One consent."""

    purpose_id: str
    notice_version: str
    withdrawn_at: datetime | None


class EmployeeRequestOut(BaseModel):
    """One DPDP request on an employee's record."""

    id: UUID
    type: str
    status: str


class EmployeeDetailOut(EmployeeOut):
    """An employee's record."""

    lifecycle: list[LifecycleOut]
    documents: list[DocumentOut]
    consents: list[ConsentOut]
    data_requests: list[EmployeeRequestOut]


class DataRequestIn(BaseModel):
    """A DPDP request from the signed-in person about their own data."""

    type: Literal["access", "correction", "erasure"]


class DataRequestOut(BaseModel):
    """A DPDP request's state."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    run_id: UUID | None
