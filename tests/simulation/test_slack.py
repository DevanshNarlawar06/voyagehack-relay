"""WP9 acceptance (issue #7): demo journeys 2 and 4 through the Slack app, without Slack.

Journey 4: a PAN-name conflict lands in HR's Slack as a one-click card with the analyst summary;
HR overrides it with a reason from the modal, and the run continues. Journey 2: people talk to
Relay in a DM through the web chat's intake path. Real engine, rules, skills (offline provider),
mock connectors in process and Postgres; Slack itself is a fake client.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine as SAEngine
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from mocks.app import MockSettings, create_app
from relay.api.deps import ApiServices
from relay.bootstrap import build_engine, in_process_api
from relay.config import get_config
from relay.connectors.http import load_connectors
from relay.core.engine.engine import Engine
from relay.core.engine.sealed import unseal
from relay.core.policy import PolicyService
from relay.db import models as m
from relay.seeding.generate import read
from relay.seeding.load import load
from relay.seeding.scenarios import upload_via_api
from relay.slack.app import post_digests, post_pending_cards
from relay.slack.service import UNKNOWN_USER, SlackService
from tests.simulation.harness import FakeClock, clean

pytestmark = pytest.mark.db

HR_SLACK = "UHROPS"


class FakeSlack:
    """Records what would have been posted."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.posts.append(kwargs)
        return {"ok": True, "ts": f"{len(self.posts)}.000"}


@dataclass
class World:
    engine: Engine
    sessions: sessionmaker[Session]
    clock: FakeClock
    service: SlackService
    run_id: uuid.UUID
    manager_slack: str


def _settle(engine: Engine, clock: FakeClock) -> None:
    for _ in range(8):
        engine.run_until_idle()
        clock.advance(minutes=10)
    engine.run_until_idle()


@pytest.fixture(scope="module")
def world(db_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    sa: SAEngine = create_engine(db_url, pool_size=5)
    clean(sa)
    sessions = sessionmaker(bind=sa, expire_on_commit=False)
    with sessions.begin() as s:
        load(s, read())
        hr = s.scalar(select(m.User).where(m.User.email == "hr.ops@relay.test"))
        assert hr is not None
        hr.slack_user_id = HR_SLACK
    transport = httpx.ASGITransport(app=create_app(MockSettings(slow_seconds=0, timeout_seconds=0)))
    clock = FakeClock()
    engine = build_engine(sessions, connectors=load_connectors(transport=transport), clock=clock)
    api = in_process_api(engine, sessions, tmp_path_factory.mktemp("uploads"))

    cand = next(c for c in read().candidates if c["scenario"] == "pan_name_mismatch")
    with sessions() as s:
        row = s.scalar(select(m.Candidate).where(m.Candidate.external_ref == cand["candidate_ref"]))
        assert row is not None and row.offer_id is not None
        offer = s.get(m.Offer, row.offer_id)
        assert offer is not None
        manager = s.scalar(
            select(m.User)
            .join(m.Employee, m.Employee.person_id == m.User.person_id)
            .where(m.Employee.employee_code == cand["offer"]["manager_code"])
        )
    run_id = engine.start_run(
        "onboarding",
        {
            "candidate_ref": cand["candidate_ref"],
            "manager_code": cand["offer"]["manager_code"],
            "decision_task_id": str(offer.approved_decision_id),
            "subject_label": cand["full_name"],
        },
        actor="human:hr.ops@relay.test",
        subject_id=cand["candidate_ref"],
    )
    _settle(engine, clock)
    with sessions() as s:
        run = s.get(m.WorkflowRun, run_id)
        assert run is not None
        token = unseal(run.context["ctx"]["invite"]["token"])
    upload_via_api(api, str(token), cand)
    _settle(engine, clock)

    services = ApiServices(
        policy=PolicyService(),
        config=get_config(),
        engine=engine,
        intake=engine.deps.skills["intake"],
        helpdesk=engine.deps.skills["helpdesk"],
    )
    service = SlackService(sessions, services)
    yield World(engine, sessions, clock, service, run_id, manager.slack_user_id if manager else "")
    sa.dispose()


def _conflict(world: World) -> m.HumanTask:
    with world.sessions() as s:
        task = s.scalar(
            select(m.HumanTask).where(
                m.HumanTask.run_id == world.run_id, m.HumanTask.kind == m.HumanTaskKind.CONFLICT
            )
        )
        assert task is not None
        return task


def test_journey_4_the_conflict_card_reaches_hr_once_with_the_analyst_summary(world: World) -> None:
    slack = FakeSlack()
    assert post_pending_cards(world.service, slack) >= 1
    task = _conflict(world)
    [card] = [p for p in slack.posts if str(task.id) in str(p["blocks"])]
    assert card["channel"] == "#hr-ops"
    assert task.context_packet["analysis"]["summary"] in str(card["blocks"])
    assert post_pending_cards(world.service, slack) == 0  # never posted twice
    with world.sessions() as s:
        post = s.scalar(select(m.SlackPost).where(m.SlackPost.task_id == task.id))
        assert post is not None and post.ts


def test_journey_4_someone_else_cannot_decide_the_card(world: World) -> None:
    task = _conflict(world)
    assert world.manager_slack
    result = world.service.click(world.manager_slack, str(task.id), "retry")
    assert result.kind == "refused" and "hr_ops" in result.text
    assert world.service.click("UNOBODY", str(task.id), "retry").text == UNKNOWN_USER


def test_journey_4_override_needs_a_reason_then_the_run_continues(world: World) -> None:
    task = _conflict(world)
    click = world.service.click(HR_SLACK, str(task.id), "override")
    assert click.kind == "needs_reason" and click.title
    refused = world.service.decide(HR_SLACK, str(task.id), "override", "  ")
    assert refused.kind == "refused" and "reason" in refused.text.lower()
    decided = world.service.decide(
        HR_SLACK, str(task.id), "override", "Checked the PAN card with the joiner"
    )
    assert decided.kind == "decided" and "Checked the PAN card" in decided.text
    with world.sessions() as s:
        row = s.get(m.HumanTask, task.id)
        hr = s.scalar(select(m.User).where(m.User.slack_user_id == HR_SLACK))
        assert row is not None and hr is not None
        assert row.status is m.HumanTaskStatus.DECIDED and row.decided_by == hr.id
    _settle(world.engine, world.clock)
    with world.sessions() as s:
        step = s.scalar(
            select(m.StepRun.status).where(
                m.StepRun.run_id == world.run_id, m.StepRun.step_id == "activate_employee_id"
            )
        )
    assert step is m.StepStatus.SUCCEEDED
    again = world.service.decide(HR_SLACK, str(task.id), "override", "twice")
    assert again.kind == "refused"


def test_journey_2_a_policy_question_in_a_dm_is_answered_with_its_source(world: World) -> None:
    assert world.manager_slack
    reply = world.service.on_message(world.manager_slack, "how many leaves do I get in probation")
    assert "Source:" in reply
    with world.sessions() as s:
        convo = s.scalar(select(m.Conversation).where(m.Conversation.channel == "slack"))
        assert convo is not None


def test_journey_2_an_unknown_slack_user_gets_nothing(world: World) -> None:
    assert world.service.on_message("UNOBODY", "what's my onboarding status") == UNKNOWN_USER


def test_the_manager_digest_lists_their_joiner(world: World) -> None:
    text = world.service.digest(world.manager_slack)
    assert text is not None and "1 joiner" in text
    slack = FakeSlack()
    assert post_digests(world.service, slack) >= 1
    assert any(p["channel"] == world.manager_slack for p in slack.posts)


def test_journey_2_resigning_in_a_dm_starts_exit_and_cards_the_manager(world: World) -> None:
    with world.sessions.begin() as s:
        emp = s.scalar(select(m.Employee).where(m.Employee.employee_code == "TBO1005"))
        assert emp is not None and emp.state is m.EmployeeState.ACTIVE
        s.add(
            m.User(
                email="aniket.das@relay.test", display_name="Aniket Das",
                slack_user_id="UTEST1005", person_id=emp.person_id, roles=["employee"],
            )
        )  # fmt: skip
    reply = world.service.on_message("UTEST1005", "I want to resign")
    assert "manager" in reply and "accept" in reply
    with world.sessions() as s:
        run = s.scalar(
            select(m.WorkflowRun).where(
                m.WorkflowRun.definition_name == "exit", m.WorkflowRun.subject_id == "TBO1005"
            )
        )
        assert run is not None
    world.engine.run_until_idle()
    with world.sessions() as s:
        task = s.scalar(select(m.HumanTask).where(m.HumanTask.run_id == run.id))
        manager = s.scalar(
            select(m.User)
            .join(m.Employee, m.Employee.person_id == m.User.person_id)
            .where(m.Employee.employee_code == "TBO1000")
        )
    assert task is not None and manager is not None
    assert task.action == "employment.resignation.accept"
    [card] = [c for c in world.service.cards_to_post() if c.task_id == task.id]
    assert card.channel == manager.slack_user_id  # a DM to the named manager
    # The employee cannot accept their own resignation from the card.
    refused = world.service.click("UTEST1005", str(task.id), "accept")
    assert refused.kind == "refused"


def test_journey_2_the_manager_accepts_in_slack_with_an_agreed_release_date(world: World) -> None:
    with world.sessions() as s:
        run = s.scalar(
            select(m.WorkflowRun).where(
                m.WorkflowRun.definition_name == "exit", m.WorkflowRun.subject_id == "TBO1005"
            )
        )
        assert run is not None
        task = s.scalar(select(m.HumanTask).where(m.HumanTask.run_id == run.id))
        manager = s.scalar(
            select(m.User)
            .join(m.Employee, m.Employee.person_id == m.User.person_id)
            .where(m.Employee.employee_code == "TBO1000")
        )
    assert task is not None and manager is not None and manager.slack_user_id
    click = world.service.click(manager.slack_user_id, str(task.id), "accept")
    assert click.kind == "needs_input" and click.action == "employment.resignation.accept"
    both = world.service.decide(
        manager.slack_user_id, str(task.id), "accept", "",
        {"release_date": "2026-12-01", "buyout_agreed": True},
    )  # fmt: skip
    assert both.kind == "refused"
    done = world.service.decide(
        manager.slack_user_id, str(task.id), "accept", "", {"release_date": "2026-12-01"}
    )
    assert done.kind == "decided"
    world.engine.run_until_idle()
    with world.sessions() as s:
        ctx = s.get(m.WorkflowRun, run.id).context["ctx"]  # type: ignore[union-attr]
    assert ctx["acceptance"]["release_date"] == "2026-12-01"
    assert ctx["acceptance"]["decided_by"] == str(manager.id)
    assert ctx["exit_terms"]["last_working_day"] == "2026-12-01"
