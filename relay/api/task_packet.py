"""Turn the packet the engine stored on a task into what a person reads before deciding (#90).

The engine records raw facts: `reason`, `analysis`, `attributes`, `context_N`. Only named fields are
read here, never the packet as a whole, because it can hold ciphertext (`proposed.sealed`).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from relay.api.schemas import ContextPacketOut
from relay.core.engine.sealed import is_sealed
from relay.db import models as m

NO_SUMMARY = "No summary was recorded for this task."
_CONTEXT_PREFIX = "context_"


def _analysis(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    analysis = packet.get("analysis")
    return analysis if isinstance(analysis, Mapping) else {}


def suggested_option(packet: Mapping[str, Any]) -> str | None:
    """The option the analyst or the engine suggests, if any."""
    suggested = _analysis(packet).get("suggested_option") or packet.get("suggested_option")
    return str(suggested) if suggested else None


def _humanize(name: str) -> str:
    return name.replace(".", " ").replace("_", " ").strip().capitalize()


def _display(value: Any) -> str | None:
    """A value a person can read, or None for one that should not be shown."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str | int | float):
        return str(value)
    return None


def _flatten(values: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, str]]:
    for key, value in values.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping) and not is_sealed(value):
            yield from _flatten(value, f"{name} ")
        elif (shown := _display(value)) is not None:
            yield _humanize(name), shown


def _context_facts(packet: Mapping[str, Any], names: Sequence[str]) -> Iterator[tuple[str, str]]:
    keys = [k for k in packet if k.startswith(_CONTEXT_PREFIX)]
    for key in sorted(keys, key=lambda k: int(k[len(_CONTEXT_PREFIX) :])):
        index = int(key[len(_CONTEXT_PREFIX) :])
        shown = _display(packet[key])
        if shown is None:
            continue
        label = names[index].removeprefix("ctx.") if index < len(names) else None
        yield (_humanize(label) if label else f"Detail {index + 1}"), shown


def _facts(packet: Mapping[str, Any], names: Sequence[str]) -> dict[str, str]:
    facts: dict[str, str] = {}
    stored = packet.get("facts")
    if isinstance(stored, Mapping):
        facts.update({str(k): str(v) for k, v in stored.items()})
    fields = packet.get("fields")
    if isinstance(fields, list) and fields:
        facts["Fields in question"] = ", ".join(str(f) for f in fields)
    for key in ("candidate_ref", "attempts", "confidence"):
        if (shown := _display(packet.get(key))) is not None:
            facts[_humanize(key)] = shown
    attributes = packet.get("attributes")
    if isinstance(attributes, Mapping):
        facts.update(_flatten(attributes))
    proposed = packet.get("proposed")
    # `fields` is masked for people; its sibling `sealed` is ciphertext and is never read.
    if isinstance(proposed, Mapping) and isinstance(proposed.get("fields"), Mapping):
        facts.update(_flatten(proposed["fields"]))
    facts.update(_context_facts(packet, names))
    return facts


def _rule(packet: Mapping[str, Any]) -> str | None:
    rule_ids = packet.get("rule_ids")
    if isinstance(rule_ids, list) and rule_ids:
        return ", ".join(str(r) for r in rule_ids)
    rule = packet.get("rule")
    return str(rule) if rule else None


def describe(task: m.HumanTask, context_names: Sequence[str]) -> tuple[str, ContextPacketOut]:
    """Return a task's title and packet. `context_names` are its step's `context_from` expressions."""
    packet = task.context_packet or {}
    analysis = _analysis(packet)
    recorded = packet.get("summary") or packet.get("reason") or packet.get("policy_reason")
    summary = analysis.get("summary") or recorded or NO_SUMMARY
    facts = _facts(packet, context_names)
    if analysis.get("summary") and recorded and str(recorded) not in str(summary):
        facts["Recorded reason"] = str(recorded)
    title = packet.get("title") or f"{_humanize(task.kind.value)}: {_humanize(task.action).lower()}"
    return str(title), ContextPacketOut(
        summary=str(summary),
        from_analysis=bool(analysis.get("summary")),
        likely_cause=analysis.get("likely_cause") or packet.get("likely_cause"),
        suggested_option=suggested_option(packet),
        facts=facts,
        rule=_rule(packet),
    )
