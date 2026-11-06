"""Regex and checksum recognizers for Indian identifiers (PRD 14.1, 27.4).

The gateway fails towards masking: a false positive costs a model some context, a false negative
sends personal data to a third party. So a bare 12-digit number that fails Aadhaar's checksum is
still masked (as UAN, low score), and context only decides which label wins an overlap.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from relay.models.recognizers.base import Entity, Span
from relay.models.recognizers.checksums import verhoeff_is_valid

_CONTEXT_WINDOW = 32
_UAN_CONTEXT = re.compile(r"\buan\b|universal account|provident fund|\bepf\b|\bpf\b", re.IGNORECASE)
_DOB_CONTEXT = re.compile(r"\bd\.?o\.?b\b|date\s+of\s+birth|birth\s*date|\bborn\b", re.IGNORECASE)
_DATE_FORMATS = (
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
)
_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?"
    r"|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)


def _preceding_line(text: str, start: int) -> str:
    """Return up to the context window of text before `start`, stopping at a line break."""
    window = text[max(0, start - _CONTEXT_WINDOW) : start]
    return window.rsplit("\n", 1)[-1]


def _is_plausible_dob(value: str) -> bool:
    """Return True if `value` parses as a real date between 1900 and today."""
    normalized = " ".join(value.split())
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
        if date(1900, 1, 1) <= parsed <= date.today():
            return True
    return False


@dataclass(frozen=True, slots=True)
class RegexRecognizer:
    """Emits a span per regex match that passes the optional validator.

    `group` selects the capture group that holds the value when the pattern also matches a label.
    `score_fn` lets a recognizer score a match using the text around it.
    """

    entity: Entity
    pattern: re.Pattern[str]
    score: float
    group: int = 0
    validate: Callable[[str, str, int], bool] | None = None
    score_fn: Callable[[str, int], float] | None = None

    def analyze(self, text: str) -> list[Span]:
        """Return a span for every validated match in `text`."""
        spans: list[Span] = []
        for match in self.pattern.finditer(text):
            value = match.group(self.group)
            start = match.start(self.group)
            if self.validate is not None and not self.validate(value, text, start):
                continue
            score = self.score_fn(text, start) if self.score_fn is not None else self.score
            spans.append(Span(self.entity, start, match.end(self.group), score, value))
        return spans


def _aadhaar_is_valid(value: str, _text: str, _start: int) -> bool:
    return verhoeff_is_valid(re.sub(r"\D", "", value))


def _uan_score(text: str, start: int) -> float:
    return 0.98 if _UAN_CONTEXT.search(_preceding_line(text, start)) else 0.6


def _dob_is_valid(value: str, text: str, start: int) -> bool:
    return bool(_DOB_CONTEXT.search(_preceding_line(text, start))) and _is_plausible_dob(value)


PAN = RegexRecognizer(
    Entity.PAN,
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Za-z0-9])", re.IGNORECASE),
    0.95,
)
AADHAAR = RegexRecognizer(
    Entity.AADHAAR,
    re.compile(r"(?<!\d)[2-9]\d{3}([ -]?)\d{4}\1\d{4}(?!\d)"),
    0.95,
    validate=_aadhaar_is_valid,
)
MASKED_AADHAAR = RegexRecognizer(
    Entity.AADHAAR, re.compile(r"(?<![A-Za-z0-9*])[Xx*]{4}([ -]?)[Xx*]{4}\1\d{4}(?!\d)"), 0.9
)
IFSC = RegexRecognizer(
    Entity.IFSC, re.compile(r"(?<![A-Za-z0-9])[A-Z]{4}0[A-Z0-9]{6}(?![A-Za-z0-9])"), 0.9
)
UAN = RegexRecognizer(
    Entity.UAN, re.compile(r"(?<!\d)\d{4}([ -]?)\d{4}\1\d{4}(?!\d)"), 0.6, score_fn=_uan_score
)
MOBILE = RegexRecognizer(
    Entity.MOBILE,
    re.compile(r"(?<![\d+])(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"),
    0.8,
)
EMAIL = RegexRecognizer(
    Entity.EMAIL,
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"),
    0.99,
)
DOB = RegexRecognizer(
    Entity.DOB,
    re.compile(
        r"(?<!\d)(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{4}|\d{4}-\d{2}-\d{2}"
        rf"|\d{{1,2}}[\s-]+{_MONTH}[\s-]+\d{{4}}|{_MONTH}\s+\d{{1,2}},\s*\d{{4}})(?!\d)",
        re.IGNORECASE,
    ),
    0.9,
    validate=_dob_is_valid,
)
BANK_ACCOUNT = RegexRecognizer(
    Entity.BANK_ACCOUNT,
    re.compile(
        r"(?:\ba/?c\b|\bacct\b|\baccount\b)(?:\s*(?:no|num|number))?\.?\s*[:#\-]?\s*(\d{9,18})(?!\d)",
        re.IGNORECASE,
    ),
    0.97,
    group=1,
)

DEFAULT_RECOGNIZERS: tuple[RegexRecognizer, ...] = (
    PAN,
    AADHAAR,
    MASKED_AADHAAR,
    IFSC,
    UAN,
    MOBILE,
    EMAIL,
    DOB,
    BANK_ACCOUNT,
)
