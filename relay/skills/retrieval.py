"""Policy retrieval for `helpdesk` (PRD 13.2).

Chunks are generated from `labour_rules.yaml`, so an answer can never disagree with the rules the
engine applies. Scoring is lexical BM25: no model download and no vectors. A pgvector retriever can
replace it behind `Retriever`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from relay.config import REPO_ROOT

LABOUR_RULES = REPO_ROOT / "definitions" / "policies" / "labour_rules.yaml"

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "am",
        "do",
        "does",
        "did",
        "i",
        "me",
        "my",
        "we",
        "you",
        "your",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "and",
        "or",
        "how",
        "what",
        "when",
        "many",
        "much",
        "can",
        "will",
        "would",
        "should",
        "get",
        "have",
        "has",
        "be",
        "it",
        "this",
        "that",
        "with",
        "from",
    ]
)
_SYNONYMS = {
    "leaves": "leave",
    "holidays": "leave",
    "vacation": "leave",
    "pf": "provident",
    "epf": "provident",
    "fnf": "settlement",
    "resign": "notice",
    "resigning": "notice",
    "quit": "notice",
    "salary": "wages",
    "dues": "settlement",
}
_K1 = 1.5
_B = 0.75


@dataclass(frozen=True)
class Chunk:
    """A citable piece of policy."""

    id: str
    title: str
    text: str
    source: str


@dataclass(frozen=True)
class Hit:
    """A chunk with its score: the share of the question it covers, 0 to 1."""

    chunk: Chunk
    score: float


class Retriever(Protocol):
    """Finds policy text for a question."""

    def search(self, query: str, limit: int = 3) -> list[Hit]:
        """Return the best chunks, best first."""
        ...


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9&]+", text.lower())
    return [_SYNONYMS.get(word, word) for word in words if word not in _STOPWORDS]


def chunks_from_labour_rules(pack: Mapping[str, Any]) -> list[Chunk]:
    """Turn the decision tables into sentences, each citing its source clause."""
    tables = pack["tables"]
    chunks: list[Chunk] = []

    def add(chunk_id: str, title: str, text: str, source: str) -> None:
        chunks.append(Chunk(chunk_id, title, text, source))

    probation = tables["probation_months_by_grade"]
    rows = ", ".join(f"{g}: {m} months" for g, m in probation["rows"].items() if g != "default")
    add(
        "probation.length",
        "Probation length",
        f"Probation period length by grade: {rows}. Any other grade: {probation['rows']['default']} months.",
        probation["source"],
    )
    notice = tables["notice_days"]
    rows = ", ".join(f"{g}: {d} days" for g, d in notice["by_grade"].items() if g != "default")
    add(
        "notice.period",
        "Notice period",
        f"Notice period when you resign, by grade: {rows}. During probation the notice period is "
        f"{notice['during_probation']} days.",
        notice["source"],
    )
    leave = tables["leave"]
    entitlement = ", ".join(
        f"{kind}: {days} days" for kind, days in leave["annual_entitlement_days"].items()
    )
    usable = "can" if leave["earned_leave_usable_during_probation"] else "cannot"
    add(
        "leave.entitlement",
        "Annual leave entitlement",
        f"Annual leave entitlement: {entitlement}. Leave accrual starts from your joining date.",
        leave["source"],
    )
    add(
        "leave.probation",
        "Leave during probation",
        f"During probation you {usable} use earned leave. Casual and sick leave can be used.",
        leave["source"],
    )
    add(
        "leave.notice",
        "Leave during notice",
        f"Leave during the notice period: {leave['leave_during_notice']}.",
        leave["source"],
    )
    settlement = tables["full_and_final"]
    add(
        "fnf.timeline",
        "Full and final settlement",
        f"Full and final settlement of wages due is paid within {settlement['settle_within_working_days']} "
        "working days of your last working day, after finance sign-off.",
        settlement["source"],
    )
    pf = tables["provident_fund"]
    add(
        "pf.contribution",
        "Provident fund",
        f"Provident fund (PF): employee contribution {pf['employee_rate']:.0%} and employer contribution "
        f"{pf['employer_rate']:.0%}. PF is mandatory up to a wage ceiling of ₹{pf['mandatory_wage_ceiling_inr']:,}.",
        pf["source"],
    )
    gratuity = tables["gratuity"]
    add(
        "gratuity.eligibility",
        "Gratuity",
        f"Gratuity is payable after {gratuity['min_continuous_service_years']} years of continuous service. "
        f"Amount: last drawn basic and DA x 15 / 26 x completed years, capped at ₹{gratuity['ceiling_inr']:,}.",
        gratuity["source"],
    )
    return chunks


class LexicalRetriever:
    """BM25 over policy chunks."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        """Index `chunks`."""
        self._chunks = list(chunks)
        # Title words count twice: a passage titled "Probation length" is about probation length.
        self._docs = [Counter(_tokens(f"{c.title} {c.title} {c.text}")) for c in self._chunks]
        self._lengths = [sum(doc.values()) for doc in self._docs]
        self._average = sum(self._lengths) / len(self._lengths) if self._lengths else 1.0
        total = len(self._docs)
        terms = {term for doc in self._docs for term in doc}
        self._idf = {
            term: math.log(
                1
                + (total - sum(term in doc for doc in self._docs) + 0.5)
                / (sum(term in doc for doc in self._docs) + 0.5)
            )
            for term in terms
        }

    def search(self, query: str, limit: int = 3) -> list[Hit]:
        """Rank with BM25, and score each hit by how much of the question it covers.

        BM25 ranks well but its size means little across queries, so it cannot carry a floor.
        Coverage can: the share of the question's weight found in the passage, where a word the
        policies never mention counts fully against the match. "Wifi password" scores 0.
        """
        terms = _tokens(query)
        if not terms or not self._docs:
            return []
        average_idf = sum(self._idf.values()) / len(self._idf)
        total_weight = sum(self._idf.get(term, average_idf) for term in terms)
        ranked: list[tuple[float, Hit]] = []
        for chunk, doc, length in zip(self._chunks, self._docs, self._lengths, strict=True):
            bm25 = 0.0
            covered = 0.0
            for term in terms:
                frequency = doc.get(term, 0)
                if frequency:
                    covered += self._idf[term]
                    denominator = frequency + _K1 * (1 - _B + _B * length / self._average)
                    bm25 += self._idf[term] * frequency * (_K1 + 1) / denominator
            if bm25 > 0:
                ranked.append((bm25, Hit(chunk, min(covered / total_weight, 1.0))))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [hit for _, hit in ranked[:limit]]


def default_retriever(path: Path = LABOUR_RULES) -> LexicalRetriever:
    """Build the retriever from the shipped labour rules."""
    with path.open(encoding="utf-8") as handle:
        return LexicalRetriever(chunks_from_labour_rules(yaml.safe_load(handle)))
