"""Registered rule functions (PRD 7, 12.5). Workflows reference them by name in `uses`."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from relay.core.policy import PolicyService
from relay.rules import it, labour
from relay.rules.dpdp import DpdpRules
from relay.rules.exit import ExitRules
from relay.rules.hr import HrRules
from relay.rules.retention import RetentionRules


def build_rules(
    sessions: sessionmaker[Session],
    policy: PolicyService | None = None,
    storage_dir: Path | None = None,
) -> dict[str, Callable[..., Mapping[str, Any]]]:
    """Every rule function, bound to the database, the active policy tables and the file store."""
    policies = policy or PolicyService()
    store = storage_dir or Path(os.environ.get("RELAY_STORAGE_DIR", "storage/documents"))

    def retention_categories() -> Mapping[str, Any]:
        with sessions() as s:
            return policies.policies(s).retention.categories

    def labour_tables() -> Mapping[str, Any]:
        with sessions() as s:
            return policies.policies(s).labour_rules.tables

    def compute_onboarding_terms(joining_date: str, grade: str) -> Mapping[str, Any]:
        return labour.compute_onboarding_terms(joining_date, grade, labour_tables())

    return {
        **HrRules(sessions).functions(),
        **ExitRules(sessions, labour_tables).functions(),
        **DpdpRules(sessions).functions(),
        **RetentionRules(sessions, retention_categories, store).functions(),
        "rules.labour.compute_onboarding_terms": compute_onboarding_terms,
        "rules.it.select_laptop": it.select_laptop,
    }
