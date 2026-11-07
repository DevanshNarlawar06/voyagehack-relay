"""Workflow DSL (PRD 11.1): typed model, duration parsing and parallel-block flattening.

The Pydantic models are the source of truth; `relay/core/dsl/schema/workflow.schema.json` is
generated from them (`relay validate --write-schema`) for editors and reviewers.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_duration(text: str) -> timedelta:
    """Parse `45s`, `30m`, `24h`, `2d`."""
    m = _DURATION.match(text.strip())
    if not m:
        raise ValueError(f"bad duration {text!r}; use e.g. 30m, 24h, 2d")
    return timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetryPolicy(_Model):
    max_attempts: int = Field(default=5, ge=1)
    backoff: Literal["exponential", "fixed"] = "exponential"
    base_seconds: float = Field(default=10, gt=0)
    max_seconds: float = Field(default=600, gt=0)

    def delay(self, attempt: int) -> timedelta:
        """Delay before retry number `attempt` (1-based)."""
        if self.backoff == "fixed":
            return timedelta(seconds=self.base_seconds)
        return timedelta(seconds=min(self.max_seconds, self.base_seconds * 2 ** (attempt - 1)))


class Defaults(_Model):
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: int = Field(default=120, gt=0)


class Trigger(_Model):
    event: str
    require_decision: str | None = None  # a decided human task for this action must exist


class Sla(_Model):
    """Chasing (PRD 11.6): reminders to the owning team, then escalation to a human queue."""

    fulfil_within: str | None = None
    remind_at: list[str] = Field(default_factory=list)
    escalate_after: str | None = None
    escalate_to: str | None = None

    @model_validator(mode="after")
    def _durations(self) -> Sla:
        for d in [self.fulfil_within, self.escalate_after, *self.remind_at]:
            if d is not None:
                parse_duration(d)
        if (self.escalate_after or self.fulfil_within) and not self.escalate_to:
            raise ValueError("an SLA that escalates needs escalate_to")
        return self

    @property
    def escalate_delay(self) -> timedelta | None:
        """When to escalate: `escalate_after`, else `fulfil_within`."""
        d = self.escalate_after or self.fulfil_within
        return parse_duration(d) if d else None


class HumanTaskSpec(_Model):
    assignee_role: str
    assignee_user: str | None = None  # CEL
    options: list[str] = Field(min_length=1)
    context_from: list[str] = Field(default_factory=list)  # CEL expressions


StepKind = Literal["rule", "connector", "skill", "human", "wait"]


class Step(_Model):
    """One step. `inputs`/`attributes`/`outputs` values are CEL over {ctx, trigger, result, now}."""

    id: str
    kind: StepKind
    action: str
    uses: str | None = None
    needs: list[str] = Field(default_factory=list)
    when: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, str] = Field(default_factory=dict)  # computed facts for authorize()
    outputs: dict[str, str] = Field(default_factory=dict)  # ctx.<key> = <expr over result>
    sla: Sla | None = None
    on_failure: Literal["park", "fail"] = "park"
    retry: RetryPolicy | None = None
    task: HumanTaskSpec | None = None
    until: str | None = None  # wait: CEL timestamp
    for_subject_action: str | None = None  # wait: subject action name

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError(f"step id {v!r} must be snake_case")
        return v

    @model_validator(mode="after")
    def _kind_fields(self) -> Step:
        k = self.kind
        if k in ("rule", "connector", "skill") and not self.uses:
            raise ValueError(f"{self.id}: {k} steps need `uses`")
        if k == "human" and self.task is None:
            raise ValueError(f"{self.id}: human steps need `task`")
        if k == "wait" and (self.until is None) == (self.for_subject_action is None):
            raise ValueError(
                f"{self.id}: wait steps need exactly one of `until`, `for_subject_action`"
            )
        if k != "wait" and (self.until or self.for_subject_action):
            raise ValueError(f"{self.id}: `until`/`for_subject_action` are only for wait steps")
        if k != "human" and self.task is not None:
            raise ValueError(f"{self.id}: `task` is only for human steps")
        return self


class ParallelBody(_Model):
    id: str
    needs: list[str] = Field(default_factory=list)
    branches: list[list[Step]] = Field(min_length=1)


class ParallelBlock(_Model):
    parallel: ParallelBody


class ChildRun(_Model):
    """A linked run started when this one completes (PRD 19: probation as a child run).

    `payload` values are CEL over the finished run (`ctx`, `trigger`, `now`, `run_id`). The child
    starts in the same transaction that completes the parent, so the hand-off cannot be lost.
    """

    workflow: str
    payload: dict[str, str] = Field(default_factory=dict)


class WorkflowDefinition(_Model):
    name: str
    version: int = Field(ge=1)
    subject: str
    description: str | None = None
    trigger: Trigger
    context: dict[str, str] = Field(default_factory=dict)
    defaults: Defaults = Field(default_factory=Defaults)
    steps: list[Step | ParallelBlock] = Field(min_length=1)
    on_complete: list[ChildRun] = Field(default_factory=list)

    def flat_steps(self) -> list[Step]:
        """Every step, with parallel blocks expanded and block aliases resolved in `needs`.

        Inside a branch, a step without `needs` depends on the previous step of that branch (the
        first one on the block's `needs`). A `needs` entry naming a block means "all its steps".
        """
        aliases: dict[str, list[str]] = {}
        raw: list[Step] = []
        for item in self.steps:
            if isinstance(item, Step):
                raw.append(item)
                continue
            block = item.parallel
            members: list[str] = []
            for branch in block.branches:
                prev: str | None = None
                for step in branch:
                    needs = step.needs or ([prev] if prev else list(block.needs))
                    raw.append(step.model_copy(update={"needs": needs}))
                    members.append(step.id)
                    prev = step.id
            aliases[block.id] = members
        out: list[Step] = []
        for step in raw:
            expanded: list[str] = []
            for n in step.needs:
                expanded.extend(aliases.get(n, [n]))
            out.append(step.model_copy(update={"needs": list(dict.fromkeys(expanded))}))
        return out

    def step(self, step_id: str) -> Step:
        """Look up a step by id."""
        for s in self.flat_steps():
            if s.id == step_id:
                return s
        raise KeyError(step_id)


def parse_definition(text: str) -> WorkflowDefinition:
    """Parse YAML text into a definition (schema only; see the registry for full validation)."""
    data: Any = yaml.safe_load(text)
    return WorkflowDefinition.model_validate(data)


def definition_hash(text: str) -> str:
    """sha256 of the YAML source."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
