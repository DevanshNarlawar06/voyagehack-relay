"""Composition root: the one place that wires concrete connectors, skills and rules together.

`relay.api` and `relay.core` know only interfaces, so neither reaches httpx or litellm, even
indirectly (PRD 6.4, 6.5).
API:    uvicorn relay.bootstrap:create_app --factory --port 8000
Worker: relay worker
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from relay.api.app import create_app as create_api
from relay.api.deps import ApiServices, EngineGateway
from relay.config import get_config
from relay.connectors.faults import HttpFaultControl
from relay.connectors.http import load_connectors
from relay.core.audit.writer import policy_audit_hook
from relay.core.engine.engine import Engine
from relay.core.engine.ports import ConnectorPort, EngineDeps, utcnow
from relay.core.policy import PolicyService
from relay.core.registry.store import DefinitionStore
from relay.db.session import session_factory
from relay.models.db_sink import IndependentSink
from relay.models.router import ModelRouter
from relay.rules import build_rules
from relay.skills import build_skills
from relay.skills.base import load_manifest

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

log = logging.getLogger(__name__)

_DEFAULT_MOCKS_URL = "http://localhost:9000"


def default_services(engine: EngineGateway | None = None) -> ApiServices:
    """Build the production wiring: policy decisions reach the hash chain, connectors from YAML."""
    config = get_config()
    sessions = session_factory()
    skills = build_skills(ModelRouter(config.models, IndependentSink(sessions)))
    return ApiServices(
        policy=PolicyService(audit=policy_audit_hook),
        config=config,
        connectors=dict(load_connectors()),
        faults=HttpFaultControl(os.environ.get("RELAY_MOCKS_URL", _DEFAULT_MOCKS_URL)),
        engine=engine,
        intake=skills["intake"],
        helpdesk=skills["helpdesk"],
    )


def build_deps(
    sessions: sessionmaker[Session],
    *,
    connectors: Mapping[str, ConnectorPort] | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> EngineDeps:
    """Rules, connectors, skills (behind the model router) and confidence thresholds."""
    router = ModelRouter(get_config().models, IndependentSink(sessions))
    skills = build_skills(router)
    thresholds: dict[str, float] = {}
    for name in skills:
        manifest = load_manifest(name)
        if manifest.threshold is not None:
            thresholds[name] = manifest.threshold
    return EngineDeps(
        rules=build_rules(sessions),
        connectors=dict(connectors) if connectors is not None else dict(load_connectors()),
        skills=skills,
        clock=clock,
        confidence_thresholds=thresholds,
    )


def build_engine(
    sessions: sessionmaker[Session] | None = None,
    *,
    connectors: Mapping[str, ConnectorPort] | None = None,
    clock: Callable[[], datetime] = utcnow,
    worker_id: str | None = None,
    register: bool = True,
) -> Engine:
    """The engine with everything wired. Registers `definitions/workflows/` (idempotent)."""
    sessions = sessions or session_factory()
    deps = build_deps(sessions, connectors=connectors, clock=clock)
    policy = PolicyService(
        audit=policy_audit_hook, confidence_thresholds=deps.confidence_thresholds
    )
    store = DefinitionStore()
    if register:
        with sessions.begin() as s:
            store.register_dir(s, policies=policy.policies(s), deps=deps, actor="bootstrap")
    return Engine(sessions, deps, policy=policy, definitions=store, worker_id=worker_id)


def run_worker(engine: Engine, *, idle_sleep: float = 0.2, max_passes: int | None = None) -> None:
    """Tick forever (or `max_passes` times): lease steps, fire timers, reclaim expired leases."""
    passes = 0
    while max_passes is None or passes < max_passes:
        result = engine.tick()
        passes += 1
        if not result.busy:
            time.sleep(idle_sleep)


def in_process_api(
    engine: Engine, sessions: sessionmaker[Session], storage_dir: Path | None = None
) -> TestClient:
    """The real API over `engine` and `sessions`, called in process (simulation and tests)."""
    from fastapi.testclient import TestClient

    from relay.api.deps import get_session

    services = ApiServices(
        policy=PolicyService(audit=policy_audit_hook), config=get_config(), engine=engine
    )
    if storage_dir is not None:
        services.storage_dir = storage_dir
    app = create_api(services)

    def session() -> Iterator[Session]:
        with sessions() as s:
            yield s
            s.commit()

    app.dependency_overrides[get_session] = session
    return TestClient(app)


def create_app() -> FastAPI:
    """Build the API with the production wiring and its own engine (workers tick separately)."""
    engine: Any = build_engine()
    return create_api(default_services(engine=engine))


# ---------------------------------------------------------------- CLI entry point
# `relay` is the interface CLI plus the commands that need concrete wiring. Those live here, in
# the composition root, so relay.cli itself never reaches httpx or litellm (PRD 6.4, 6.5).
import typer  # noqa: E402

from relay.cli import app as cli  # noqa: E402


@cli.command()
def worker(
    worker_id: str | None = typer.Option(None, help="Defaults to a random id."),
) -> None:
    """Run an engine worker: leases steps, fires timers and reclaims expired leases (Ctrl+C stops)."""
    engine = build_engine(worker_id=worker_id)
    typer.echo(f"worker {engine.worker_id} running")
    run_worker(engine)


@cli.command()
def simulate(
    scenario: str | None = typer.Option(None, help="Only candidates of this PRD 22 scenario."),
    workflow: str = typer.Option("onboarding", help="onboarding, exit, or all."),
) -> None:
    """Play the seed scenarios on a fast clock (needs `relay seed load` and mocks).

    Talks to the mock service at RELAY_MOCKS_URL. Onboarding prints each candidate's end state and
    the step automation rate; exit plays the five seed resignations (PRD 22).
    """
    if workflow not in ("onboarding", "exit", "all"):
        typer.echo("--workflow must be onboarding, exit or all", err=True)
        raise typer.Exit(2)
    import json
    import os
    from datetime import UTC, datetime, timedelta

    from relay.db.session import session_factory
    from relay.seeding.generate import Dataset, read
    from relay.seeding.scenarios import ScenarioPlayer

    class _FastClock:
        def __init__(self) -> None:
            self.now = datetime.now(UTC)

        def __call__(self) -> datetime:
            return self.now

        def advance(self, **kwargs: float) -> None:
            self.now += timedelta(**kwargs)

    sessions = session_factory()
    clock = _FastClock()
    engine = build_engine(sessions, clock=clock, worker_id="simulate")
    faults = HttpFaultControl(os.environ.get("RELAY_MOCKS_URL", "http://localhost:9000"))
    ds = read()
    if workflow in ("onboarding", "all"):
        if scenario:
            ds = Dataset(candidates=[c for c in ds.candidates if c["scenario"] == scenario])
        player = ScenarioPlayer(engine, sessions, clock, in_process_api(engine, sessions), faults)
        report = player.play_all(ds)
        for o in report.outcomes:
            mark = "OK " if o.as_expected else "!! "
            typer.echo(f"{mark}{o.candidate_ref} {o.scenario:26} {o.end_state:10} {o.task_kinds}")
        typer.echo(json.dumps(report.summary(), indent=2))
    if workflow in ("exit", "all"):
        import asyncio

        from relay.seeding.exits import ExitPlayer, prepare_leavers, provision

        prepare_leavers(sessions)
        exits = read().exits
        asyncio.run(provision(load_connectors(), [str(e["employee_code"]) for e in exits]))
        exit_report = ExitPlayer(engine, sessions, clock, faults).play_all(exits)
        for x in exit_report.outcomes:
            mark = "OK " if x.status == "completed" else "!! "
            typer.echo(f"{mark}{x.code} {x.status:10} {x.task_actions}")
        typer.echo(json.dumps(exit_report.summary(), indent=2))


@cli.command()
def slack(
    digest: bool = typer.Option(False, help="Send each manager's onboarding digest once and exit."),
) -> None:
    """Run the Slack app in Socket Mode (PRD 16.2). Needs SLACK_BOT_TOKEN and SLACK_APP_TOKEN.

    DMs use the web chat's intake path; approval cards post to the named approver or the role's
    channel, and clicks are decided as the mapped Relay user. Run a worker alongside it.
    """
    from relay.db.session import session_factory
    from relay.slack import app as slack_app
    from relay.slack.service import SlackService

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not bot_token or (not digest and not app_token):
        typer.echo("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN first (issue #21).", err=True)
        raise typer.Exit(2)
    sessions = session_factory()
    service = SlackService(sessions, default_services(engine=build_engine(sessions)))
    if digest:
        from slack_sdk import WebClient

        sent = slack_app.post_digests(service, WebClient(token=bot_token))
        typer.echo(f"sent {sent} digest(s)")
        return
    assert app_token is not None
    typer.echo("slack app running (Socket Mode)")
    slack_app.run(service, bot_token=bot_token, app_token=app_token)


@cli.command()
def demo() -> None:
    """Rehearse the 5-minute demo (PRD 22): each step, what to show, and whether it is ready."""
    from relay.db.session import session_factory
    from relay.seeding.demo import checklist

    with session_factory()() as s:
        steps = checklist(s)
    for step in steps:
        typer.echo(f"{'OK ' if step.ready else '!! '}{step.at}  {step.title}")
        typer.echo(f"         {step.show}")
        typer.echo(f"         {step.detail}")
    ready = sum(step.ready for step in steps)
    typer.echo(f"{ready} of {len(steps)} steps ready")
    if ready < len(steps):
        raise typer.Exit(1)


def start_retention_sweep(engine: Engine, as_of: str | None = None) -> list[uuid.UUID]:
    """Start the retention sweeper (PRD 14). `as_of` (ISO date) defaults to the engine's clock."""
    payload = {"as_of": as_of} if as_of else {}
    return engine.handle_event(
        "retention.sweep.due", payload, actor="system:retention_schedule", subject_id="retention"
    )


@cli.command("retention-sweep")
def retention_sweep(
    as_of: str | None = typer.Option(None, help="ISO date to judge retention on. Defaults to now."),
    wait: bool = typer.Option(False, help="Run the sweep here instead of leaving it to a worker."),
) -> None:
    """Start the DPDP retention sweeper: purge documents retention.yaml no longer allows (nightly)."""
    engine = build_engine()
    run_ids = start_retention_sweep(engine, as_of)
    if wait:
        engine.run_until_idle()
    typer.echo(
        f"retention sweep started: {', '.join(str(r) for r in run_ids) or 'no active workflow'}"
    )
