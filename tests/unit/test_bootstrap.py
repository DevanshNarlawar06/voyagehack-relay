"""The production wiring builds, and it sends policy decisions to the real audit chain."""

from __future__ import annotations

from relay.api.deps import ApiServices
from relay.bootstrap import create_app, default_services
from relay.core.audit.writer import policy_audit_hook


def test_default_services_use_the_hash_chain_and_every_connector() -> None:
    services = default_services()
    assert services.policy.audit is policy_audit_hook
    assert {"assets", "access", "payroll", "notify"} <= set(services.connectors)
    assert services.faults is not None
    assert services.engine is None


def test_create_app_publishes_the_routes() -> None:
    app = create_app()
    services: ApiServices = app.state.services
    assert services.config.app.demo_mode is True
    assert "/api/v1/tasks/{task_id}/decide" in app.openapi()["paths"]
