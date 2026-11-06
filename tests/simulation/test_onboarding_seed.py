"""WP7 acceptance (issue #6): every PRD 22 seed scenario reaches its end state; automation >= 85%.

Real onboarding.v1.yaml, real rules, skills on the offline mock provider, real HttpConnectors to
the mock service in process, real Postgres. Only the clock is fake.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from mocks.app import MockSettings, create_app
from relay.bootstrap import build_engine
from relay.connectors.faults import HttpFaultControl
from relay.connectors.http import load_connectors
from relay.core.audit.verifier import verify_chain
from relay.db import models as m
from relay.seeding.generate import read
from relay.seeding.load import load
from relay.seeding.scenarios import ScenarioPlayer, SimulationReport
from tests.simulation.harness import FakeClock, clean

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def report(db_url: str) -> Iterator[tuple[SimulationReport, sessionmaker]]:  # type: ignore[type-arg]
    sa: SAEngine = create_engine(db_url, pool_size=5)
    clean(sa)
    sessions = sessionmaker(bind=sa, expire_on_commit=False)
    with sessions.begin() as s:
        load(s, read())
    app = create_app(MockSettings(slow_seconds=0, timeout_seconds=0))
    transport = httpx.ASGITransport(app=app)
    clock = FakeClock()
    engine = build_engine(sessions, connectors=load_connectors(transport=transport), clock=clock)
    faults = HttpFaultControl("http://mocks", transport=transport)
    yield ScenarioPlayer(engine, sessions, clock, faults).play_all(read()), sessions
    sa.dispose()


def test_every_seed_scenario_reaches_its_end_state(report) -> None:  # type: ignore[no-untyped-def]
    result, _ = report
    summary = result.summary()
    assert summary["unexpected"] == [], summary
    assert summary["end_states"] == {"completed": 34, "waiting": 2, "refused": 1}


def test_step_automation_rate_is_at_least_85_percent(report) -> None:  # type: ignore[no-untyped-def]
    result, _ = report
    assert result.automation_rate >= 0.85, result.summary()


def test_no_raw_identifier_or_ctc_is_stored_in_plain_text(report) -> None:  # type: ignore[no-untyped-def]
    _, sessions = report
    with sessions() as s:
        blobs = [str(r.context) for r in s.scalars(select(m.WorkflowRun))] + [
            str(sr.output) for sr in s.scalars(select(m.StepRun))
        ]
    text = " ".join(blobs)
    ds = read()
    for cand in ds.candidates[:10]:
        assert cand["pan"] not in text and cand["aadhaar"] not in text
        assert str(cand["offer"]["ctc"]) not in text


def test_the_audit_chain_verifies_after_the_whole_seed(report) -> None:  # type: ignore[no-untyped-def]
    _, sessions = report
    with sessions() as s:
        assert verify_chain(s).ok
