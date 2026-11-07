"""What a person may add to a decision, per action (PRD 16.2, issue #69).

A human step's result is what the decider chose plus this output, and later steps read it (the
exit workflow reads an agreed release date). So output is never free-form for a reserved decision
or an approval: each action declares the fields it takes, and anything else is refused. Actions
not listed here take no output.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from relay.db import models as m


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResignationAcceptOutput(_Output):
    """The manager may agree an earlier release date, or a buyout of the notice period."""

    release_date: date | None = None
    buyout_agreed: bool = False

    @model_validator(mode="after")
    def _not_both(self) -> ResignationAcceptOutput:
        if self.release_date is not None and self.buyout_agreed:
            raise ValueError("agree a release date or a buyout, not both")
        return self


class _NoOutput(_Output):
    """This decision takes no extra fields."""


OUTPUTS: dict[str, type[_Output]] = {
    "employment.resignation.accept": ResignationAcceptOutput,
}
# Tasks whose output decides what later steps do. Conflict overrides keep their own rules.
_CHECKED = (m.HumanTaskKind.RESERVED_DECISION, m.HumanTaskKind.APPROVAL)


class OutputRefused(ValueError):
    """The output does not fit this decision."""


def fields_for(action: str) -> type[_Output] | None:
    """The output model an action takes, or None if it takes none."""
    return OUTPUTS.get(action)


def validate_output(task: m.HumanTask, output: Mapping[str, Any] | None) -> dict[str, Any]:
    """The output, checked and normalised for this task, or OutputRefused."""
    if task.kind not in _CHECKED:
        return dict(output or {})
    model = OUTPUTS.get(task.action, _NoOutput)
    try:
        parsed = model.model_validate(dict(output or {}))
    except ValidationError as exc:
        problems = "; ".join(e["msg"] for e in exc.errors())
        raise OutputRefused(f"This decision does not take that input: {problems}") from None
    return parsed.model_dump(mode="json", exclude_defaults=True)
