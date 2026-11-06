"""exception_analyst on human tasks (#60): summarised when it works, never blocking when it fails."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select

from relay.core.engine.engine import Engine
from relay.core.engine.ports import SkillOutput
from relay.db import models as m
from tests.simulation.harness import World, build_world
from tests.simulation.test_engine import _start, _tasks, _to_completion

pytestmark = pytest.mark.db


class _Analyst:
    name = "exception_analyst"

    def __init__(self, allowed: list[str], fail: bool = False) -> None:
        self.allowed_actions = allowed
        self.fail = fail
        self.seen: list[Mapping[str, Any]] = []

    def run(self, inputs: Mapping[str, Any], *, run_id: Any, step_run_id: Any) -> SkillOutput:
        self.seen.append(inputs)
        if self.fail:
            raise RuntimeError("model down")
        return SkillOutput(output={"summary": f"{inputs['step']}: {inputs['reason']}",
                                   "likely_cause": "x", "suggested_option": "retry"})  # fmt: skip


@pytest.fixture(scope="module")
def sa_engine(db_url: str) -> Iterator[SAEngine]:
    engine = create_engine(db_url, pool_size=5)
    yield engine
    engine.dispose()


def _with(world: World, analyst: _Analyst) -> World:
    deps = dataclasses.replace(world.engine.deps, skills={analyst.name: analyst})
    engine = Engine(world.sessions, deps, definitions=world.engine.definitions, worker_id="w1")
    return dataclasses.replace(world, engine=engine)


def test_a_conflict_task_carries_the_summary_and_the_analyst_saw_no_personal_data(
    sa_engine: SAEngine,
) -> None:
    analyst = _Analyst(["exception.analyse"])
    world = _with(build_world(sa_engine), analyst)
    world.directory.faults = ["conflict"]
    run_id = _start(world)
    _to_completion(world, run_id)
    (task,) = _tasks(world, run_id)
    assert task.context_packet["analysis"]["summary"].startswith("provision_identity")
    assert set(analyst.seen[0]) == {"kind", "reason", "fields", "step", "options", "facts"}
    with world.sessions() as s:
        decisions = [
            d
            for d in s.scalars(select(m.Decision))
            if d.request.get("action") == "exception.analyse"
        ]
    assert decisions and decisions[0].effect == "ALLOW"  # authorized like any step


def test_an_analyst_outside_its_allowlist_is_refused_and_the_task_still_opens(
    sa_engine: SAEngine,
) -> None:
    analyst = _Analyst(["comms.welcome"])  # not allowed to analyse
    world = _with(build_world(sa_engine), analyst)
    world.directory.faults = ["conflict"]
    run_id = _start(world)
    _to_completion(world, run_id)
    (task,) = _tasks(world, run_id)
    assert "analysis" not in task.context_packet and analyst.seen == []


def test_a_failing_analyst_never_blocks_the_task(sa_engine: SAEngine) -> None:
    world = _with(build_world(sa_engine), _Analyst(["exception.analyse"], fail=True))
    world.directory.faults = ["permanent"]
    run_id = _start(world)
    _to_completion(world, run_id)
    (task,) = _tasks(world, run_id)
    assert task.kind is m.HumanTaskKind.EXCEPTION and "analysis" not in task.context_packet
