"""Error taxonomy raised by connectors and skills and handled by the engine (PRD 11.4).

`reason` and `data` end up in `step_runs.error`, in logs and in human task packets, so they must
never quote payloads, responses or personal data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class RelayStepError(Exception):
    """Base class of every classified step failure."""

    def __init__(self, reason: str, *, data: Mapping[str, Any] | None = None) -> None:
        """Record a short reason and optional non-sensitive details."""
        super().__init__(reason)
        self.reason = reason
        self.data: dict[str, Any] = dict(data or {})


class TransientError(RelayStepError):
    """Might work if retried: retry with backoff, then park."""


class PermanentError(RelayStepError):
    """Will not work if retried: no retry, park or fail."""


class ConflictError(RelayStepError):
    """The data disagrees with the outside world: opens a `human_task(conflict)`.

    `fields` names what disagrees, for the conflict packet. It holds field names, not values.
    """

    def __init__(
        self,
        reason: str,
        *,
        fields: Sequence[str] = (),
        data: Mapping[str, Any] | None = None,
    ) -> None:
        """Record the reason, the conflicting field names and optional details."""
        super().__init__(reason, data=data)
        self.fields = list(fields)


class LowConfidence(RelayStepError):
    """A skill is not sure enough: router escalation, then `human_task(low_confidence)`."""


class PolicyDenied(RelayStepError):
    """`authorize()` said DENY: the step fails and the denial is audited."""
