"""Masks PII before text leaves our infrastructure and restores it in replies (PRD 14.1).

The token mapping lives only in a `PiiVault`, in process memory, for the duration of the run's
call. The vault refuses to be pickled and hides its contents from repr, so it cannot reach a queue,
a cache or a log by accident.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import NoReturn, SupportsIndex

from relay.models.recognizers import DEFAULT_RECOGNIZERS, Entity, Recognizer, Span

_TOKEN = re.compile(r"<[A-Z_]+_\d+>")


def _normalize(entity: Entity, value: str) -> str:
    """Reduce a value to its identity, so two spellings of one number share a token."""
    if entity is Entity.MOBILE:
        return re.sub(r"\D", "", value)[-10:]
    if entity in (Entity.AADHAAR, Entity.UAN, Entity.BANK_ACCOUNT):
        return re.sub(r"[\s-]", "", value).upper()
    return " ".join(value.split()).upper()


class PiiVault:
    """In-memory token map for one run. Create one per run and let it be garbage collected."""

    def __init__(self) -> None:
        """Create an empty vault."""
        self._tokens: dict[tuple[Entity, str], str] = {}
        self._values: dict[str, str] = {}
        self._counts: Counter[Entity] = Counter()

    def token_for(self, entity: Entity, value: str) -> str:
        """Return the stable token for `value`, allocating the next `<ENTITY_n>` if it is new."""
        key = (entity, _normalize(entity, value))
        token = self._tokens.get(key)
        if token is None:
            self._counts[entity] += 1
            token = f"<{entity.value}_{self._counts[entity]}>"
            self._tokens[key] = token
            self._values[token] = value
        return token

    def value_of(self, token: str) -> str | None:
        """Return the original value behind `token`, or None if this vault did not issue it."""
        return self._values.get(token)

    def __repr__(self) -> str:
        """Describe the vault without revealing any value."""
        return f"PiiVault(tokens={len(self._values)})"

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        """Refuse serialization: PRD 14.1 says the mapping is never persisted."""
        raise TypeError("PiiVault must stay in process memory and cannot be serialized")


@dataclass(frozen=True, slots=True)
class MaskResult:
    """Masked text plus per-entity counts, safe to log and to store in `model_payloads`."""

    text: str
    entity_counts: dict[Entity, int]


def resolve_overlaps(spans: Iterable[Span]) -> list[Span]:
    """Keep the best non-overlapping spans (higher score, then longer) in document order."""
    kept: list[Span] = []
    for span in sorted(spans, key=lambda s: (-s.score, s.start - s.end, s.start)):
        if all(span.end <= other.start or span.start >= other.end for other in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)


class PiiGateway:
    """Applies recognizers to outbound text and reverses the tokens in inbound text."""

    def __init__(self, recognizers: Sequence[Recognizer] = DEFAULT_RECOGNIZERS) -> None:
        """Use the given recognizers; the default is the offline regex and checksum set."""
        self._recognizers = recognizers

    def with_recognizer(self, recognizer: Recognizer) -> "PiiGateway":
        """Return a gateway that also applies `recognizer`."""
        return PiiGateway((*self._recognizers, recognizer))

    def analyze(self, text: str) -> list[Span]:
        """Return the non-overlapping PII spans in `text`, in document order."""
        return resolve_overlaps(s for r in self._recognizers for s in r.analyze(text))

    def mask(self, text: str, vault: PiiVault) -> MaskResult:
        """Replace every PII span in `text` with its vault token."""
        parts: list[str] = []
        counts: Counter[Entity] = Counter()
        cursor = 0
        for span in self.analyze(text):
            parts += [text[cursor : span.start], vault.token_for(span.entity, span.text)]
            counts[span.entity] += 1
            cursor = span.end
        parts.append(text[cursor:])
        return MaskResult("".join(parts), dict(counts))

    def unmask(self, text: str, vault: PiiVault) -> str:
        """Restore tokens this vault issued. Unknown tokens, such as model inventions, stay as is."""
        return _TOKEN.sub(lambda m: vault.value_of(m.group(0)) or m.group(0), text)
