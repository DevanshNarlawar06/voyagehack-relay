"""Policy contract types (PRD 12.1)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Actor(_Model):
    """Who is asking. Agents also carry their skill allowlist (PRD 6.7)."""

    type: Literal["human", "agent", "system"]
    id: str
    roles: list[str] = Field(default_factory=list)
    allowed_actions: list[str] | None = None  # agents only: skill.allowed_actions


class SubjectRef(_Model):
    """What the action is about, e.g. `employee:<uuid>`."""

    type: str
    id: str


class ActionRequest(_Model):
    """One request to perform `action`. `attributes` carries computed facts (never raw PII)."""

    action: str
    actor: Actor
    on_behalf_of: str | None = None
    principal_roles: list[str] | None = None  # roles of the principal an agent acts for
    subject: SubjectRef | None = None
    resource: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    run_id: UUID | None = None
    step_id: str | None = None
    workflow: str | None = None
    skill: str | None = None
    connector: str | None = None


Effect = Literal["ALLOW", "REQUIRE_HUMAN", "DENY"]


class Decision(_Model):
    """Outcome of `authorize()`."""

    effect: Effect
    approver_role: str | None = None
    rule_ids: list[str] = Field(default_factory=list)
    reason: str
    policy_version: str

    @property
    def is_kill_switch(self) -> bool:
        """True when the deny came from a kill switch (the engine pauses instead of failing)."""
        return self.effect == "DENY" and "kill_switch" in self.rule_ids
