"""Registered rule functions (PRD 7, 12.5). Workflows reference them by name in `uses`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from relay.core.policy import PolicyService
from relay.rules import it, labour
from relay.rules.hr import HrRules


def build_rules(
    sessions: sessionmaker[Session], policy: PolicyService | None = None
) -> dict[str, Callable[..., Mapping[str, Any]]]:
    """Every rule function, bound to the database and the active labour_rules tables."""
    policies = policy or PolicyService()

    def labour_tables() -> Mapping[str, Any]:
        with sessions() as s:
            return policies.policies(s).labour_rules.tables

    def compute_onboarding_terms(joining_date: str, grade: str) -> Mapping[str, Any]:
        return labour.compute_onboarding_terms(joining_date, grade, labour_tables())

    return {
        **HrRules(sessions).functions(),
        "rules.labour.compute_onboarding_terms": compute_onboarding_terms,
        "rules.it.select_laptop": it.select_laptop,
    }
