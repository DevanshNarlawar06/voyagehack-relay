"""Model router (PRD 13.3): every model call goes through here, and every prompt is masked first.

This is the only module allowed to import litellm (PRD 6.5). Which model answers is decided by
`config/models.yaml` alone: `mock/*` models use the offline provider, anything else goes to LiteLLM.
"""

import json
import logging
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import jsonschema

from relay.config import ModelsConfig, Provider
from relay.core.cost.writer import llm_cost_inr
from relay.models.mock_provider import MockProvider
from relay.models.pii_gateway import PiiGateway, PiiVault
from relay.models.types import (
    CostRecord,
    LowConfidence,
    ModelProvider,
    ModelRequest,
    ModelResult,
    ModelUnavailable,
    PayloadRecord,
    ProviderRequest,
    ProviderResponse,
    RouterSink,
)

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.8
_CONSISTENCY_SAMPLES = 3
_CONSISTENCY_TEMPERATURE = 0.7
_MOCK_PREFIX = "mock/"
_REPAIR_INSTRUCTION = (
    "Your previous reply was not valid JSON for the required schema. "
    "Reply again with only a JSON object that matches this schema: "
)


class _LiteLLMProvider:
    """Calls any hosted model through LiteLLM. Keys come from the environment, never from code."""

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Send the masked prompt to the provider named in `request.model`."""
        import litellm  # heavy, and unused by the offline mock path

        kwargs: dict[str, Any] = {}
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = litellm.completion(
            model=request.model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_output_tokens,
            **kwargs,
        )
        usage = response.usage
        return ProviderResponse(
            response.choices[0].message.content or "", usage.prompt_tokens, usage.completion_tokens
        )


def _select_provider(config: Provider) -> ModelProvider:
    if config.litellm_model.startswith(_MOCK_PREFIX):
        return MockProvider()
    return _LiteLLMProvider()


def _parse(text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    """Return the reply as a schema-valid object, or None if it is not one."""
    try:
        value = json.loads(text)
        jsonschema.validate(value, schema)
    except (json.JSONDecodeError, jsonschema.ValidationError):
        return None
    return value if isinstance(value, dict) else None


@dataclass(slots=True)
class _Attempt:
    """The running totals of one `ModelRouter.complete` call."""

    cost: Decimal
    payload_ids: list[UUID]


@dataclass(frozen=True, slots=True)
class _TierOutcome:
    """A tier's parsed answer, in masked form, and its confidence. Output None means invalid."""

    masked: dict[str, Any] | None
    confidence: float | None


class ModelRouter:
    """Implements the routing algorithm of PRD 13.3."""

    def __init__(
        self,
        config: ModelsConfig,
        sink: RouterSink,
        gateway: PiiGateway | None = None,
    ) -> None:
        """Create a router over a models config, recording payloads and costs to `sink`."""
        self._config = config
        self._sink = sink
        self._gateway = gateway or PiiGateway()

    def complete(self, request: ModelRequest) -> ModelResult:
        """Mask, call tier1, gate on confidence, escalate to tier2 once, record everything.

        Raises LowConfidence if tier2 also falls short, and ModelUnavailable if no provider answers.
        Returns the output with PII tokens restored.
        """
        vault = request.vault if request.vault is not None else PiiVault()
        messages = [
            {"role": m.role, "content": self._gateway.mask(m.content, vault).text}
            for m in request.messages
        ]
        threshold = self._threshold(request)
        attempt = _Attempt(Decimal(0), [])
        failures = 0
        for tier in ("tier1", "tier2"):
            try:
                outcome = self._run_tier(tier, request, messages, attempt, vault)
            except ModelUnavailable:
                failures += 1
                continue
            accepted = outcome.masked is not None and (
                outcome.confidence is None or outcome.confidence >= threshold
            )
            if accepted and outcome.masked is not None:
                return self._result(
                    request, tier, outcome.masked, outcome.confidence, attempt, vault
                )
        if failures == 2:
            raise ModelUnavailable(f"no provider answered for skill {request.skill}")
        raise LowConfidence(f"skill {request.skill} stayed below threshold {threshold}")

    def _threshold(self, request: ModelRequest) -> float:
        configured = self._config.thresholds.get(request.skill)
        if configured is not None:
            return configured
        return request.threshold if request.threshold is not None else _DEFAULT_THRESHOLD

    def _run_tier(
        self,
        tier: str,
        request: ModelRequest,
        messages: list[dict[str, str]],
        attempt: _Attempt,
        vault: PiiVault,
    ) -> _TierOutcome:
        temperature = self._config.defaults.temperature
        masked = self._call_with_repair(tier, request, messages, temperature, attempt, vault)
        if masked is None:
            return _TierOutcome(None, None)
        if request.classification_field is not None:
            return self._self_consistency(tier, request, messages, masked, attempt, vault)
        if request.confidence is not None:
            return _TierOutcome(masked, request.confidence(self._unmask(masked, vault)))
        return _TierOutcome(masked, None)

    def _self_consistency(
        self,
        tier: str,
        request: ModelRequest,
        messages: list[dict[str, str]],
        first: dict[str, Any],
        attempt: _Attempt,
        vault: PiiVault,
    ) -> _TierOutcome:
        """Sample at a raised temperature. Confidence is the share agreeing with the majority."""
        field = request.classification_field
        assert field is not None
        samples = [first]
        for _ in range(_CONSISTENCY_SAMPLES - 1):
            sample = self._call(tier, request, messages, _CONSISTENCY_TEMPERATURE, attempt, vault)
            parsed = _parse(sample, request.output_schema)
            if parsed is not None:
                samples.append(parsed)
        votes = Counter(json.dumps(s.get(field), sort_keys=True) for s in samples)
        winner, count = votes.most_common(1)[0]
        chosen = next(s for s in samples if json.dumps(s.get(field), sort_keys=True) == winner)
        return _TierOutcome(chosen, count / _CONSISTENCY_SAMPLES)

    def _call_with_repair(
        self,
        tier: str,
        request: ModelRequest,
        messages: list[dict[str, str]],
        temperature: float,
        attempt: _Attempt,
        vault: PiiVault,
    ) -> dict[str, Any] | None:
        """Call once and, on invalid output at tier1, once more with a repair instruction."""
        reply = self._call(tier, request, messages, temperature, attempt, vault)
        parsed = _parse(reply, request.output_schema)
        if parsed is not None or tier != "tier1":
            return parsed
        instruction = _REPAIR_INSTRUCTION + json.dumps(request.output_schema)
        repair = [
            *messages,
            {"role": "assistant", "content": self._gateway.mask(reply, vault).text},
            {"role": "user", "content": instruction},
        ]
        reply = self._call(tier, request, repair, temperature, attempt, vault)
        return _parse(reply, request.output_schema)

    def _call(
        self,
        tier: str,
        request: ModelRequest,
        messages: list[dict[str, str]],
        temperature: float,
        attempt: _Attempt,
        vault: PiiVault,
    ) -> str:
        """Make one provider call, log both directions masked, and emit its cost event."""
        config = self._config.provider_for_tier(tier)
        name = self._config.tiers[tier]
        defaults = self._config.defaults
        outbound = json.dumps(messages, ensure_ascii=False)
        # Logged before the call: a request that fails may still have reached the provider.
        out_row = PayloadRecord(
            direction="out",
            masked_text=outbound,
            provider=name,
            model_id=config.litellm_model,
            region=config.region,
            token_count=max(1, len(outbound) // 4),  # estimate; the cost event has the real count
            run_id=request.run_id,
            step_run_id=request.step_run_id,
        )
        self._sink.record_payload(out_row)
        attempt.payload_ids.append(out_row.id)
        provider_request = ProviderRequest(
            model=config.litellm_model,
            messages=messages,
            temperature=temperature,
            max_output_tokens=request.max_output_tokens or defaults.max_output_tokens,
            output_schema=request.output_schema,
            json_mode=defaults.json_mode,
        )
        try:
            response = _select_provider(config).complete(provider_request)
        except Exception as exc:  # any provider failure means try the next tier
            # Only the type is logged, and the cause is dropped: its text can quote the prompt.
            logger.warning("model provider %s failed: %s", name, type(exc).__name__)
            raise ModelUnavailable(f"provider {name} failed") from None
        # A reply can repeat PII the model was never sent, so it is masked before being stored.
        masked_reply = self._gateway.mask(response.text, vault).text
        in_row = PayloadRecord(
            direction="in",
            masked_text=masked_reply,
            provider=name,
            model_id=config.litellm_model,
            region=config.region,
            token_count=response.tokens_out,
            run_id=request.run_id,
            step_run_id=request.step_run_id,
        )
        self._sink.record_payload(in_row)
        attempt.payload_ids.append(in_row.id)
        cost = llm_cost_inr(config.price_inr, response.tokens_in, response.tokens_out)
        attempt.cost += cost
        self._sink.record_cost(
            CostRecord(
                run_id=request.run_id,
                step_run_id=request.step_run_id,
                provider=name,
                model_id=config.litellm_model,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                unit_prices={
                    "in_per_1k": str(config.price_inr.in_per_1k),
                    "out_per_1k": str(config.price_inr.out_per_1k),
                },
                cost_inr=cost,
            )
        )
        return masked_reply

    def _unmask(self, value: Any, vault: PiiVault) -> Any:
        """Restore tokens in every string of a parsed reply."""
        if isinstance(value, str):
            return self._gateway.unmask(value, vault)
        if isinstance(value, list):
            return [self._unmask(item, vault) for item in value]
        if isinstance(value, dict):
            return {key: self._unmask(item, vault) for key, item in value.items()}
        return value

    def _result(
        self,
        request: ModelRequest,
        tier: str,
        masked: dict[str, Any],
        confidence: float | None,
        attempt: _Attempt,
        vault: PiiVault,
    ) -> ModelResult:
        return ModelResult(
            output=self._unmask(masked, vault),
            masked_output=masked,
            tier=tier,
            model_id=self._config.provider_for_tier(tier).litellm_model,
            skill_version=request.skill_version,
            prompt_version=request.prompt_version,
            confidence=confidence,
            escalated=tier != "tier1",
            cost_inr=attempt.cost,
            payload_ids=attempt.payload_ids,
        )
