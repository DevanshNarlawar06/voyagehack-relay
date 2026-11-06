"""Contract tests: every connector op, including reconcile, against the in-process mock service."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Coroutine, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from mocks.app import MockSettings, create_app
from relay.connectors.base import ConnectorResult
from relay.connectors.http import HttpConnector, load_connectors
from relay.connectors.manifest import ConnectorManifest, load_manifests
from relay.core.errors import ConflictError, PermanentError, TransientError

CREATES: dict[tuple[str, str], dict[str, Any]] = {
    ("directory", "create_account"): {
        "employee_code": "E1",
        "display_name": "Test One",
        "email": "e1@example.com",
    },
    ("assets", "allocate_laptop"): {"employee_code": "E1", "sku": "LAT-14"},
    ("access", "issue_card"): {"employee_code": "E1", "photo_ref": "photo-1"},
    ("payroll", "setup_employee"): {"employee_code": "E1", "ctc_inr": 1200000},
    ("ticketing", "create_ticket"): {"queue": "it", "subject": "Laptop handover"},
    ("statutory", "register_pf"): {"employee_code": "E1"},
    ("calendar", "create_events"): {
        "employee_code": "E1",
        "events": [{"title": "Induction", "start": "2027-01-04T10:00", "end": "2027-01-04T11:00"}],
    },
    ("notify", "slack_dm"): {"recipient": "U1", "body": "Welcome"},
    ("notify", "email"): {"recipient": "e1@example.com", "body": "Welcome"},
}
STATE_CHANGES = [
    ("directory", "create_account", "disable_account", "disabled"),
    ("assets", "allocate_laptop", "recover_laptop", "recovered"),
    ("access", "issue_card", "revoke_card", "revoked"),
    ("payroll", "setup_employee", "notify_exit", "exiting"),
]
LOOKUPS = [
    ("directory", "create_account", "lookup"),
    ("assets", "allocate_laptop", "lookup_by_reference"),
    ("access", "issue_card", "lookup"),
    ("payroll", "setup_employee", "lookup"),
    ("statutory", "register_pf", "lookup"),
]
KEY = hashlib.sha256(b"run:step:op").hexdigest()


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class World:
    """The mock service plus connectors wired to it in process."""

    def __init__(self) -> None:
        self.app = create_app(MockSettings(slow_seconds=0.01, timeout_seconds=0.01))
        self.control = TestClient(self.app)
        self.connectors = load_connectors(transport=httpx.ASGITransport(app=self.app))

    def fault(self, fault: str, **scope: Any) -> None:
        response = self.control.post("/_control/faults", json={"fault": fault, **scope})
        assert response.status_code == 201, response.text

    def clear_faults(self) -> None:
        self.control.delete("/_control/faults")

    def records(self, system: str) -> int:
        return int(self.control.get("/_control/stats").json()[system]["records"])

    def create(self, connector: str, op: str, key: str = KEY) -> ConnectorResult:
        return run(
            self.connectors[connector].call(op, CREATES[(connector, op)], idempotency_key=key)
        )


@pytest.fixture
def world() -> Iterator[World]:
    yield World()


def test_manifests_cover_every_op_in_the_prd() -> None:
    expected = {
        "directory": {"create_account", "disable_account", "lookup"},
        "assets": {"allocate_laptop", "recover_laptop", "lookup_by_reference"},
        "access": {"issue_card", "revoke_card", "lookup"},
        "payroll": {"setup_employee", "notify_exit", "lookup"},
        "ticketing": {"create_ticket", "get_ticket", "comment"},
        "statutory": {"register_pf", "lookup"},
        "calendar": {"create_events"},
        "notify": {"slack_dm", "email"},
    }
    manifests = load_manifests()
    assert {name: set(m.ops) for name, m in manifests.items()} == expected


def test_every_side_effect_declares_how_it_stays_idempotent() -> None:
    for manifest in load_manifests().values():
        for name, spec in manifest.ops.items():
            if spec.side_effect:
                assert spec.idempotency_mode is not None, f"{manifest.connector}.{name}"
            if spec.side_effect and spec.inject_reference:
                assert spec.reconcile is not None, f"{manifest.connector}.{name} cannot reconcile"


@pytest.mark.parametrize(("connector", "op"), CREATES)
def test_create_contract(world: World, connector: str, op: str) -> None:
    result = world.create(connector, op)
    assert result.external_id is not None
    assert result.response["client_reference"] == KEY
    assert result.reconciled is False
    system = connector
    assert world.records(system) == 1


@pytest.mark.parametrize(("connector", "op"), CREATES)
def test_reconcile_finds_an_earlier_attempt(world: World, connector: str, op: str) -> None:
    created = world.create(connector, op)
    found = run(world.connectors[connector].reconcile(op, KEY, CREATES[(connector, op)]))
    assert found is not None
    assert found.reconciled is True
    assert found.external_id == created.external_id


@pytest.mark.parametrize(("connector", "op"), CREATES)
def test_reconcile_returns_none_when_nothing_landed(world: World, connector: str, op: str) -> None:
    assert run(world.connectors[connector].reconcile(op, KEY, CREATES[(connector, op)])) is None


@pytest.mark.parametrize(("connector", "create", "op", "status"), STATE_CHANGES)
def test_state_change_contract_is_repeatable(
    world: World, connector: str, create: str, op: str, status: str
) -> None:
    created = world.create(connector, create)
    payload = {"external_id": created.external_id}
    connection = world.connectors[connector]
    assert run(connection.call(op, payload, idempotency_key="k2")).response["status"] == status
    assert (
        run(connection.reconcile(op, "k2", payload)) is None
    )  # safe to repeat, nothing to look up
    assert run(connection.call(op, payload, idempotency_key="k2")).response["status"] == status


@pytest.mark.parametrize(("connector", "create", "op"), LOOKUPS)
def test_lookup_contract(world: World, connector: str, create: str, op: str) -> None:
    created = world.create(connector, create)
    found = run(
        world.connectors[connector].call(op, {"client_reference": KEY}, idempotency_key="r")
    )
    assert found.response["count"] == 1
    assert found.response["items"][0]["id"] == created.external_id


def test_get_ticket_and_comment_contract(world: World) -> None:
    created = world.create("ticketing", "create_ticket")
    connection = world.connectors["ticketing"]
    payload = {"external_id": created.external_id}
    fetched = run(connection.call("get_ticket", payload, idempotency_key="r"))
    assert fetched.external_id == created.external_id
    comment = {**payload, "comment": "Chasing"}
    first = run(connection.call("comment", comment, idempotency_key="c1"))
    retry = run(connection.call("comment", comment, idempotency_key="c1"))
    assert first.response["comments"] == ["Chasing"]
    assert retry.replayed is True
    assert retry.response["comments"] == ["Chasing"]


@pytest.mark.parametrize(("op", "channel"), [("slack_dm", "slack"), ("email", "email")])
def test_notify_sets_the_channel_from_the_manifest(world: World, op: str, channel: str) -> None:
    assert world.create("notify", op).response["channel"] == channel


def test_retry_with_the_same_key_replays_on_a_header_system(world: World) -> None:
    first = world.create("assets", "allocate_laptop")
    second = world.create("assets", "allocate_laptop")
    assert second.replayed is True
    assert second.external_id == first.external_id
    assert world.records("assets") == 1


@pytest.mark.parametrize(
    ("connector", "op"),
    [("payroll", "setup_employee"), ("statutory", "register_pf")],
)
def test_retry_on_a_natural_key_system_adopts_our_own_record(
    world: World, connector: str, op: str
) -> None:
    first = world.create(connector, op)
    second = world.create(connector, op)
    assert second.reconciled is True
    assert second.external_id == first.external_id
    assert world.records(connector) == 1


def test_natural_key_held_by_someone_else_is_a_conflict(world: World) -> None:
    world.create("payroll", "setup_employee", key="another-run")
    with pytest.raises(ConflictError) as caught:
        world.create("payroll", "setup_employee")
    assert caught.value.fields == ["employee_code"]
    assert "not ours" in caught.value.reason
    with pytest.raises(ConflictError):
        run(
            world.connectors["payroll"].reconcile(
                "setup_employee", KEY, CREATES[("payroll", "setup_employee")]
            )
        )
    assert world.records("payroll") == 1


@pytest.mark.parametrize(("connector", "op"), CREATES)
def test_lost_ack_then_reconcile_means_exactly_one_resource(
    world: World, connector: str, op: str
) -> None:
    """The crash window from PRD 11.5: the side effect landed but we never saw the response."""
    world.fault("timeout_after_commit", system=connector, remaining=1)
    with pytest.raises(TransientError):
        world.create(connector, op)
    found = run(world.connectors[connector].reconcile(op, KEY, CREATES[(connector, op)]))
    assert found is not None
    assert found.reconciled is True
    assert world.records(connector) == 1


@pytest.mark.parametrize(("connector", "op"), CREATES)
def test_failure_with_no_side_effect_reconciles_to_none_so_the_call_is_retried(
    world: World, connector: str, op: str
) -> None:
    world.fault("503", system=connector, remaining=1)
    with pytest.raises(TransientError):
        world.create(connector, op)
    assert run(world.connectors[connector].reconcile(op, KEY, CREATES[(connector, op)])) is None
    assert world.create(connector, op).external_id is not None
    assert world.records(connector) == 1


def test_reconcile_reports_duplicates_on_a_system_without_idempotency(world: World) -> None:
    world.create("access", "issue_card")
    world.create("access", "issue_card")
    found = run(
        world.connectors["access"].reconcile("issue_card", KEY, CREATES[("access", "issue_card")])
    )
    assert found is not None
    assert found.duplicates == 1


@pytest.mark.parametrize("fault", ["503", "timeout"])
def test_outage_is_transient(world: World, fault: str) -> None:
    world.fault(fault, system="assets")
    with pytest.raises(TransientError) as caught:
        world.create("assets", "allocate_laptop")
    assert KEY not in str(caught.value)
    assert world.records("assets") == 0


def test_a_503_storm_on_reconcile_is_transient_too(world: World) -> None:
    world.fault("503", system="assets")
    with pytest.raises(TransientError):
        run(
            world.connectors["assets"].reconcile(
                "allocate_laptop", KEY, CREATES[("assets", "allocate_laptop")]
            )
        )


def test_slow_response_still_succeeds(world: World) -> None:
    world.fault("slow", system="assets")
    assert world.create("assets", "allocate_laptop").external_id is not None


def test_errors_carry_the_status_for_the_engine(world: World) -> None:
    world.fault("503", system="assets")
    with pytest.raises(TransientError) as caught:
        world.create("assets", "allocate_laptop")
    assert caught.value.data == {"status": 503}
    assert "HTTP 503" in caught.value.reason


def test_invalid_payload_is_permanent_and_quotes_nothing(world: World) -> None:
    secret = {"employee_code": "E1", "ctc_inr": -5, "note": "ABCPK1234L"}
    with pytest.raises(PermanentError) as caught:
        run(world.connectors["payroll"].call("setup_employee", secret, idempotency_key=KEY))
    assert "ABCPK1234L" not in str(caught.value)


def test_unknown_record_is_permanent(world: World) -> None:
    with pytest.raises(PermanentError):
        run(
            world.connectors["assets"].call(
                "recover_laptop", {"external_id": "missing"}, idempotency_key="k"
            )
        )


def test_missing_path_field_and_unknown_op_are_permanent(world: World) -> None:
    connection = world.connectors["assets"]
    with pytest.raises(PermanentError, match="external_id"):
        run(connection.call("recover_laptop", {}, idempotency_key="k"))
    with pytest.raises(PermanentError, match="no op"):
        run(connection.call("nope", {}, idempotency_key="k"))
    with pytest.raises(PermanentError, match="employee_code"):
        run(world.connectors["payroll"].reconcile("setup_employee", "k", {}))


def test_path_values_are_escaped(world: World) -> None:
    with pytest.raises(PermanentError):
        run(
            world.connectors["assets"].call(
                "recover_laptop", {"external_id": "../../_control/reset"}, idempotency_key="k"
            )
        )


def _manifest(**op: Any) -> ConnectorManifest:
    spec = {"side_effect": True, "method": "POST", "path": "/x", "idempotency_mode": "header", **op}
    return ConnectorManifest(
        connector="x",
        version=1,
        base_url="http://x.test",
        health_path="/health",
        ops={"go": spec},
    )


def _raising(exc: Exception) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("t"),
        httpx.ConnectTimeout("t"),
        httpx.ConnectError("refused"),
        httpx.RemoteProtocolError("reset"),
    ],
)
def test_network_failures_are_transient_and_health_reports_them(exc: Exception) -> None:
    connector = HttpConnector(_manifest(), _raising(exc))
    with pytest.raises(TransientError):
        run(connector.call("go", {}, idempotency_key="k"))
    health = run(connector.health())
    assert health.healthy is False
    assert health.detail == type(exc).__name__


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (500, TransientError),
        (502, TransientError),
        (429, TransientError),
        (408, TransientError),
        (409, ConflictError),
        (400, PermanentError),
        (403, PermanentError),
        (404, PermanentError),
        (422, PermanentError),
    ],
)
def test_default_error_map(status: int, error: type[Exception]) -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(status, json={}))
    with pytest.raises(error):
        run(HttpConnector(_manifest(), transport).call("go", {}, idempotency_key="k"))


def test_manifest_can_override_the_error_map() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(404, json={}))
    connector = HttpConnector(_manifest(error_map={404: "transient"}), transport)
    with pytest.raises(TransientError):
        run(connector.call("go", {}, idempotency_key="k"))


def test_key_travels_in_the_header_and_cost_comes_from_the_manifest() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": 7})

    connector = HttpConnector(_manifest(cost_inr="1.5"), httpx.MockTransport(handler))
    result = run(connector.call("go", {}, idempotency_key="abc"))
    assert seen[0].headers["Idempotency-Key"] == "abc"
    assert result.external_id == "7"
    assert result.cost_inr == Decimal("1.5")


@pytest.mark.parametrize("connector", list(load_manifests()))
def test_health_contract(world: World, connector: str) -> None:
    health = run(world.connectors[connector].health())
    assert health.healthy is True
    assert health.status_code == 200
    assert health.latency_ms is not None


def test_health_shows_an_outage(world: World) -> None:
    world.fault("503", system="assets")
    health = run(world.connectors["assets"].health())
    assert health.healthy is False
    assert health.status_code == 503
    assert run(world.connectors["access"].health()).healthy is True


def test_base_url_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = load_manifests()["assets"]
    monkeypatch.delenv("RELAY_MOCKS_URL", raising=False)
    assert manifest.resolved_base_url() == "http://localhost:9000"
    monkeypatch.setenv("RELAY_MOCKS_URL", "http://mocks:9000/")
    assert manifest.resolved_base_url() == "http://mocks:9000"


def test_base_url_with_no_default_must_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    manifest = _manifest().model_copy(update={"base_url": "${NOPE}"})
    with pytest.raises(ValueError, match="NOPE"):
        manifest.resolved_base_url()


def test_duplicate_connector_names_are_rejected(tmp_path: Path) -> None:
    text = "connector: a\nversion: 1\nbase_url: http://a\nhealth_path: /h\nops:\n"
    text += "  go: { side_effect: false, method: GET, path: /x }\n"
    (tmp_path / "one.yaml").write_text(text, encoding="utf-8")
    (tmp_path / "two.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_manifests(tmp_path)


def test_unknown_manifest_fields_are_rejected() -> None:
    with pytest.raises(ValueError):
        _manifest(surprise=True)


def test_a_side_effect_without_an_idempotency_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="idempotency_mode"):
        _manifest(idempotency_mode=None)


def test_salary_echoed_by_payroll_never_leaves_the_connector(world: World) -> None:
    """The ledger and step output are not encrypted, so a 🔒 field must not reach them (PRD 10)."""
    payload = CREATES[("payroll", "setup_employee")]
    connector = world.connectors["payroll"]
    created = run(connector.call("setup_employee", payload, idempotency_key=KEY))
    assert "ctc_inr" not in created.response
    assert created.response["employee_code"] == "E1"
    found = run(connector.reconcile("setup_employee", KEY, payload))
    assert found is not None
    assert "ctc_inr" not in found.response
    # A retry adopts the record through the 409 path, which must be trimmed too.
    assert (
        "ctc_inr"
        not in run(connector.call("setup_employee", payload, idempotency_key=KEY)).response
    )
    listed = run(connector.call("lookup", {"client_reference": KEY}, idempotency_key="r"))
    assert listed.response["count"] == 1
    assert "ctc_inr" not in listed.response["items"][0]
    assert listed.response["items"][0]["id"] == created.external_id


def test_every_manifest_keeps_sensitive_fields_out() -> None:
    sensitive = {"ctc_inr", "pan", "bank_account", "aadhaar", "phone", "dob", "photo_ref"}
    for manifest in load_manifests().values():
        for name, spec in manifest.ops.items():
            assert not sensitive & set(spec.response_fields), f"{manifest.connector}.{name}"


def test_unlisted_fields_are_dropped_and_listed_ones_kept() -> None:
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(
            201, json={"id": 1, "status": "active", "secret": "x", "keep": "y"}
        )
    )
    connector = HttpConnector(_manifest(response_fields=("keep",)), transport)
    response = run(connector.call("go", {}, idempotency_key="k")).response
    assert response == {"id": 1, "status": "active", "keep": "y"}
