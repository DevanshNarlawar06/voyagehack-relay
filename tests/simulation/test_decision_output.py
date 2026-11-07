"""A human step's result keeps who decided and what they chose, whatever output is sent (#69).

The API refuses undeclared output, but the engine must not rely on that: a later step (the
relieving letter) reads `decided_by`, so output can never overwrite it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select

from relay.db import models as m
from tests.simulation.harness import build_world

pytestmark = pytest.mark.db

WORKFLOW = """
name: decision_output_probe
version: 1
subject: employee
trigger: { event: probe.started }
context: { consented_purposes: "[]" }
steps:
  - id: accept
    kind: human
    action: employment.resignation.accept
    task: { assignee_role: manager, options: [accept] }
    outputs: { acceptance: "result" }
"""


def test_output_cannot_overwrite_who_decided_or_the_option(db_url: str) -> None:
    sa: SAEngine = create_engine(db_url)
    world = build_world(sa, WORKFLOW)
    with world.sessions.begin() as s:
        manager = m.User(email="mgr@probe.test", display_name="Mgr", roles=["manager"])
        s.add(manager)
        s.flush()
        manager_id = manager.id
    run_id = world.engine.start_run("decision_output_probe", {}, actor="test", subject_id="e1")
    world.engine.run_until_idle()
    with world.sessions() as s:
        task = s.scalar(select(m.HumanTask).where(m.HumanTask.run_id == run_id))
        assert task is not None
    world.engine.decide_task(
        task.id,
        user_id=manager_id,
        option="accept",
        output={"decided_by": "someone-else", "decision": "reject", "release_date": "2026-12-18"},
    )
    world.engine.run_until_idle()
    with world.sessions() as s:
        run = s.get(m.WorkflowRun, run_id)
        assert run is not None and run.status is m.RunStatus.COMPLETED
        acceptance = run.context["ctx"]["acceptance"]
    assert acceptance["decided_by"] == str(manager_id)
    assert acceptance["decision"] == "accept"
    assert acceptance["release_date"] == "2026-12-18"
    sa.dispose()
