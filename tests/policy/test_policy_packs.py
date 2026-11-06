"""Policy packs, the YAML case suite, and the reserved-floor property (PRD 12, 21)."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from relay.core.policy import ActionRequest, Actor, EvaluationContext, KillSwitchState, evaluate
from relay.core.policy.baseline import BASELINE_RESERVED, BASELINE_ROUTE_ONLY
from relay.core.policy.packs import PolicySet, load_dir, parse_pack
from relay.core.policy.service import DEFAULT_POLICY_DIR
from relay.core.policy.testing import load_case_files

POLICIES, _ = load_dir(DEFAULT_POLICY_DIR)
CASE_FILES = load_case_files(DEFAULT_POLICY_DIR / "tests")


@pytest.mark.parametrize(
    ("pack", "case"),
    [(f.pack, c) for f in CASE_FILES for c in f.cases],
    ids=lambda x: x.name if hasattr(x, "name") else x,
)
def test_yaml_policy_case(pack: str, case: object) -> None:
    from relay.core.policy.testing import PolicyCase, run_case

    assert isinstance(case, PolicyCase)
    result = run_case(POLICIES, pack, case)
    assert result.passed, result.detail


def test_suite_exercises_every_evaluation_step() -> None:
    from relay.core.policy.testing import PolicyCase

    seen: set[str] = set()
    for f in CASE_FILES:
        for case in f.cases:
            assert isinstance(case, PolicyCase)
            ctx = EvaluationContext(
                policies=POLICIES,
                kill_switches=KillSwitchState(frozenset(case.kill_switches)),
                confidence_thresholds=case.confidence_thresholds,
            )
            seen.update(evaluate(case.request, ctx).rule_ids)
    expected = {
        "kill_switch", "rbac", "dpdp_purpose", "route_only", "reserved",
        "laptop_high_spend", "confidence_gate", "payroll_uses_approved_ctc", "allow",
    }  # fmt: skip
    assert expected <= seen, expected - seen


def test_composite_version_names_every_pack() -> None:
    assert POLICIES.version == ("approvals@1,labour_rules@1,rbac@1,reserved@1,retention@1")


def test_reserved_pack_cannot_redefine_baseline() -> None:
    with pytest.raises(ValidationError, match="immutable"):
        parse_pack(
            "pack: reserved\nversion: 2\nreserved:\n"
            "  - {action: hiring.offer.approve, approver_role: manager}\n"
        )


def test_reserved_pack_cannot_shadow_baseline_with_wildcard() -> None:
    with pytest.raises(ValidationError, match="immutable"):
        parse_pack(
            "pack: reserved\nversion: 2\nreserved:\n  - {action: 'comp.*', approver_role: x}\n"
        )


def test_bad_cel_is_rejected_at_load() -> None:
    with pytest.raises(ValidationError, match="CEL"):
        parse_pack(
            "pack: approvals\nversion: 2\nrules:\n"
            "  - {id: r, description: d, applies_to: [a], when: 'attributes.x >', "
            "effect: DENY, source: s}\n"
        )


def test_require_human_rule_needs_an_approver() -> None:
    with pytest.raises(ValidationError, match="approver_role"):
        parse_pack(
            "pack: approvals\nversion: 2\nrules:\n"
            "  - {id: r, description: d, applies_to: [a], effect: REQUIRE_HUMAN, source: s}\n"
        )


def test_unknown_pack_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown policy pack"):
        parse_pack("pack: vibes\nversion: 1\n")


def test_load_dir_requires_every_pack(tmp_path: Path) -> None:
    (tmp_path / "approvals.yaml").write_text("pack: approvals\nversion: 1\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_dir(tmp_path)


# ---------------------------------------------------------------- reserved floor property

_ROLES = [*POLICIES.rbac.roles, "approver_offer", "random_role"]


def _reserved_actions() -> st.SearchStrategy[str]:
    def concretize(pattern: str) -> st.SearchStrategy[str]:
        if "*" not in pattern:
            return st.just(pattern)
        suffix = st.text("abcdefghijklmnopqrstuvwxyz_.", min_size=1, max_size=12).filter(
            lambda s: not s.startswith(".") and not s.endswith(".")
        )
        return suffix.map(lambda s: pattern.replace("*", s))

    return st.sampled_from(sorted(BASELINE_RESERVED)).flatmap(concretize)


@settings(max_examples=10_000, deadline=None)
@given(
    action=_reserved_actions(),
    actor_type=st.sampled_from(["agent", "system"]),
    roles=st.lists(st.sampled_from(_ROLES), max_size=4),
    principal=st.one_of(st.none(), st.lists(st.sampled_from(_ROLES), max_size=4)),
    allowlist=st.one_of(
        st.none(),
        st.just(["*"]),
        st.lists(
            st.sampled_from(["*", "hiring.*", "comp.*", "employment.*", "payroll.*"]), max_size=3
        ),
    ),
    amount=st.integers(min_value=0, max_value=10_000_000),
    confidence=st.floats(min_value=0, max_value=1),
)
def test_no_agent_or_system_actor_is_ever_allowed_a_reserved_action(
    action: str,
    actor_type: str,
    roles: list[str],
    principal: list[str] | None,
    allowlist: list[str] | None,
    amount: int,
    confidence: float,
) -> None:
    req = ActionRequest(
        action=action,
        actor=Actor(type=actor_type, id=f"{actor_type}:x", roles=roles, allowed_actions=allowlist),  # type: ignore[arg-type]
        principal_roles=principal,
        attributes={"amount_inr": amount, "confidence": confidence, "ctc_matches_offer": True},
    )
    decision = evaluate(req, EvaluationContext(policies=POLICIES))
    assert decision.effect != "ALLOW", (action, decision)


@settings(max_examples=2_000, deadline=None)
@given(
    area=st.sampled_from(list(BASELINE_ROUTE_ONLY)),
    verb=st.sampled_from(["decide", "close", "dismiss", "case.decide", "outcome.set"]),
    actor_type=st.sampled_from(["agent", "system"]),
)
def test_agents_never_decide_route_only_areas(area: str, verb: str, actor_type: str) -> None:
    action = area.replace("*", verb)
    req = ActionRequest(
        action=action,
        actor=Actor(type=actor_type, id="x", roles=["hr_admin"], allowed_actions=["*"]),  # type: ignore[arg-type]
        principal_roles=["hr_admin"],
    )
    assert evaluate(req, EvaluationContext(policies=POLICIES)).effect == "DENY"


def test_kill_switch_beats_everything() -> None:
    req = ActionRequest(
        action="hiring.offer.approve",
        actor=Actor(type="human", id="u", roles=["approver_offer"]),
        workflow="onboarding",
    )
    ctx = EvaluationContext(
        policies=POLICIES, kill_switches=KillSwitchState(frozenset({("workflow", "onboarding")}))
    )
    decision = evaluate(req, ctx)
    assert decision.effect == "DENY" and decision.is_kill_switch


def test_policy_set_swap_changes_outcome() -> None:
    stricter = parse_pack(
        (DEFAULT_POLICY_DIR / "approvals.yaml")
        .read_text(encoding="utf-8")
        .replace("version: 1", "version: 2")
        .replace("> 150000", "> 50000")
    )
    req = ActionRequest(
        action="assets.laptop.allocate",
        actor=Actor(type="system", id="engine", roles=["system"]),
        attributes={"amount_inr": 90000},
    )
    assert evaluate(req, EvaluationContext(policies=POLICIES)).effect == "ALLOW"
    swapped: PolicySet = POLICIES.with_pack(stricter)
    assert evaluate(req, EvaluationContext(policies=swapped)).effect == "REQUIRE_HUMAN"


def test_amount_exactly_at_threshold_is_allowed() -> None:
    req = ActionRequest(
        action="assets.laptop.allocate",
        actor=Actor(type="system", id="engine", roles=["system"]),
        attributes={"amount_inr": 150000},
    )
    assert evaluate(req, EvaluationContext(policies=POLICIES)).effect == "ALLOW"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "0.99", None])
def test_confidence_gate_fails_closed_on_invalid_values(bad: object) -> None:
    """Issue #32.3: NaN, infinities, booleans and non-numbers never pass the gate."""
    req = ActionRequest(
        action="documents.extract",
        actor=Actor(type="agent", id="agent:x", allowed_actions=["documents.extract"]),
        principal_roles=["hr_ops"],
        skill="document_extractor",
        attributes={"confidence": bad, "consented_purposes": ["onboarding_verification"]},
    )
    ctx = EvaluationContext(policies=POLICIES, confidence_thresholds={"document_extractor": 0.82})
    decision = evaluate(req, ctx)
    assert decision.effect == "REQUIRE_HUMAN" and decision.rule_ids == ["confidence_gate"]


def test_run_request_without_workflow_or_step_is_denied() -> None:
    """Issue #32.4: a request from a run that omits workflow/step cannot dodge kill switches."""
    from uuid import uuid4

    req = ActionRequest(
        action="employee.create",
        actor=Actor(type="system", id="engine", roles=["system"]),
        run_id=uuid4(),
        step_id="create_prehire",
    )
    decision = evaluate(req, EvaluationContext(policies=POLICIES))
    assert decision.effect == "DENY" and decision.rule_ids == ["incomplete_request"]
    ok = req.model_copy(update={"workflow": "onboarding"})
    assert evaluate(ok, EvaluationContext(policies=POLICIES)).effect == "ALLOW"


def test_global_kill_switch_never_locks_hr_out() -> None:
    """Issue #38: HR can release a global switch and read audit while it is on."""
    from uuid import uuid4

    ctx = EvaluationContext(
        policies=POLICIES, kill_switches=KillSwitchState(frozenset({("global", "*")}))
    )
    hr = Actor(type="human", id="u-hradmin", roles=["hr_admin"])
    for action in ("kill_switch.set", "audit.read", "runs.read"):
        assert evaluate(ActionRequest(action=action, actor=hr), ctx).effect == "ALLOW", action
    in_run = ActionRequest(
        action="assets.laptop.allocate",
        actor=Actor(type="system", id="engine", roles=["system"]),
        run_id=uuid4(), workflow="onboarding", step_id="order_laptop",
        attributes={"amount_inr": 1},
    )  # fmt: skip
    assert evaluate(in_run, ctx).is_kill_switch
    human_on_paused_run = ActionRequest(
        action="tasks.decide", actor=hr, run_id=uuid4(), workflow="onboarding", step_id="x"
    )
    assert evaluate(human_on_paused_run, ctx).is_kill_switch
    agent_outside_run = ActionRequest(
        action="chat.answer",
        actor=Actor(type="agent", id="agent:helpdesk", allowed_actions=["chat.*"]),
        principal_roles=["employee"], skill="helpdesk",
    )  # fmt: skip
    assert evaluate(agent_outside_run, ctx).is_kill_switch
