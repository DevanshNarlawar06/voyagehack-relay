"""Data carried across the model router's boundary, and the sinks it writes to."""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from relay.core.errors import TransientError
from relay.models.pii_gateway import PiiVault
from relay.models.recognizers import Entity

ConfidenceFn = Callable[[dict[str, Any]], float]


class ChatMessage(BaseModel):
    """One prompt message."""

    role: Literal["system", "user", "assistant"]
    content: str


class ModelRequest(BaseModel):
    """What a skill asks of the router.

    The confidence gate is chosen by the skill's kind (PRD 13.3): `confidence` scores extraction
    (for example the weighted share of fields passing validators), `classification_field` turns on
    self-consistency sampling for classification, and neither means generation, which has no gate.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    skill: str
    skill_version: str
    prompt_version: str
    messages: list[ChatMessage] = Field(min_length=1)
    output_schema: dict[str, Any]
    run_id: UUID | None = None
    step_run_id: UUID | None = None
    confidence: ConfidenceFn | None = None
    classification_field: str | None = None
    threshold: float | None = Field(default=None, ge=0, le=1)
    max_output_tokens: int | None = Field(default=None, gt=0)
    vault: PiiVault | None = None  # pass one to keep tokens stable across a run's calls
    # Values the system of record already knows, such as names, masked wherever they appear.
    known_values: list[tuple[Entity, str]] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """A fully masked call to a model provider."""

    model: str
    messages: list[dict[str, str]]
    temperature: float
    max_output_tokens: int
    output_schema: dict[str, Any]
    json_mode: bool


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """A provider's raw reply and token usage."""

    text: str
    tokens_in: int
    tokens_out: int


class ModelProvider(Protocol):
    """Anything that can answer a masked prompt."""

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Return the model's reply to `request`."""
        ...


@dataclass(frozen=True, slots=True)
class PayloadRecord:
    """One `model_payloads` row: what left our infrastructure, byte for byte, always masked."""

    direction: Literal["out", "in"]
    masked_text: str
    provider: str
    model_id: str
    region: str
    token_count: int
    run_id: UUID | None = None
    step_run_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class CostRecord:
    """One `cost_events` row of kind `llm`."""

    run_id: UUID | None
    step_run_id: UUID | None
    provider: str
    model_id: str
    tokens_in: int
    tokens_out: int
    unit_prices: dict[str, str]
    cost_inr: Decimal


class RouterSink(Protocol):
    """Where the router records payloads and costs. WP2 supplies the Postgres implementation."""

    def record_payload(self, payload: PayloadRecord) -> None:
        """Persist one masked payload."""
        ...

    def record_cost(self, cost: CostRecord) -> None:
        """Persist one cost event."""
        ...


@dataclass(slots=True)
class InMemorySink:
    """A sink that keeps rows in lists. For tests and for runs with no database."""

    payloads: list[PayloadRecord] = field(default_factory=list)
    costs: list[CostRecord] = field(default_factory=list)

    def record_payload(self, payload: PayloadRecord) -> None:
        """Keep the payload."""
        self.payloads.append(payload)

    def record_cost(self, cost: CostRecord) -> None:
        """Keep the cost event."""
        self.costs.append(cost)


@dataclass(frozen=True, slots=True)
class ModelResult:
    """A validated answer plus everything the audit event must stamp (PRD 6.10).

    `output` is unmasked, so it is hidden from repr to keep it out of logs.
    """

    output: dict[str, Any] = field(repr=False)
    masked_output: dict[str, Any]
    tier: str
    model_id: str
    skill_version: str
    prompt_version: str
    confidence: float | None
    escalated: bool
    cost_inr: Decimal
    payload_ids: list[UUID]


class ModelClient(Protocol):
    """What skills need from the router. They depend on this, so they never reach litellm."""

    def complete(self, request: ModelRequest) -> ModelResult:
        """Mask, call a model and return a validated answer."""
        ...

    def is_offline(self, tier: str) -> bool:
        """True if a tier is the deterministic mock."""
        ...


class ModelUnavailable(TransientError):
    """A provider failed. If every tier does, the engine retries, then parks (PRD 11.4)."""
