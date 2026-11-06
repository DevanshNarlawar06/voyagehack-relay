"""Pure policy evaluation: `ActionRequest` + policy state -> `Decision` (PRD 12.2).

Order (deny overrides): kill switch, RBAC (agents: principal ∩ skill allowlist), DPDP purpose,
reserved floor and route-only areas, approval thresholds, confidence gate, statutory assertions,
otherwise ALLOW. Rule conditions that cannot be evaluated fail closed: the rule fires.

No I/O here; `relay.core.policy.service` loads state and persists the decision.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from relay.core.policy import cel
from relay.core.policy.baseline import is_route_action
from relay.core.policy.matching import matches_any
from relay.core.policy.packs import ConditionalRule, PolicySet
from relay.core.policy.types import ActionRequest, Decision

CONFIDENCE_FALLBACK_ROLE = "hr_ops"


@dataclass(frozen=True)
class KillSwitchState:
    """Active kill switches as (scope, target) pairs; target `*` matches everything."""

    active: frozenset[tuple[str, str]] = frozenset()

    def hit(self, req: ActionRequest) -> tuple[str, str] | None:
        """Return the first active switch matching `req`, if any.

        Step switches may target `step_id` or `workflow.step_id`.
        """
        values = {
            "workflow": req.workflow,
            "step": req.step_id,
            "skill": req.skill,
            "connector": req.connector,
        }
        for scope, target in sorted(self.active):
            if scope == "global":
                return scope, target
            value = values.get(scope)
            if not value:
                continue
            qualified = f"{req.workflow}.{value}" if scope == "step" else value
            if target in ("*", value, qualified):
                return scope, target
        return None


@dataclass(frozen=True)
class EvaluationContext:
    """Everything `evaluate` needs besides the request."""

    policies: PolicySet
    kill_switches: KillSwitchState = field(default_factory=KillSwitchState)
    confidence_thresholds: Mapping[str, float] = field(default_factory=dict)


def _decision(
    ctx: EvaluationContext,
    effect: str,
    reason: str,
    rule_ids: Iterable[str],
    approver_role: str | None = None,
) -> Decision:
    return Decision.model_validate(
        {
            "effect": effect,
            "approver_role": approver_role,
            "rule_ids": list(rule_ids),
            "reason": reason,
            "policy_version": ctx.policies.version,
        }
    )


def _activation(req: ActionRequest) -> dict[str, Any]:
    return {
        "action": req.action,
        "actor": req.actor.model_dump(),
        "attributes": req.attributes,
        "resource": req.resource,
        "subject": req.subject.model_dump() if req.subject else {},
    }


def _fires(rule: ConditionalRule, activation: dict[str, Any]) -> tuple[bool, str]:
    try:
        return cel.evaluate_bool(rule.when, activation), ""
    except cel.CelError as exc:
        return True, f" (condition could not be evaluated, failing closed: {exc})"


def _rbac_allows(req: ActionRequest, ctx: EvaluationContext) -> tuple[bool, str]:
    rbac = ctx.policies.rbac
    actor = req.actor
    if actor.type == "agent":
        if not req.principal_roles:
            return False, "agent has no principal to act on behalf of (PRD 6.7)"
        if actor.allowed_actions is None or not matches_any(actor.allowed_actions, req.action):
            return False, f"{req.action} is not in the skill allowlist"
        if not rbac.permits(req.principal_roles, req.action):
            return False, f"principal roles {req.principal_roles} do not permit {req.action}"
        return True, ""
    if not rbac.permits(actor.roles, req.action):
        return False, f"roles {actor.roles} do not permit {req.action}"
    return True, ""


def evaluate(req: ActionRequest, ctx: EvaluationContext) -> Decision:
    """Decide `req` under `ctx`. Pure and deterministic."""
    pol = ctx.policies
    actor = req.actor

    # 0. Engine-originated requests must say where they come from, or kill switches could miss
    #    them (issue #32).
    if req.run_id is not None and not (req.workflow and req.step_id):
        return _decision(
            ctx,
            "DENY",
            "request from a run must name its workflow and step",
            ["incomplete_request"],
        )

    # 1. Kill switch
    hit = ctx.kill_switches.hit(req)
    if hit:
        return _decision(ctx, "DENY", f"kill_switch active: {hit[0]}={hit[1]}", ["kill_switch"])

    # 2. RBAC
    ok, why = _rbac_allows(req, ctx)
    if not ok:
        return _decision(ctx, "DENY", why, ["rbac"])

    # 2b. DPDP purpose limitation (PRD 14.2)
    purpose = pol.rbac.purpose_of(req.action)
    if purpose and purpose not in pol.rbac.legitimate_use_purposes:
        consented = req.attributes.get("consented_purposes")
        if not isinstance(consented, list) or purpose not in consented:
            return _decision(
                ctx, "DENY", f"no consent recorded for purpose {purpose!r}", ["dpdp_purpose"]
            )

    # 3. Route-only areas, then the reserved floor
    if pol.is_route_only(req.action) and not is_route_action(req.action) and actor.type != "human":
        return _decision(
            ctx,
            "DENY",
            f"{req.action} is route-only: agents may route, never decide",
            ["route_only"],
        )
    approver = pol.reserved_approver(req.action)
    if approver is not None and (actor.type != "human" or approver not in actor.roles):
        return _decision(
            ctx,
            "REQUIRE_HUMAN",
            f"{req.action} is a reserved decision for {approver}",
            ["reserved"],
            approver,
        )

    activation = _activation(req)

    # 4. Approval thresholds (a human holding the approver role is the approval)
    for rule in pol.approvals.rules:
        if not rule.applies(req.action):
            continue
        if (
            rule.effect == "REQUIRE_HUMAN"
            and actor.type == "human"
            and rule.approver_role in actor.roles
        ):
            continue
        fired, note = _fires(rule, activation)
        if fired:
            return _decision(
                ctx, rule.effect, f"{rule.description} [{rule.source}]{note}", [rule.id],
                rule.approver_role,
            )  # fmt: skip

    # 5. Confidence gate for skill outputs
    if req.skill and "confidence" in req.attributes:
        threshold = ctx.confidence_thresholds.get(req.skill)
        confidence = req.attributes["confidence"]
        valid = (
            isinstance(confidence, int | float)
            and not isinstance(confidence, bool)
            and math.isfinite(confidence)
        )
        if threshold is not None and (not valid or confidence < threshold):
            return _decision(
                ctx,
                "REQUIRE_HUMAN",
                f"{req.skill} confidence {confidence} below threshold {threshold}",
                ["confidence_gate"],
                CONFIDENCE_FALLBACK_ROLE,
            )

    # 6. Statutory and policy assertions
    for rule in pol.labour_rules.assertions:
        if rule.applies(req.action):
            fired, note = _fires(rule, activation)
            if fired:
                return _decision(
                    ctx, rule.effect, f"{rule.description} [{rule.source}]{note}", [rule.id],
                    rule.approver_role,
                )  # fmt: skip

    # 7. Allow
    return _decision(ctx, "ALLOW", "all policy checks passed", ["allow"])
