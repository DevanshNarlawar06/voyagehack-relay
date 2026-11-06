"""Immutable reserved-decision floor (PRD 6.3, 12.3, v1.1 fair hiring).

These actions always need a named human holding the approver role. Configuration
(`definitions/policies/reserved.yaml`) can only ADD to this list; it cannot remove or re-route
anything here. Agents and system actors can never receive ALLOW on these actions.
"""

from __future__ import annotations

from types import MappingProxyType

# action pattern -> approver role
BASELINE_RESERVED: MappingProxyType[str, str] = MappingProxyType(
    {
        # Hiring (incl. fair hiring: no automated rejection without human review)
        "hiring.offer.approve": "approver_offer",
        "hiring.final_decision": "approver_offer",
        "hiring.candidate.reject": "approver_offer",
        "hiring.candidate.shortlist_decision": "approver_offer",
        # Compensation, grade, role, exceptions
        "comp.*": "approver_finance",
        "employment.grade_change": "hr_admin",
        "employment.role_change": "hr_admin",
        "policy.exception.*": "hr_admin",
        # Separation and discipline
        "employment.resignation.accept": "manager",
        "employment.release_date.agree": "manager",
        "employment.notice_buyout.agree": "manager",
        "employment.terminate": "hr_admin",
        "employment.disciplinary.*": "hr_admin",
        "performance.pip.*": "hr_admin",
        # Statutory and payroll sign-off
        "statutory.filing.signoff": "approver_finance",
        "payroll.run.signoff": "approver_finance",
        "payroll.fnf.signoff": "approver_finance",
    }
)

# Route only, never decide: agents and systems may only perform `<area>.route` / `<area>.*.route`.
BASELINE_ROUTE_ONLY: tuple[str, ...] = ("posh.*", "grievance.*", "whistleblower.*")


def baseline_approver(action: str) -> str | None:
    """Approver role if `action` is baseline-reserved, else None."""
    from relay.core.policy.matching import matches

    for pattern, role in BASELINE_RESERVED.items():
        if matches(pattern, action):
            return role
    return None


def is_route_action(action: str) -> bool:
    """True for routing actions, which are the only thing agents may do in route-only areas."""
    return action.endswith(".route")
