"""Safe CEL evaluation for policy conditions (PRD 8, 11.1).

Compiled programs are cached by expression text. Evaluation errors (for example a missing
attribute) are reported to the caller, which fails closed.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import celpy
from celpy.adapter import json_to_cel
from celpy.evaluation import CELEvalError

_ENV = celpy.Environment()


class CelError(ValueError):
    """An expression failed to compile or evaluate."""


@lru_cache(maxsize=1024)
def _program(expr: str) -> Any:
    try:
        return _ENV.program(_ENV.compile(expr))
    except Exception as exc:
        raise CelError(f"cannot compile CEL {expr!r}: {exc}") from exc


def check(expr: str) -> None:
    """Raise `CelError` if `expr` does not compile."""
    _program(expr)


def evaluate(expr: str, activation: dict[str, Any]) -> Any:
    """Evaluate `expr` against plain-Python `activation` values and return a Python value."""
    prog = _program(expr)
    try:
        result = prog.evaluate({k: json_to_cel(v) for k, v in activation.items()})
    except Exception as exc:
        raise CelError(f"cannot evaluate CEL {expr!r}: {exc}") from exc
    if isinstance(result, CELEvalError):
        raise CelError(f"cannot evaluate CEL {expr!r}: {result}")
    return result


def evaluate_bool(expr: str, activation: dict[str, Any]) -> bool:
    """Evaluate `expr` and require a boolean result."""
    result = evaluate(expr, activation)
    if not isinstance(result, celpy.celtypes.BoolType | bool):
        raise CelError(f"CEL {expr!r} returned {type(result).__name__}, expected bool")
    return bool(result)


def to_python(value: Any) -> Any:
    """Convert a CEL result to plain JSON-friendly Python (timestamps -> ISO strings)."""
    from datetime import UTC, datetime, timedelta

    from celpy import celtypes as t

    if value is None or isinstance(value, t.NullType):
        return None
    if isinstance(value, t.BoolType | bool):
        return bool(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, t.IntType | t.UintType | int):
        return int(value)
    if isinstance(value, t.DoubleType | float):
        return float(value)
    if isinstance(value, t.StringType | str):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_python(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_python(v) for v in value]
    return str(value)


def evaluate_py(expr: str, activation: dict[str, Any]) -> Any:
    """Evaluate `expr` and return plain Python."""
    return to_python(evaluate(expr, activation))
