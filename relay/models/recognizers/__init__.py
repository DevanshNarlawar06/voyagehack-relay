"""India PII recognizers. The regex set is P0; Presidio plugs in behind `Recognizer` (PRD 27.4)."""

from relay.models.recognizers.base import Entity, Recognizer, Span
from relay.models.recognizers.checksums import verhoeff_check_digit, verhoeff_is_valid
from relay.models.recognizers.india import DEFAULT_RECOGNIZERS, known_value_recognizer

__all__ = [
    "DEFAULT_RECOGNIZERS",
    "Entity",
    "Recognizer",
    "Span",
    "known_value_recognizer",
    "verhoeff_check_digit",
    "verhoeff_is_valid",
]
