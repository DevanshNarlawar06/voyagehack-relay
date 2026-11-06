"""FastAPI application. Run: uvicorn relay.api.app:create_app --factory --port 8000"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from relay.api.deps import ApiServices, EngineGateway
from relay.api.routers import router
from relay.config import get_config
from relay.connectors.faults import HttpFaultControl
from relay.connectors.http import load_connectors
from relay.core.audit.writer import policy_audit_hook
from relay.core.policy import PolicyService

_DEFAULT_MOCKS_URL = "http://localhost:9000"
_DEFAULT_CONSOLE_ORIGIN = "http://localhost:3000"


def default_services(engine: EngineGateway | None = None) -> ApiServices:
    """Build the production wiring: policy decisions reach the hash chain, connectors from YAML."""
    return ApiServices(
        policy=PolicyService(audit=policy_audit_hook),
        config=get_config(),
        connectors=dict(load_connectors()),
        faults=HttpFaultControl(os.environ.get("RELAY_MOCKS_URL", _DEFAULT_MOCKS_URL)),
        engine=engine,
    )


def create_app(services: ApiServices | None = None) -> FastAPI:
    """Build the API. OpenAPI is published at `/docs` and `/openapi.json`."""
    app = FastAPI(title="Relay API", version="1")
    app.state.services = services or default_services()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.environ.get("RELAY_CONSOLE_ORIGIN", _DEFAULT_CONSOLE_ORIGIN)],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app
