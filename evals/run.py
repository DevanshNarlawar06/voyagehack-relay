"""Eval runner and threshold report (PRD 13.4).

Run: python evals/run.py           (uses config/models.yaml; set it to a real provider to re-measure)
Writes evals/threshold_report.md and evals/threshold_curves.png.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from relay.config import ModelsConfig, get_config
from relay.models.router import ModelRouter
from relay.models.types import InMemorySink
from relay.skills.document_extractor import DocumentExtractorSkill, open_sealed
from relay.skills.helpdesk import HelpdeskSkill
from relay.skills.intake import UNKNOWN, IntakeSkill
from relay.skills.retrieval import default_retriever

HERE = Path(__file__).parent
MAX_ERROR_WHEN_ACCEPTED = 0.02
SWEEP = [round(0.50 + i * 0.01, 2) for i in range(50)]  # 0.50 to 0.99, as PRD 13.4 specifies


@dataclass(frozen=True)
class Row:
    """One labelled example: how sure the skill was, and whether acting on it would be right."""

    confidence: float
    correct: bool
    cost_inr: float = 0.0


@dataclass(frozen=True)
class Point:
    """The measures at one threshold."""

    threshold: float
    accepted: float
    error_when_accepted: float
    to_human: float
    expected_cost_inr: float


def evaluate_threshold(rows: list[Row], t: float, c_error: float, c_human: float) -> Point:
    """Compute E(t) = P(accept)·P(error|accept)·C_error + P(human)·C_human + mean model cost.

    Tier 2 sits inside `cost_inr`: each row carries what its model calls really cost.
    """
    total = len(rows)
    accepted = [row for row in rows if row.confidence >= t]
    errors = sum(1 for row in accepted if not row.correct)
    p_accept = len(accepted) / total
    error_rate = errors / len(accepted) if accepted else 0.0
    p_human = 1 - p_accept
    model_cost = sum(row.cost_inr for row in rows) / total
    return Point(t, p_accept, error_rate, p_human, p_accept * error_rate * c_error + p_human * c_human + model_cost)


def choose_threshold(points: list[Point]) -> Point | None:
    """Return the cheapest point meeting the error limit, preferring the lower t in a tie."""
    allowed = [p for p in points if p.error_when_accepted <= MAX_ERROR_WHEN_ACCEPTED and p.accepted > 0]
    return min(allowed, key=lambda p: (round(p.expected_cost_inr, 6), p.threshold)) if allowed else None


def _load(skill: str) -> list[dict[str, Any]]:
    path = HERE / skill / "labelled.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _spent(sink: InMemorySink, already: Decimal) -> tuple[float, Decimal]:
    total = sum((c.cost_inr for c in sink.costs), Decimal(0))
    return float(total - already), total


def eval_intake(router: ModelRouter, sink: InMemorySink) -> list[Row]:
    """A wrong intent is an error. `unknown` goes to a person, so it scores as not accepted."""
    skill = IntakeSkill(router)
    rows: list[Row] = []
    spent = Decimal(0)
    for item in _load("intake"):
        out = skill.run({"text": item["text"]}, run_id=None, step_run_id=None)
        cost, spent = _spent(sink, spent)
        intent = out.output["intent"]
        confidence = 0.0 if intent == UNKNOWN else float(out.confidence or 0)
        rows.append(Row(confidence, intent == item["intent"], cost))
    return rows


def eval_documents(router: ModelRouter, sink: InMemorySink) -> list[Row]:
    """Right means every required field is present and equals the truth."""
    skill = DocumentExtractorSkill(router)
    rows: list[Row] = []
    spent = Decimal(0)
    for item in _load("document_extractor"):
        out = skill.run(
            {"documents": [{"category": item["category"], "text": item["text"], "ocr_confidence": item["ocr_confidence"]}]},
            run_id=None,
            step_run_id=None,
        )
        cost, spent = _spent(sink, spent)
        opened = open_sealed(out.output["sealed"][item["category"]])
        rows.append(Row(float(out.confidence or 0), opened == item["truth"], cost))
    return rows


def eval_helpdesk(router: ModelRouter, sink: InMemorySink) -> list[Row]:
    """Scores retrieval directly, since that is what the similarity floor controls.

    Answering an off-topic question is an error, however confident. Below the floor the question
    goes to HR, so it scores as not accepted.
    """
    del router, sink  # retrieval needs no model call
    retriever = default_retriever()
    rows: list[Row] = []
    for item in _load("helpdesk"):
        hits = retriever.search(item["question"])
        score = hits[0].score if hits else 0.0
        right = item["chunk"] is not None and bool(hits) and hits[0].chunk.id == item["chunk"]
        rows.append(Row(score, right))
    return rows


def _chart(results: dict[str, list[Point]], chosen: dict[str, Point | None], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    accent, ink, muted = "#4f46e5", "#18181b", "#5f5f69"
    fig, axes = plt.subplots(2, len(results), figsize=(4 * len(results), 5.4), sharex=True)
    for column, (name, points) in enumerate(results.items()):
        xs = [p.threshold for p in points]
        for row, (values, label) in enumerate(
            (
                ([p.expected_cost_inr for p in points], "Expected cost per item (INR)"),
                ([p.error_when_accepted * 100 for p in points], "Error when accepted (%)"),
            )
        ):
            axis = axes[row][column]
            axis.plot(xs, values, color=accent, linewidth=2)
            if chosen[name]:
                axis.axvline(chosen[name].threshold, color=muted, linewidth=1, linestyle="--")
            if row == 1:
                axis.axhline(MAX_ERROR_WHEN_ACCEPTED * 100, color=muted, linewidth=1, linestyle=":")
                axis.set_xlabel("Threshold t", color=muted)
            if column == 0:
                axis.set_ylabel(label, color=muted)
            if row == 0:
                axis.set_title(name, color=ink, fontsize=11)
            axis.tick_params(colors=muted, labelsize=8)
            axis.grid(axis="y", color="#e4e4e7", linewidth=0.8)
            axis.set_axisbelow(True)
            for side in ("top", "right"):
                axis.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _section(name: str, rows: list[Row], points: list[Point], best: Point | None, note: str) -> list[str]:
    lines = [f"## {name}", "", note, "", f"Labelled examples: {len(rows)}.", ""]
    if best is None:
        lines += ["**No threshold meets the error limit on this set.**", ""]
    else:
        lines += [
            f"**Chosen t\\* = {best.threshold:.2f}**: accepts {best.accepted:.1%} automatically, "
            f"with {best.error_when_accepted:.1%} errors among those, sends {best.to_human:.1%} "
            f"to a person, at an expected ₹{best.expected_cost_inr:.2f} per item.",
            "",
        ]
    lines += ["| t | Accepted | Error when accepted | To a person | E(t) INR |", "|---|---|---|---|---|"]
    for p in points:
        if round(p.threshold * 100) % 10 == 0 or p == best or p.threshold == 0.99:
            lines.append(
                f"| {p.threshold:.2f} | {p.accepted:.1%} | {p.error_when_accepted:.1%} | {p.to_human:.1%} | {p.expected_cost_inr:.2f} |"
            )
    return [*lines, ""]


def main(config: ModelsConfig | None = None) -> dict[str, Point | None]:
    """Run every eval, write the report and the chart, and return the chosen thresholds."""
    app = get_config()
    models = config or app.models
    c_error, c_human = app.app.costs.c_error_inr, app.app.costs.c_human_inr
    provider = models.provider_for_tier("tier1").litellm_model
    offline = provider.startswith("mock/")

    sink = InMemorySink()
    router = ModelRouter(models, sink)
    sets = {
        "intake": eval_intake(router, sink),
        "document_extractor": eval_documents(router, sink),
        "helpdesk": eval_helpdesk(router, sink),
    }
    results = {name: [evaluate_threshold(rows, t, c_error, c_human) for t in SWEEP] for name, rows in sets.items()}
    chosen = {name: choose_threshold(points) for name, points in results.items()}
    _chart(results, chosen, HERE / "threshold_curves.png")

    notes = {
        "intake": (
            "**Measures the keyword table only.** With no real model, a message the table misses becomes "
            "`unknown` and goes to a person, so confidence is either 1 or 0 and the curve is flat. "
            "**This does not justify a model threshold.** Re-run with a real provider."
            if offline
            else "Keyword table plus self-consistency sampling on the configured model."
        ),
        "document_extractor": (
            "Measures local parsing and validators, the path most documents take. Confidence is the weighted "
            "share of fields passing validators. "
            + ("No model fallback ran, so documents failing validators go to a person." if offline else "Includes model fallback.")
        ),
        "helpdesk": (
            "Measures retrieval, which is what the similarity floor controls. Confidence is the top passage's "
            "coverage of the question. Answer wording is not evaluated here. If no threshold meets the limit, the "
            "manifest floor is a judgement, not a measurement: errors here are questions answered from the wrong "
            "passage, which a score cannot catch because the wrong passage does contain the words."
        ),
    }
    report = [
        "# Threshold report",
        "",
        "Generated by `python evals/run.py` (PRD 13.4). Do not edit by hand.",
        "",
        f"- **Model provider:** `{provider}`" + (" (offline mock; **no real model has been evaluated**)" if offline else ""),
        f"- **Assumptions (`config/app.yaml`):** a person's time, C_human = ₹{c_human:.0f}; an uncaught error, C_error = ₹{c_error:.0f}.",
        f"- **Rule:** pick t\\* = argmin E(t), subject to P(error | accepted) ≤ {MAX_ERROR_WHEN_ACCEPTED:.0%}, for t from 0.50 to 0.99.",
        "- **Formula:** E(t) = P(accept)·P(error|accept)·C_error + P(human)·C_human + mean model cost per item.",
        "- **Data:** synthetic sets from `evals/generate_sets.py`, written by the same team as the skills. Treat these as a regression baseline, not as field accuracy. Reader confidence in the document set is an assumed distribution until real OCR runs on real scans.",
        "",
        "![Expected cost and error rate against threshold, per skill](threshold_curves.png)",
        "",
    ]
    for name, rows in sets.items():
        report += _section(name, rows, results[name], chosen[name], notes[name])
    (HERE / "threshold_report.md").write_text("\n".join(report), encoding="utf-8", newline="\n")
    return chosen


if __name__ == "__main__":
    for skill_name, point in main().items():
        print(skill_name, point)  # noqa: T201
