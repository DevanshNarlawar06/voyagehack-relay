"""DSL parsing, parallel flattening and registry validation (`relay validate` rejects invalid fixtures)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from relay.core.dsl.model import RetryPolicy, parse_definition, parse_duration
from relay.core.engine.ports import EngineDeps
from relay.core.policy.packs import load_dir
from relay.core.policy.service import DEFAULT_POLICY_DIR
from relay.core.registry.validate import validate_definition
from tests.simulation.harness import TOY

POLICIES, _ = load_dir(DEFAULT_POLICY_DIR)


def test_durations() -> None:
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("2d") == timedelta(days=2)
    with pytest.raises(ValueError):
        parse_duration("tomorrow")


def test_exponential_backoff_is_capped() -> None:
    r = RetryPolicy(base_seconds=10, max_seconds=60)
    assert [r.delay(i).total_seconds() for i in (1, 2, 3, 4, 5)] == [10, 20, 40, 60, 60]


def test_parallel_blocks_flatten_and_aliases_expand() -> None:
    wf = parse_definition(TOY)
    steps = {s.id: s for s in wf.flat_steps()}
    assert steps["provision_identity"].needs == ["await_documents"]
    assert steps["select_laptop"].needs == ["await_documents"]
    assert steps["order_laptop"].needs == ["select_laptop"]  # previous step in its branch
    assert steps["probation_confirmation"].needs == [
        "provision_identity", "select_laptop", "order_laptop",
    ]  # fmt: skip


def test_toy_definition_is_valid() -> None:
    assert validate_definition(parse_definition(TOY), POLICIES) == []


def _broken(old: str, new: str) -> list[str]:
    assert old in TOY
    return validate_definition(parse_definition(TOY.replace(old, new, 1)), POLICIES)


def test_unknown_need_is_rejected() -> None:
    assert any("unknown step" in e for e in _broken("needs: [create_prehire]", "needs: [nope]"))


def test_cycle_is_rejected() -> None:
    errors = _broken(
        "    uses: rules.prehire\n    action: employee.create\n",
        "    uses: rules.prehire\n    action: employee.create\n    needs: [compute_terms]\n",
    )
    assert any(e.startswith("cycle:") for e in errors)


def test_undeclared_action_is_rejected() -> None:
    assert any(
        "not declared" in e for e in _broken("action: employee.create", "action: employee.vibe")
    )


def test_bad_cel_is_rejected() -> None:
    assert any(
        "CEL" in e for e in _broken('"ctx.probation_months > 3"', '"ctx.probation_months >"')
    )


def test_reserved_human_step_must_go_to_the_approver() -> None:
    errors = _broken("assignee_role: manager", "assignee_role: hr_ops")
    assert any("must be assigned to manager" in e for e in errors)


def test_unresolved_uses_is_rejected_when_deps_given() -> None:
    errors = validate_definition(parse_definition(TOY), POLICIES, EngineDeps())
    assert any("unknown rule 'rules.prehire'" in e for e in errors)
    assert any("unknown connector op 'assets.allocate'" in e for e in errors)


def test_require_decision_must_be_reserved() -> None:
    errors = _broken(
        "trigger: { event: toy.hired }",
        "trigger: { event: toy.hired, require_decision: employee.create }",
    )
    assert any("not a reserved action" in e for e in errors)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("kind: human", "kind: wait"),  # wait without until/for_subject_action
        ("id: create_prehire", "id: Create-Prehire"),  # not snake_case
        ("escalate_to: hr_ops", "escalate_to: null"),  # escalating SLA without a target
    ],
)
def test_schema_errors(old: str, new: str) -> None:
    with pytest.raises(ValidationError):
        parse_definition(TOY.replace(old, new, 1))


@pytest.mark.db
def test_concurrent_registration_does_not_race(db_engine) -> None:  # type: ignore[no-untyped-def]
    """An API and workers starting together all register definitions/workflows (#58 review)."""
    import threading

    from sqlalchemy.orm import sessionmaker

    from relay.core.policy import PolicyService
    from relay.core.registry.store import DefinitionStore

    sessions = sessionmaker(bind=db_engine)
    errors: list[BaseException] = []

    def register() -> None:
        try:
            with sessions.begin() as s:
                DefinitionStore().register(
                    s, TOY_RACE, policies=PolicyService().policies(s), deps=None, actor="race"
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=register) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


TOY_RACE = TOY.replace("name: toy_onboarding", "name: toy_race")
