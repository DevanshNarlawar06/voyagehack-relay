"""The threshold maths, by hand, and that the shipped sets meet PRD 13.4's sizes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.run import Row, choose_threshold, evaluate_threshold

EVALS = Path("evals")


def test_expected_cost_formula() -> None:
    rows = [
        Row(0.9, True, 0.1),
        Row(0.9, False, 0.1),
        Row(0.6, True, 0.1),
        Row(0.1, False, 0.1),
    ]
    point = evaluate_threshold(rows, 0.5, c_error=100, c_human=10)
    # Accept 3 of 4, 1 error among them; 1 of 4 to a person; model cost 0.1 each.
    assert point.accepted == 0.75
    assert point.error_when_accepted == pytest.approx(1 / 3)
    assert point.to_human == 0.25
    assert point.expected_cost_inr == pytest.approx(0.75 * (1 / 3) * 100 + 0.25 * 10 + 0.1)


def test_nothing_accepted_has_no_error_rate() -> None:
    point = evaluate_threshold([Row(0.1, True)], 0.5, 100, 10)
    assert point.accepted == 0
    assert point.error_when_accepted == 0
    assert point.expected_cost_inr == 10


def test_choice_respects_the_error_limit_even_when_a_riskier_point_is_cheaper() -> None:
    rows = [Row(0.6, False)] + [Row(0.9, True)] * 9
    points = [evaluate_threshold(rows, t, c_error=1, c_human=50) for t in (0.5, 0.8)]
    assert points[0].expected_cost_inr < points[1].expected_cost_inr
    chosen = choose_threshold(points)
    assert chosen is not None
    assert chosen.threshold == 0.8


def test_ties_prefer_the_lower_threshold() -> None:
    rows = [Row(0.9, True)]
    chosen = choose_threshold([evaluate_threshold(rows, t, 1, 1) for t in (0.5, 0.6, 0.7)])
    assert chosen is not None
    assert chosen.threshold == 0.5


def test_no_threshold_is_chosen_when_none_meets_the_limit() -> None:
    rows = [Row(0.99, False)]
    assert choose_threshold([evaluate_threshold(rows, 0.5, 1, 1)]) is None


def test_accepting_nothing_is_not_a_valid_choice() -> None:
    assert choose_threshold([evaluate_threshold([Row(0.1, True)], 0.5, 1, 1)]) is None


@pytest.mark.parametrize(
    ("skill", "minimum"), [("intake", 150), ("document_extractor", 100), ("helpdesk", 60)]
)
def test_sets_are_big_enough(skill: str, minimum: int) -> None:
    lines = (EVALS / skill / "labelled.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= minimum
    assert all(json.loads(line) for line in lines)


def test_the_report_exists_and_says_what_it_measured() -> None:
    report = (EVALS / "threshold_report.md").read_text(encoding="utf-8")
    assert "Model provider" in report
    assert "t\\*" in report
    assert (EVALS / "threshold_curves.png").stat().st_size > 1000
