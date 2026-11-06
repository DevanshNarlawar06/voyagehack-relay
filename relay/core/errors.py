"""Error taxonomy raised by connectors and skills and handled by the engine (PRD 11.4).

Messages must never quote payloads or responses: they end up in `step_runs.error` and in logs.
"""

from __future__ import annotations


class RelayStepError(Exception):
    """Base class of every classified step failure."""


class TransientError(RelayStepError):
    """Might work if retried: retry with backoff, then park."""


class PermanentError(RelayStepError):
    """Will not work if retried: no retry, park or fail."""


class ConflictError(RelayStepError):
    """The data disagrees with the outside world: opens a `human_task(conflict)`."""


class LowConfidence(RelayStepError):
    """A skill is not sure enough: router escalation, then `human_task(low_confidence)`."""


class PolicyDenied(RelayStepError):
    """`authorize()` said DENY: the step fails and the denial is audited."""
