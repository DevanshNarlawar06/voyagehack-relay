"""FastAPI application factory. `relay.bootstrap` supplies the production services."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from relay.api.deps import ApiServices
from relay.api.routers import router

_DEFAULT_CONSOLE_ORIGIN = "http://localhost:3000"


def create_app(services: ApiServices) -> FastAPI:
    """Build the API. OpenAPI is published at `/docs` and `/openapi.json`."""
    app = FastAPI(title="Relay API", version="1")
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.environ.get("RELAY_CONSOLE_ORIGIN", _DEFAULT_CONSOLE_ORIGIN)],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app
