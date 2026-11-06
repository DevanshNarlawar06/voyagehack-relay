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
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

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

log = logging.getLogger(__name__)

_DEFAULT_MOCKS_URL = "http://localhost:9000"


def default_services(engine: EngineGateway | None = None) -> ApiServices:
    """Build the production wiring: policy decisions reach the hash chain, connectors from YAML."""
    return ApiServices(
        policy=PolicyService(audit=policy_audit_hook),
        config=get_config(),
        connectors=dict(load_connectors()),
        faults=HttpFaultControl(os.environ.get("RELAY_MOCKS_URL", _DEFAULT_MOCKS_URL)),
        engine=engine,
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


def create_app() -> FastAPI:
    """Build the API with the production wiring and its own engine (workers tick separately)."""
    engine: Any = build_engine()
    return create_api(default_services(engine=engine))
