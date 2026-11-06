"""Connector protocol and result types (PRD 15.1)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, Field

from relay.connectors.manifest import OpSpec


class ConnectorResult(BaseModel):
    """A completed operation. `response` may hold personal data: never log it.

    `reconciled` means an earlier attempt was found rather than a new call being made. `duplicates`
    counts extra records sharing our reference: a system without idempotency received a retry, and
    the engine should audit that.
    """

    external_id: str | None = None
    response: dict[str, Any] = Field(default_factory=dict)
    reconciled: bool = False
    replayed: bool = False
    duplicates: int = 0
    cost_inr: Decimal = Decimal(0)


class HealthStatus(BaseModel):
    """Whether a connector's system is answering, for the Controls screen."""

    connector: str
    healthy: bool
    status_code: int | None = None
    latency_ms: float | None = None
    detail: str | None = None


class Connector(Protocol):
    """Every side effect in Relay goes through one of these, under the idempotency ledger."""

    name: str

    def ops(self) -> dict[str, OpSpec]:
        """Return the operations this connector declares."""
        ...

    async def call(
        self, op: str, payload: dict[str, Any], *, idempotency_key: str
    ) -> ConnectorResult:
        """Perform an operation. Raises a classified error from `relay.core.errors`."""
        ...

    async def reconcile(
        self, op: str, idempotency_key: str, payload: dict[str, Any]
    ) -> ConnectorResult | None:
        """Look for an earlier attempt. None means it did not land, so calling again is safe."""
        ...

    async def health(self) -> HealthStatus:
        """Check the external system. Never raises."""
        ...


class FaultControl(Protocol):
    """Standing faults on the mock systems, for the console's demo controls."""

    async def list(self) -> list[dict[str, Any]]:
        """Return the active fault rules."""
        ...

    async def add(self, system: str, fault: str) -> dict[str, Any]:
        """Start injecting `fault` into `system`."""
        ...

    async def clear(self) -> None:
        """Remove every fault rule."""
        ...
