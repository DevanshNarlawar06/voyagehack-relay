"""Recognizer contract shared by the regex recognizers and the later Presidio upgrade (PRD 27.4)."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class Entity(StrEnum):
    """PII categories the gateway masks. The value is the prefix of the mask token."""

    PAN = "PAN"
    AADHAAR = "AADHAAR"
    IFSC = "IFSC"
    UAN = "UAN"
    MOBILE = "MOBILE"
    EMAIL = "EMAIL"
    DOB = "DOB"
    BANK_ACCOUNT = "BANK_ACCOUNT"


@dataclass(frozen=True, slots=True)
class Span:
    """One detected PII value. `text` is excluded from repr so a logged span cannot leak it."""

    entity: Entity
    start: int
    end: int
    score: float
    text: str = field(repr=False)


class Recognizer(Protocol):
    """Finds the spans of one or more entities in a text."""

    def analyze(self, text: str) -> list[Span]:
        """Return every candidate span in `text`. Spans from different recognizers may overlap."""
        ...
