"""WP7 acceptance (issue #6): every PRD 22 seed scenario reaches its end state; automation >= 85%.

Real onboarding.v1.yaml, real rules, skills on the offline mock provider, real HttpConnectors to
the mock service in process, real Postgres. Joiners upload through the real API route with the
signed link the run sent them. Only the clock is fake.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from mocks.app import MockSettings, create_app
from relay.bootstrap import build_engine, in_process_api
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
def report(
    db_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[SimulationReport, sessionmaker, TestClient]]:  # type: ignore[type-arg]
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
    api = in_process_api(engine, sessions, tmp_path_factory.mktemp("uploads"))
    result = ScenarioPlayer(engine, sessions, clock, api, faults).play_all(read())
    yield result, sessions, TestClient(app)
    sa.dispose()


def test_every_seed_scenario_reaches_its_end_state(report) -> None:  # type: ignore[no-untyped-def]
    result, _, _ = report
    summary = result.summary()
    assert summary["unexpected"] == [], summary
    assert summary["end_states"] == {"completed": 34, "waiting": 2, "refused": 1}


def test_step_automation_rate_is_at_least_85_percent(report) -> None:  # type: ignore[no-untyped-def]
    result, _, _ = report
    assert result.automation_rate >= 0.85, result.summary()


def test_no_raw_identifier_or_ctc_is_stored_in_plain_text(report) -> None:  # type: ignore[no-untyped-def]
    _, sessions, _ = report
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
    _, sessions, _ = report
    with sessions() as s:
        assert verify_chain(s).ok


def test_each_joiner_was_sent_a_signed_link_that_ctx_never_holds_in_plain_text(report) -> None:  # type: ignore[no-untyped-def]
    _, sessions, mocks = report
    sent = mocks.get("/notify/messages").json()["items"]
    links = [m_["body"] for m_ in sent if "/upload?token=" in m_["body"]]
    assert len(links) == 36  # every approved offer, including the two who never upload
    with sessions() as s:
        contexts = " ".join(str(r.context) for r in s.scalars(select(m.WorkflowRun)))
    for body in links:
        token = body.split("/upload?token=")[1].split()[0]
        assert token not in contexts
