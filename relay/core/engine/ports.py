"""Ports: how the frozen core reaches rules, connectors, skills and notifications.

The core never imports concrete connectors or skills (import-linter contract). The worker builds
an `EngineDeps` from the real implementations; tests build one from fakes. Connector results are
duck-typed so `relay.connectors.base.ConnectorResult` fits without an import.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

RuleFn = Callable[..., Mapping[str, Any]]


class ConnectorResultLike(Protocol):
    external_id: str | None
    response: dict[str, Any]
    reconciled: bool
    duplicates: int
    cost_inr: Decimal


class OpSpecLike(Protocol):
    side_effect: bool


class ConnectorPort(Protocol):
    name: str

    def ops(self) -> Mapping[str, OpSpecLike]: ...

    async def call(
        self, op: str, payload: dict[str, Any], *, idempotency_key: str
    ) -> ConnectorResultLike: ...

    async def reconcile(
        self, op: str, idempotency_key: str, payload: dict[str, Any]
    ) -> ConnectorResultLike | None: ...


@dataclass(frozen=True)
class SkillOutput:
    """What a skill returns to the engine. Skills never call connectors (PRD 13.2)."""

    output: Mapping[str, Any]
    confidence: float | None = None
    skill_version: str | None = None
    prompt_version: str | None = None
    model_id: str | None = None
    reasoning: str | None = None


class SkillPort(Protocol):
    name: str
    allowed_actions: list[str]

    def run(self, inputs: Mapping[str, Any], *, run_id: Any, step_run_id: Any) -> SkillOutput: ...


class Notifier(Protocol):
    """Templated notifications for SLA reminders and escalations (no LLM, PRD 11.6)."""

    def notify(self, team: str, template: str, facts: Mapping[str, Any]) -> None: ...


class _NullNotifier:
    def notify(self, team: str, template: str, facts: Mapping[str, Any]) -> None:
        """Drop the notification (tests and offline simulation)."""


def utcnow() -> datetime:
    """Real clock."""
    return datetime.now(UTC)


@dataclass
class EngineDeps:
    """Everything the engine calls out to."""

    rules: Mapping[str, RuleFn] = field(default_factory=dict)
    connectors: Mapping[str, ConnectorPort] = field(default_factory=dict)
    skills: Mapping[str, SkillPort] = field(default_factory=dict)
    notifier: Notifier = field(default_factory=_NullNotifier)
    clock: Callable[[], datetime] = utcnow
    confidence_thresholds: Mapping[str, float] = field(default_factory=dict)

    def connector_op(self, uses: str) -> tuple[ConnectorPort, str]:
        """Resolve `connector.op`."""
        name, _, op = uses.partition(".")
        conn = self.connectors.get(name)
        if conn is None or op not in conn.ops():
            raise KeyError(f"unknown connector op {uses!r}")
        return conn, op
