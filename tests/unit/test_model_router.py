"""Router tests: PRD 13.3 algorithm, no raw PII in any payload, provider swap is config-only."""

import ast
import json
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import jsonschema
import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.config import ModelsConfig, get_config
from relay.db.models import (
    CostEvent,
    CostKind,
    ModelPayload,
    PayloadDirection,
    RunStatus,
    WorkflowRun,
)
from relay.models import router as router_module
from relay.models.db_sink import DatabaseSink
from relay.models.mock_provider import MockProvider
from relay.models.pii_gateway import PiiGateway, PiiVault
from relay.models.recognizers import verhoeff_check_digit
from relay.models.router import ModelRouter
from relay.models.types import (
    ChatMessage,
    InMemorySink,
    LowConfidence,
    ModelRequest,
    ModelUnavailable,
    ProviderRequest,
    ProviderResponse,
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"intent": {"type": "string"}, "note": {"type": "string"}},
    "required": ["intent", "note"],
}
PAN = "ABCPK1234L"
MOBILE = "9876543210"
EMAIL = "asha@example.com"
AADHAAR = "23456789012" + verhoeff_check_digit("23456789012")
PROMPT = f"Joiner PAN {PAN}, Aadhaar {AADHAAR}, mobile {MOBILE}, mail {EMAIL}."
RAW_VALUES = (PAN, AADHAAR, MOBILE, EMAIL)


def config(**overrides: Any) -> ModelsConfig:
    base: dict[str, Any] = {
        "providers": {
            "small": {
                "litellm_model": "mock/small",
                "price_inr": {"in_per_1k": 1, "out_per_1k": 2},
                "region": "local",
            },
            "large": {
                "litellm_model": "mock/large",
                "price_inr": {"in_per_1k": 10, "out_per_1k": 20},
            },
        },
        "tiers": {"tier1": "small", "tier2": "large"},
        "defaults": {"max_output_tokens": 400, "temperature": 0, "json_mode": True},
    }
    return ModelsConfig.model_validate({**base, **overrides})


def request(**overrides: Any) -> ModelRequest:
    fields: dict[str, Any] = {
        "skill": "intake",
        "skill_version": "1",
        "prompt_version": "v1",
        "messages": [ChatMessage(role="user", content=PROMPT)],
        "output_schema": SCHEMA,
        **overrides,
    }
    return ModelRequest(**fields)


class Scripted:
    """A provider that replays canned replies and remembers what it was sent."""

    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = list(replies)
        self.requests: list[ProviderRequest] = []

    def complete(self, provider_request: ProviderRequest) -> ProviderResponse:
        self.requests.append(provider_request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ProviderResponse(reply, 100, 50)


def scripted_router(
    monkeypatch: pytest.MonkeyPatch, replies: list[str | Exception], **overrides: Any
) -> tuple[ModelRouter, InMemorySink, Scripted]:
    provider = Scripted(replies)
    monkeypatch.setattr(router_module, "_select_provider", lambda _config: provider)
    sink = InMemorySink()
    return ModelRouter(config(**overrides), sink), sink, provider


def reply(intent: str = "status", note: str = "ok") -> str:
    return json.dumps({"intent": intent, "note": note})


def test_shipped_config_loads_and_defaults_to_the_mock_provider() -> None:
    loaded = get_config().models
    assert loaded.provider_for_tier("tier1").litellm_model.startswith("mock/")
    assert loaded.provider_for_tier("tier2").litellm_model.startswith("mock/")
    assert loaded.provider_for_tier("tier1").region == "local"


@pytest.mark.parametrize("tiers", [{"tier1": "small"}, {"tier1": "small", "tier2": "nope"}])
def test_config_rejects_bad_tiers(tiers: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        config(tiers=tiers)


def test_generation_has_no_gate_and_returns_schema_valid_mock_output() -> None:
    sink = InMemorySink()
    result = ModelRouter(config(), sink).complete(request())
    jsonschema.validate(result.output, SCHEMA)
    assert result.tier == "tier1"
    assert result.model_id == "mock/small"
    assert result.confidence is None
    assert result.escalated is False
    assert (result.skill_version, result.prompt_version) == ("1", "v1")


def test_mock_is_deterministic_and_keyed_on_the_masked_prompt() -> None:
    first = ModelRouter(config(), InMemorySink()).complete(request())
    second = ModelRouter(config(), InMemorySink()).complete(request())
    other = ModelRouter(config(), InMemorySink()).complete(
        request(messages=[ChatMessage(role="user", content="something else")])
    )
    assert first.output == second.output
    assert first.output != other.output


@pytest.mark.parametrize(
    ("schema", "check"),
    [
        ({"type": "object", "properties": {"a": {"enum": ["x", "y"]}}}, None),
        (
            {
                "type": "object",
                "properties": {"a": {"type": "integer", "minimum": 3, "maximum": 4}},
            },
            None,
        ),
        (
            {
                "type": "object",
                "properties": {"a": {"type": "array", "items": {"type": "boolean"}}},
            },
            None,
        ),
        ({"type": "object", "properties": {"a": {"type": ["null", "number"]}}}, None),
        (
            {
                "type": "object",
                "properties": {"a": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            },
            None,
        ),
        ({"type": "object", "properties": {"a": {"const": 7}}}, None),
        ({"type": "object", "properties": {"a": {"type": "null"}}}, None),
        ({"type": "object", "properties": {"a": {"type": "string", "minLength": 40}}}, None),
        ({"type": "object", "properties": {"a": {"type": "string", "maxLength": 3}}}, None),
        ({"type": "object", "properties": {"a": {"allOf": [{"type": "string"}]}}}, None),
    ],
)
def test_mock_output_is_valid_for_each_schema_shape(schema: dict[str, Any], check: None) -> None:
    provider_request = ProviderRequest(
        "mock/x", [{"role": "user", "content": "x"}], 0, 50, schema, True
    )
    jsonschema.validate(json.loads(MockProvider().complete(provider_request).text), schema)


def test_mock_confidence_field_is_high_so_mock_runs_do_not_escalate() -> None:
    schema = {"type": "object", "properties": {"confidence": {"type": "number"}}}
    provider_request = ProviderRequest(
        "mock/x", [{"role": "user", "content": "x"}], 0, 50, schema, True
    )
    assert json.loads(MockProvider().complete(provider_request).text)["confidence"] == 0.95


def test_provider_only_ever_sees_masked_text(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, [reply()])
    router.complete(request())
    sent = json.dumps(provider.requests[0].messages)
    assert "<PAN_1>" in sent
    for value in RAW_VALUES:
        assert value not in sent


def test_no_raw_pii_in_any_payload_row_even_if_the_model_repeats_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    echo = json.dumps({"intent": "status", "note": f"PAN {PAN} mail {EMAIL} phone {MOBILE}"})
    bad = f"not json, but mentions {AADHAAR} and {PAN}"
    router, sink, _provider = scripted_router(monkeypatch, [bad, echo])
    result = router.complete(request())
    assert len(sink.payloads) == 4
    for row in sink.payloads:
        for value in RAW_VALUES:
            assert value not in row.masked_text
    assert {row.direction for row in sink.payloads} == {"out", "in"}
    assert PAN not in json.dumps(result.masked_output)


def test_no_raw_pii_on_the_mock_path_across_a_random_sample() -> None:
    sink = InMemorySink()
    router = ModelRouter(config(), sink)
    for i in range(50):
        body = f"{2 + i % 8}{i:010d}"
        aadhaar = body + verhoeff_check_digit(body)
        pan = f"QWERT{1000 + i}Z"
        router.complete(
            request(messages=[ChatMessage(role="user", content=f"PAN {pan} Aadhaar {aadhaar}")])
        )
        stored = " ".join(row.masked_text for row in sink.payloads)
        assert pan not in stored
        assert aadhaar not in stored


def test_output_has_tokens_restored_and_is_hidden_from_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router, _sink, _provider = scripted_router(monkeypatch, [reply(note="for <PAN_1> only")])
    result = router.complete(request())
    assert result.output["note"] == f"for {PAN} only"
    assert result.masked_output["note"] == "for <PAN_1> only"
    assert PAN not in repr(result)


def test_a_passed_vault_keeps_tokens_stable_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, [reply(), reply()])
    vault = PiiVault()
    other = ChatMessage(role="user", content="PAN QWERT1111Z")
    router.complete(request(messages=[other], vault=vault))
    router.complete(request(vault=vault))
    assert "<PAN_2>" in json.dumps(provider.requests[1].messages)


def test_invalid_json_gets_one_repair_on_tier1(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, ["oops", reply()])
    result = router.complete(request())
    assert result.tier == "tier1"
    assert len(provider.requests) == 2
    assert "not valid JSON" in provider.requests[1].messages[-1]["content"]
    assert provider.requests[1].model == "mock/small"


def test_schema_violation_counts_as_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, ['{"intent": 1}', reply()])
    assert router.complete(request()).output["intent"] == "status"
    assert len(provider.requests) == 2


def test_invalid_after_repair_escalates_to_tier2_with_no_second_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router, _sink, provider = scripted_router(monkeypatch, ["bad", "bad", reply()])
    result = router.complete(request())
    assert result.tier == "tier2"
    assert result.escalated is True
    assert result.model_id == "mock/large"
    assert [r.model for r in provider.requests] == ["mock/small", "mock/small", "mock/large"]


def test_invalid_on_both_tiers_is_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, ["bad", "bad", "bad"])
    with pytest.raises(LowConfidence) as caught:
        router.complete(request())
    assert len(provider.requests) == 3
    assert PAN not in str(caught.value)


def confidence_by_note(score: dict[str, float]) -> Callable[[dict[str, Any]], float]:
    return lambda output: score[output["note"]]


def test_confidence_above_threshold_stays_on_tier1(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, [reply(note="good")])
    result = router.complete(request(confidence=confidence_by_note({"good": 0.8}), threshold=0.8))
    assert result.tier == "tier1"
    assert result.confidence == 0.8
    assert len(provider.requests) == 1


def test_low_confidence_escalates_once_to_tier2(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, [reply(note="weak"), reply(note="good")])
    result = router.complete(
        request(confidence=confidence_by_note({"weak": 0.5, "good": 0.9}), threshold=0.8)
    )
    assert result.tier == "tier2"
    assert result.confidence == 0.9
    assert len(provider.requests) == 2


def test_still_low_on_tier2_raises_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, _provider = scripted_router(
        monkeypatch, [reply(note="weak"), reply(note="weak")]
    )
    with pytest.raises(LowConfidence):
        router.complete(request(confidence=confidence_by_note({"weak": 0.5}), threshold=0.8))


def test_confidence_function_sees_unmasked_values(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def score(output: dict[str, Any]) -> float:
        seen.append(output["note"])
        return 1.0

    router, _sink, _provider = scripted_router(monkeypatch, [reply(note="<PAN_1>")])
    router.complete(request(confidence=score))
    assert seen == [PAN]


def test_config_threshold_overrides_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(
        monkeypatch, [reply(note="x"), reply(note="x")], thresholds={"intake": 0.5}
    )
    router.complete(request(confidence=confidence_by_note({"x": 0.6}), threshold=0.99))
    assert len(provider.requests) == 1


def test_default_threshold_applies_when_none_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, [reply(note="x"), reply(note="x")])
    with pytest.raises(LowConfidence):
        router.complete(request(confidence=confidence_by_note({"x": 0.79})))
    assert len(provider.requests) == 2


def test_self_consistency_agreeing_samples_are_confident(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, [reply("a"), reply("a"), reply("a")])
    result = router.complete(request(classification_field="intent"))
    assert result.confidence == 1.0
    assert result.tier == "tier1"
    assert [r.temperature for r in provider.requests] == [0, 0.7, 0.7]


def test_self_consistency_disagreement_escalates_to_tier2(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, _provider = scripted_router(
        monkeypatch,
        [reply("a"), reply("b"), reply("c"), reply("d"), reply("d"), reply("d")],
    )
    result = router.complete(request(classification_field="intent"))
    assert result.tier == "tier2"
    assert result.output["intent"] == "d"


def test_self_consistency_picks_the_majority_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, _provider = scripted_router(monkeypatch, [reply("a"), reply("b"), reply("b")])
    result = router.complete(request(classification_field="intent", threshold=0.6))
    assert result.output["intent"] == "b"
    assert result.confidence == pytest.approx(2 / 3)


def test_invalid_samples_count_against_agreement(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, _provider = scripted_router(
        monkeypatch, [reply("a"), "junk", reply("a"), reply("d"), reply("d"), reply("d")]
    )
    result = router.complete(request(classification_field="intent", threshold=0.8))
    assert result.tier == "tier2"


def test_self_consistency_on_mock_agrees_so_mock_never_goes_to_humans() -> None:
    result = ModelRouter(config(), InMemorySink()).complete(request(classification_field="intent"))
    assert result.confidence == 1.0


def test_tier1_outage_falls_through_to_tier2(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, _provider = scripted_router(monkeypatch, [RuntimeError("503"), reply()])
    result = router.complete(request())
    assert result.tier == "tier2"


def test_both_tiers_down_is_model_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, _provider = scripted_router(
        monkeypatch, [RuntimeError(PROMPT), RuntimeError(PROMPT)]
    )
    with pytest.raises(ModelUnavailable) as caught:
        router.complete(request())
    assert PAN not in str(caught.value)


def test_a_bug_in_the_confidence_function_is_not_mistaken_for_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_output: dict[str, Any]) -> float:
        raise KeyError("field")

    router, _sink, provider = scripted_router(monkeypatch, [reply(), reply()])
    with pytest.raises(KeyError):
        router.complete(request(confidence=broken))
    assert len(provider.requests) == 1


def test_provider_failure_logs_only_the_type(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    router, _sink, _provider = scripted_router(monkeypatch, [RuntimeError(PROMPT), reply()])
    router.complete(request())
    assert "RuntimeError" in caplog.text
    assert PAN not in caplog.text


def test_one_cost_event_per_call_with_inr_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id, step_id = uuid4(), uuid4()
    router, sink, _provider = scripted_router(
        monkeypatch, ["bad", reply(note="weak"), reply(note="good")]
    )
    result = router.complete(
        request(
            run_id=run_id,
            step_run_id=step_id,
            confidence=confidence_by_note({"weak": 0.1, "good": 0.9}),
        )
    )
    assert [c.model_id for c in sink.costs] == ["mock/small", "mock/small", "mock/large"]
    # 100 tokens in, 50 out: small = 0.1 + 0.1, large = 1 + 1
    assert [c.cost_inr for c in sink.costs] == [Decimal("0.2"), Decimal("0.2"), Decimal(2)]
    assert result.cost_inr == Decimal("2.4")
    assert all((c.run_id, c.step_run_id) == (run_id, step_id) for c in sink.costs)
    assert sink.costs[0].unit_prices == {"in_per_1k": "1.0", "out_per_1k": "2.0"}


def test_result_links_every_payload_row_for_the_audit_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router, sink, _provider = scripted_router(monkeypatch, [reply()])
    result = router.complete(request())
    assert result.payload_ids == [row.id for row in sink.payloads]
    assert [(r.direction, r.provider, r.region) for r in sink.payloads] == [
        ("out", "small", "local"),
        ("in", "small", "local"),
    ]


def test_max_output_tokens_can_be_capped_per_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    router, _sink, provider = scripted_router(monkeypatch, [reply(), reply()])
    router.complete(request(max_output_tokens=150))
    router.complete(request())
    assert [r.max_output_tokens for r in provider.requests] == [150, 400]


def test_provider_swap_is_config_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the tiers at a hosted model in YAML alone; no code changes and masking still holds."""
    calls: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply()))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=1000),
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))
    path = tmp_path / "models.yaml"
    path.write_text(
        "providers:\n"
        "  primary: { litellm_model: 'acme/small', price_inr: { in_per_1k: 3, out_per_1k: 5 } }\n"
        "tiers: { tier1: primary, tier2: primary }\n"
        "defaults: { max_output_tokens: 400, temperature: 0, json_mode: true }\n",
        encoding="utf-8",
    )
    sink = InMemorySink()
    loaded = ModelsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    result = ModelRouter(loaded, sink).complete(request())
    assert result.model_id == "acme/small"
    assert result.cost_inr == Decimal(8)
    assert calls[0]["model"] == "acme/small"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert PAN not in json.dumps(calls[0]["messages"])


def test_json_mode_can_be_turned_off_in_config(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply()))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))
    providers = {
        "p": {"litellm_model": "acme/s", "price_inr": {"in_per_1k": 0, "out_per_1k": 0}},
    }
    cfg = config(
        providers=providers,
        tiers={"tier1": "p", "tier2": "p"},
        defaults={"max_output_tokens": 400, "temperature": 0, "json_mode": False},
    )
    ModelRouter(cfg, InMemorySink()).complete(request())
    assert "response_format" not in calls[0]


def test_custom_gateway_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = Scripted([reply()])
    monkeypatch.setattr(router_module, "_select_provider", lambda _config: provider)
    ModelRouter(config(), InMemorySink(), PiiGateway(recognizers=())).complete(request())
    assert PAN in json.dumps(provider.requests[0].messages)


@pytest.mark.db
def test_no_raw_pii_in_any_model_payloads_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The WP6b acceptance test, against the real table, with a model that repeats what it saw."""
    run = WorkflowRun(
        definition_name="onboarding",
        definition_version=1,
        subject_type="employee",
        subject_id="e1",
        status=RunStatus.RUNNING,
        started_by_actor="system:test",
    )
    db_session.add(run)
    db_session.flush()
    run_id = run.id
    echo = json.dumps({"intent": "status", "note": f"PAN {PAN} phone {MOBILE}"})
    provider = Scripted([f"junk {AADHAAR} {EMAIL}", echo])
    monkeypatch.setattr(router_module, "_select_provider", lambda _config: provider)
    result = ModelRouter(config(), DatabaseSink(db_session)).complete(request(run_id=run_id))
    rows = db_session.scalars(select(ModelPayload).where(ModelPayload.run_id == run_id)).all()
    assert [r.direction for r in rows].count(PayloadDirection.OUT) == 2
    assert [r.direction for r in rows].count(PayloadDirection.IN) == 2
    for row in rows:
        for value in RAW_VALUES:
            assert value not in row.masked_text
        assert row.provider == "small"
        assert row.model_id == "mock/small"
        assert row.region == "local"
    assert {r.id for r in rows} == set(result.payload_ids)


@pytest.mark.db
def test_database_sink_writes_cost_events(db_session: Session) -> None:
    run = WorkflowRun(
        definition_name="onboarding",
        definition_version=1,
        subject_type="employee",
        subject_id="e1",
        status=RunStatus.RUNNING,
        started_by_actor="system:test",
    )
    db_session.add(run)
    db_session.flush()
    ModelRouter(config(), DatabaseSink(db_session)).complete(request(run_id=run.id))
    event = db_session.scalars(select(CostEvent).where(CostEvent.run_id == run.id)).one()
    assert event.kind is CostKind.LLM
    assert event.model_id == "mock/small"
    assert event.cost_inr > 0
    assert event.tokens_in > 0


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_only_the_router_imports_litellm_anywhere_in_relay() -> None:
    importers = {
        path.as_posix() for path in Path("relay").rglob("*.py") if "litellm" in _imports(path)
    }
    assert importers == {"relay/models/router.py"}
