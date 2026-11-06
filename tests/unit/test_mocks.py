"""Contract tests for every mock op, the three idempotency modes and every fault mode."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mocks.app import MockSettings, create_app
from mocks.faults import Fault, FaultController, FaultRuleIn
from mocks.store import IdempotencyMode, SystemStore
from mocks.systems import SYSTEMS, SystemSpec

CREATE_BODIES: dict[str, dict[str, Any]] = {
    "directory": {"employee_code": "E1", "display_name": "Test One", "email": "e1@example.com"},
    "assets": {"employee_code": "E1", "sku": "LAT-14"},
    "access": {"employee_code": "E1", "photo_ref": "photo-1"},
    "payroll": {"employee_code": "E1", "ctc_inr": 1200000},
    "ticketing": {"queue": "it", "subject": "Laptop handover", "employee_code": "E1"},
    "statutory": {"employee_code": "E1"},
    "calendar": {
        "employee_code": "E1",
        "events": [{"title": "Induction", "start": "2027-01-04T10:00", "end": "2027-01-04T11:00"}],
    },
    "notify": {"channel": "slack", "recipient": "U1", "body": "Welcome"},
}
SPECS = {spec.name: spec for spec in SYSTEMS}
HEADER_SYSTEMS = [s.name for s in SYSTEMS if s.mode is IdempotencyMode.HEADER]
REFERENCE_SYSTEMS = [s.name for s in SYSTEMS if s.mode is IdempotencyMode.REFERENCE_LOOKUP]
NATURAL_SYSTEMS = [s.name for s in SYSTEMS if s.mode is IdempotencyMode.NATURAL_KEY]
ALL_SYSTEMS = list(SPECS)


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = MockSettings(slow_seconds=0.01, timeout_seconds=0.01)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def url(system: str) -> str:
    return f"/{system}/{SPECS[system].collection}"


def body(system: str, reference: str = "ref-1", **overrides: Any) -> dict[str, Any]:
    return {**CREATE_BODIES[system], "client_reference": reference, **overrides}


def count(client: TestClient, system: str) -> int:
    response = client.get(url(system))
    assert response.status_code == 200
    return int(response.json()["count"])


def create(client: TestClient, system: str, **kwargs: Any) -> dict[str, Any]:
    response = client.post(url(system), json=body(system), **kwargs)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def test_spec_table_covers_every_system_and_prd_op() -> None:
    assert set(CREATE_BODIES) == set(SPECS)
    expected = {
        "directory": {"create_account", "disable_account", "lookup"},
        "assets": {"allocate_laptop", "recover_laptop", "lookup_by_reference"},
        "access": {"issue_card", "revoke_card", "lookup"},
        "payroll": {"setup_employee", "notify_exit", "lookup"},
        "ticketing": {"create_ticket", "get_ticket", "comment"},
        "statutory": {"register_pf", "lookup"},
        "calendar": {"create_events"},
        "notify": {"send"},
    }
    for name, ops in expected.items():
        assert ops <= SPECS[name].ops


@pytest.mark.parametrize("system", ALL_SYSTEMS)
def test_create_read_and_lookup_contract(client: TestClient, system: str) -> None:
    created = create(client, system)
    assert created["id"].startswith(SPECS[system].id_prefix)
    assert created["client_reference"] == "ref-1"
    assert created["status"] == "active"
    assert client.get(f"{url(system)}/{created['id']}").json() == created
    by_reference = client.get(url(system), params={"client_reference": "ref-1"}).json()
    assert by_reference == {"items": [created], "count": 1}
    assert client.get(url(system), params={"client_reference": "nope"}).json()["count"] == 0


@pytest.mark.parametrize("system", ALL_SYSTEMS)
def test_read_unknown_is_404(client: TestClient, system: str) -> None:
    assert client.get(f"{url(system)}/missing").status_code == 404


@pytest.mark.parametrize("system", ALL_SYSTEMS)
def test_health(client: TestClient, system: str) -> None:
    response = client.get(f"/{system}/health")
    assert response.status_code == 200
    assert response.json()["system"] == system


@pytest.mark.parametrize("system", ALL_SYSTEMS)
def test_invalid_payloads_are_422(client: TestClient, system: str) -> None:
    assert client.post(url(system), json={}).status_code == 422
    assert client.post(url(system), json=body(system, surprise=1)).status_code == 422
    assert client.post(url(system), json=body(system, client_reference="")).status_code == 422
    assert client.post(url(system), content=b"not json").status_code == 422


@pytest.mark.parametrize(
    ("system", "path", "status"),
    [
        ("directory", "disable", "disabled"),
        ("assets", "recover", "recovered"),
        ("access", "revoke", "revoked"),
        ("payroll", "exit", "exiting"),
    ],
)
def test_state_change_ops_are_repeatable(
    client: TestClient, system: str, path: str, status: str
) -> None:
    record = create(client, system)
    first = client.post(f"{url(system)}/{record['id']}/{path}")
    assert first.status_code == 200
    assert first.json()["status"] == status
    assert client.post(f"{url(system)}/{record['id']}/{path}").status_code == 200
    stats = client.get("/_control/stats").json()[system]
    assert stats["side_effects"] == 2  # the create and one transition
    assert client.post(f"{url(system)}/missing/{path}").status_code == 404


def test_ticket_comment_appends_and_replays_with_a_key(client: TestClient) -> None:
    ticket = create(client, "ticketing")
    path = f"{url('ticketing')}/{ticket['id']}/comments"
    headers = {"Idempotency-Key": "c1"}
    first = client.post(path, json={"comment": "Chasing"}, headers=headers)
    second = client.post(path, json={"comment": "Chasing"}, headers=headers)
    assert first.json()["comments"] == ["Chasing"]
    assert second.headers["Idempotent-Replayed"] == "true"
    assert client.get(f"{url('ticketing')}/{ticket['id']}").json()["comments"] == ["Chasing"]
    assert client.post(path, json={"comment": ""}).status_code == 422


@pytest.mark.parametrize("system", HEADER_SYSTEMS)
def test_header_mode_replays_the_same_key(client: TestClient, system: str) -> None:
    headers = {"Idempotency-Key": "k1"}
    first = client.post(url(system), json=body(system), headers=headers)
    second = client.post(url(system), json=body(system), headers=headers)
    assert first.status_code == second.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert first.json() == second.json()
    assert count(client, system) == 1
    assert client.get("/_control/stats").json()[system]["replays"] == 1


@pytest.mark.parametrize("system", HEADER_SYSTEMS)
def test_header_mode_rejects_a_reused_key_with_a_new_body(client: TestClient, system: str) -> None:
    headers = {"Idempotency-Key": "k1"}
    client.post(url(system), json=body(system), headers=headers)
    response = client.post(url(system), json=body(system, "ref-2"), headers=headers)
    assert response.status_code == 422
    assert count(client, system) == 1


@pytest.mark.parametrize("system", REFERENCE_SYSTEMS)
def test_reference_mode_duplicates_unless_the_client_looks_up_first(
    client: TestClient, system: str
) -> None:
    headers = {"Idempotency-Key": "ignored"}
    client.post(url(system), json=body(system), headers=headers)
    client.post(url(system), json=body(system), headers=headers)
    found = client.get(url(system), params={"client_reference": "ref-1"}).json()
    assert found["count"] == 2


@pytest.mark.parametrize("system", NATURAL_SYSTEMS)
def test_natural_key_mode_returns_409_with_the_existing_id(client: TestClient, system: str) -> None:
    created = create(client, system)
    again = client.post(url(system), json=body(system, "ref-2"))
    assert again.status_code == 409
    assert again.json() == {"error": "already_exists", "existing_id": created["id"]}
    assert count(client, system) == 1


@pytest.mark.parametrize("system", ALL_SYSTEMS)
def test_x_fault_503_has_no_side_effect(client: TestClient, system: str) -> None:
    response = client.post(url(system), json=body(system), headers={"X-Fault": "503"})
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert count(client, system) == 0


def test_x_fault_timeout_has_no_side_effect(client: TestClient) -> None:
    response = client.post(url("assets"), json=body("assets"), headers={"X-Fault": "timeout"})
    assert response.status_code == 504
    assert count(client, "assets") == 0


def test_x_fault_timeout_after_commit_loses_the_ack_but_keeps_the_side_effect(
    client: TestClient,
) -> None:
    headers = {"X-Fault": "timeout_after_commit", "Idempotency-Key": "k1"}
    assert client.post(url("assets"), json=body("assets"), headers=headers).status_code == 504
    assert count(client, "assets") == 1
    retry = client.post(url("assets"), json=body("assets"), headers={"Idempotency-Key": "k1"})
    assert retry.status_code == 201
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert count(client, "assets") == 1


def test_x_fault_slow_waits_then_succeeds(client: TestClient) -> None:
    slow = TestClient(create_app(MockSettings(slow_seconds=0.2, timeout_seconds=0.01)))
    with slow:
        response = slow.post(url("assets"), json=body("assets"), headers={"X-Fault": "slow"})
    assert response.status_code == 201
    assert response.elapsed.total_seconds() >= 0.2


def test_x_fault_duplicate_on_retry_defeats_the_idempotency_key(client: TestClient) -> None:
    headers = {"Idempotency-Key": "k1"}
    client.post(url("assets"), json=body("assets"), headers=headers)
    retry = client.post(
        url("assets"), json=body("assets"), headers={**headers, "X-Fault": "duplicate_on_retry"}
    )
    assert retry.status_code == 201
    assert "Idempotent-Replayed" not in retry.headers
    assert count(client, "assets") == 2


def test_x_fault_duplicate_on_retry_defeats_a_natural_key(client: TestClient) -> None:
    client.post(url("payroll"), json=body("payroll"))
    retry = client.post(
        url("payroll"), json=body("payroll"), headers={"X-Fault": "duplicate_on_retry"}
    )
    assert retry.status_code == 201
    assert count(client, "payroll") == 2


def test_unknown_x_fault_is_400(client: TestClient) -> None:
    response = client.post(url("assets"), json=body("assets"), headers={"X-Fault": "gremlins"})
    assert response.status_code == 400
    assert count(client, "assets") == 0


@pytest.mark.parametrize("fault", [f.value for f in Fault])
def test_every_fault_mode_works_through_the_control_endpoint(
    client: TestClient, fault: str
) -> None:
    expected = {"503": 503, "timeout": 504, "timeout_after_commit": 504}.get(fault, 201)
    rule = client.post("/_control/faults", json={"system": "assets", "fault": fault})
    assert rule.status_code == 201
    response = client.post(url("assets"), json=body("assets"))
    assert response.status_code == expected


def test_control_outage_targets_one_system_and_can_be_lifted(client: TestClient) -> None:
    rule = client.post("/_control/faults", json={"system": "assets", "fault": "503"}).json()
    assert client.post(url("assets"), json=body("assets")).status_code == 503
    assert client.get("/assets/health").status_code == 503
    assert client.post(url("access"), json=body("access")).status_code == 201
    assert [r["id"] for r in client.get("/_control/faults").json()] == [rule["id"]]
    assert client.delete(f"/_control/faults/{rule['id']}").status_code == 204
    assert client.delete(f"/_control/faults/{rule['id']}").status_code == 404
    assert client.post(url("assets"), json=body("assets")).status_code == 201


def test_control_rule_can_target_one_op_and_a_number_of_calls(client: TestClient) -> None:
    client.post(
        "/_control/faults",
        json={"system": "assets", "op": "allocate_laptop", "fault": "503", "remaining": 2},
    )
    assert client.get(url("assets")).status_code == 200
    assert client.post(url("assets"), json=body("assets")).status_code == 503
    assert client.post(url("assets"), json=body("assets")).status_code == 503
    assert client.post(url("assets"), json=body("assets")).status_code == 201
    assert client.get("/_control/faults").json() == []


def test_x_fault_header_overrides_a_control_rule(client: TestClient) -> None:
    client.post("/_control/faults", json={"fault": "503"})
    response = client.get(url("assets"), headers={"X-Fault": "slow"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "rule",
    [
        {"system": "nope", "fault": "503"},
        {"system": "assets", "op": "nope", "fault": "503"},
        {"op": "nope", "fault": "503"},
        {"fault": "gremlins"},
        {"fault": "503", "remaining": 0},
    ],
)
def test_control_rejects_bad_rules(client: TestClient, rule: dict[str, Any]) -> None:
    assert client.post("/_control/faults", json=rule).status_code == 422


def test_clear_faults_and_reset(client: TestClient) -> None:
    client.post("/_control/faults", json={"system": "access", "fault": "503"})
    assert client.delete("/_control/faults").status_code == 204
    create(client, "access")
    client.post("/_control/faults", json={"fault": "503"})
    assert client.post("/_control/reset").status_code == 204
    assert client.get("/_control/faults").json() == []
    assert count(client, "access") == 0
    assert client.get("/_control/stats").json()["access"]["side_effects"] == 0
    assert create(client, "access")["id"].endswith("00001")


def test_healthz_ignores_faults(client: TestClient) -> None:
    client.post("/_control/faults", json={"fault": "503"})
    assert client.get("/healthz").status_code == 200
    assert client.get("/_control/stats").status_code == 200


def test_duration_rule_expires_with_the_clock() -> None:
    now = [100.0]
    faults = FaultController(clock=lambda: now[0])
    faults.add(FaultRuleIn(fault=Fault.SERVICE_UNAVAILABLE, duration_seconds=5))
    assert faults.resolve("assets", "allocate_laptop", None) is Fault.SERVICE_UNAVAILABLE
    now[0] = 105.0
    assert faults.resolve("assets", "allocate_laptop", None) is None
    assert faults.list() == []


def test_state_is_per_app_instance() -> None:
    settings = MockSettings(slow_seconds=0, timeout_seconds=0)
    with TestClient(create_app(settings)) as a, TestClient(create_app(settings)) as b:
        create(a, "assets")
        assert count(b, "assets") == 0


def test_store_without_a_key_always_creates() -> None:
    store = SystemStore("X", IdempotencyMode.HEADER)
    payload = {"client_reference": "r"}
    store.create(payload, idempotency_key=None)
    store.create(payload, idempotency_key=None)
    assert len(store.records) == 2


def test_every_spec_has_a_unique_prefix_and_collection() -> None:
    specs: list[SystemSpec] = list(SYSTEMS)
    assert len({s.id_prefix for s in specs}) == len(specs)
    assert len({s.name for s in specs}) == len(specs)
