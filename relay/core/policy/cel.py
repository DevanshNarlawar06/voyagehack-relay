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
