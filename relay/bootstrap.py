"""Composition root: the one place that wires concrete connectors into the API.

`relay.api` knows only the connector interfaces, so it never reaches httpx, even indirectly
(PRD 6.4). Run: uvicorn relay.bootstrap:create_app --factory --port 8000
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from relay.api.app import create_app as create_api
from relay.api.deps import ApiServices, EngineGateway
from relay.config import get_config
from relay.connectors.faults import HttpFaultControl
from relay.connectors.http import load_connectors
from relay.core.audit.writer import policy_audit_hook
from relay.core.policy import PolicyService

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


def create_app() -> FastAPI:
    """Build the API with the production wiring."""
    return create_api(default_services())
