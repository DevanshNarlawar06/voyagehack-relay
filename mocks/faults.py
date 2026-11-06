"""Fault injection: per request with `X-Fault`, or standing rules set on the control endpoint."""

import time
import uuid
from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, Field


class Fault(StrEnum):
    """Failure modes a mock can simulate.

    SERVICE_UNAVAILABLE: 503, no side effect.
    TIMEOUT: hangs, then 504, no side effect.
    TIMEOUT_AFTER_COMMIT: applies the side effect, then hangs and 504s: a lost acknowledgement.
    SLOW: delays, then behaves normally.
    DUPLICATE_ON_RETRY: ignores idempotency, so a retry creates a second resource.
    """

    SERVICE_UNAVAILABLE = "503"
    TIMEOUT = "timeout"
    TIMEOUT_AFTER_COMMIT = "timeout_after_commit"
    SLOW = "slow"
    DUPLICATE_ON_RETRY = "duplicate_on_retry"


class FaultRuleIn(BaseModel):
    """A standing fault. `*` matches any system or op. `remaining` and expiry are optional limits."""

    system: str = "*"
    op: str = "*"
    fault: Fault
    remaining: int | None = Field(default=None, ge=1)
    duration_seconds: float | None = Field(default=None, gt=0)


class FaultRule(FaultRuleIn):
    """A stored rule, with its id and resolved expiry."""

    id: str
    expires_at: float | None = None


class FaultController:
    """Holds the standing rules and resolves which fault, if any, applies to a request."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Start with no rules. `clock` is injectable for tests."""
        self._rules: dict[str, FaultRule] = {}
        self._clock = clock

    def add(self, rule: FaultRuleIn) -> FaultRule:
        """Store a rule and return it with its id."""
        expires = self._clock() + rule.duration_seconds if rule.duration_seconds else None
        stored = FaultRule(id=uuid.uuid4().hex[:8], expires_at=expires, **rule.model_dump())
        self._rules[stored.id] = stored
        return stored

    def list(self) -> list[FaultRule]:
        """Return the rules still in force."""
        self._expire()
        return list(self._rules.values())

    def remove(self, rule_id: str) -> bool:
        """Delete one rule. Returns False if it does not exist."""
        return self._rules.pop(rule_id, None) is not None

    def clear(self) -> None:
        """Delete every rule, ending any injected outage."""
        self._rules.clear()

    def resolve(self, system: str, op: str, header: str | None) -> Fault | None:
        """Return the fault for this request. The `X-Fault` header wins over standing rules.

        Raises ValueError if the header names an unknown fault.
        """
        if header:
            return Fault(header)
        self._expire()
        for rule in self._rules.values():
            if rule.system in ("*", system) and rule.op in ("*", op):
                if rule.remaining is not None:
                    rule.remaining -= 1
                    if rule.remaining == 0:
                        del self._rules[rule.id]
                return rule.fault
        return None

    def _expire(self) -> None:
        now = self._clock()
        for rule in list(self._rules.values()):
            if rule.expires_at is not None and rule.expires_at <= now:
                del self._rules[rule.id]
