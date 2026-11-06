"""Deterministic offline provider (PRD 27.4): schema-valid output keyed on the masked prompt.

CI and the AI-disabled demo run on this, with token counts so cost accounting exercises every path.
"""

import hashlib
import json
from typing import Any

from relay.models.types import ProviderRequest, ProviderResponse

_MOCK_CONFIDENCE = 0.95


def _count_tokens(text: str) -> int:
    """Approximate tokens at four characters each, never zero."""
    return max(1, len(text) // 4)


def _generate(schema: dict[str, Any], seed: str, name: str) -> Any:
    """Build a value for `schema`, with choices derived from `seed` and the field path."""
    digest = hashlib.sha256(f"{seed}:{name}".encode()).hexdigest()
    pick = int(digest[:8], 16)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][pick % len(schema["enum"])]
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            return _generate(schema[keyword][0], seed, name)
    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for part in schema["allOf"]:
            merged = {**merged, **part}
        return _generate(merged, seed, name)
    kind = schema.get("type", "object" if "properties" in schema else "string")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    match kind:
        case "object":
            properties = schema.get("properties", {})
            required = schema.get("required", list(properties))
            return {
                key: _generate(properties.get(key, {}), seed, f"{name}.{key}") for key in required
            }
        case "array":
            count = max(schema.get("minItems", 1), 1)
            items = schema.get("items", {})
            return [_generate(items, seed, f"{name}[{i}]") for i in range(count)]
        case "number" | "integer":
            return _generate_number(schema, kind, name, pick)
        case "boolean":
            return bool(pick % 2)
        case "null":
            return None
        case _:
            text = f"mock {name.rsplit('.', 1)[-1]} {digest[:8]}"
            minimum, maximum = schema.get("minLength", 0), schema.get("maxLength")
            text = text.ljust(minimum, "x")
            return text[:maximum] if maximum is not None else text


def _generate_number(schema: dict[str, Any], kind: str, name: str, pick: int) -> float | int:
    # A hash-derived confidence would send a random share of mock runs to humans.
    if name.rsplit(".", 1)[-1] == "confidence":
        return _MOCK_CONFIDENCE
    low = schema.get("minimum", 0)
    high = schema.get("maximum", low + 100)
    if kind == "integer":
        return int(low + pick % (int(high - low) + 1))
    return float(low + (pick % 1000) / 1000 * (high - low))


class MockProvider:
    """Same masked prompt and schema gives the same reply. Temperature is ignored on purpose."""

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Return schema-valid JSON derived from a hash of the masked prompt."""
        prompt = json.dumps(request.messages, sort_keys=True)
        seed = hashlib.sha256(prompt.encode()).hexdigest()
        text = json.dumps(_generate(request.output_schema, seed, "$"), sort_keys=True)
        tokens_out = min(_count_tokens(text), request.max_output_tokens)
        return ProviderResponse(text, _count_tokens(prompt), tokens_out)
