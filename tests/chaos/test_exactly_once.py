"""WP4b acceptance (issue #5): workers hard-killed at the side-effect boundary, exactly once.

CI runs the acceptance size (RELAY_CHAOS_RUNS=100, about 3-4 minutes); locally the default is 20.
"""

from __future__ import annotations

import os

import pytest

from tests.chaos.chaos import SYSTEMS, run_chaos

pytestmark = pytest.mark.db


def test_hard_kills_at_the_side_effect_boundary_never_duplicate(db_url: str) -> None:
    runs = int(os.environ.get("RELAY_CHAOS_RUNS", "20"))
    report = run_chaos(db_url, runs=runs, workers=2, kill_rate=0.1, seed=2026)
    assert report.self_kills > 0 and report.parent_kills > 0, "the test must actually kill workers"
    assert report.completed == runs, report
    assert report.records == dict.fromkeys(SYSTEMS, runs), report
