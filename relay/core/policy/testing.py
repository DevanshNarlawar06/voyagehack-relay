"""Policy test runner (PRD 12.4): YAML cases in `definitions/policies/tests/`.

Case format::

    pack: approvals
    cases:
      - name: expensive laptop needs approval
        request: { action: ..., actor: {type: system, id: engine, roles: [system]}, ... }
        expect: { effect: REQUIRE_HUMAN, approver_role: approver_it_spend }
        kill_switches: [[workflow, onboarding]]          # optional
        confidence_thresholds: { intake: 0.8 }            # optional
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from relay.core.policy.evaluate import EvaluationContext, KillSwitchState, evaluate
from relay.core.policy.packs import PolicySet
from relay.core.policy.types import ActionRequest


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Expectation(_Model):
    effect: str
    approver_role: str | None = None
    rule_ids: list[str] | None = None


class PolicyCase(_Model):
    name: str
    request: ActionRequest
    expect: Expectation
    kill_switches: list[tuple[str, str]] = Field(default_factory=list)
    confidence_thresholds: dict[str, float] = Field(default_factory=dict)


class CaseFile(_Model):
    pack: str
    cases: list[PolicyCase]


@dataclass(frozen=True)
class CaseResult:
    pack: str
    name: str
    passed: bool
    detail: str


def load_case_files(directory: Path) -> list[CaseFile]:
    """Load every `*.yaml` case file in `directory`, sorted by name."""
    files: list[CaseFile] = []
    for path in sorted(directory.glob("*.yaml")):
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        files.append(CaseFile.model_validate(data))
    return files


def run_case(policies: PolicySet, pack: str, case: PolicyCase) -> CaseResult:
    """Evaluate one case and compare against its expectation."""
    ctx = EvaluationContext(
        policies=policies,
        kill_switches=KillSwitchState(frozenset(case.kill_switches)),
        confidence_thresholds=case.confidence_thresholds,
    )
    got = evaluate(case.request, ctx)
    exp = case.expect
    problems = []
    if got.effect != exp.effect:
        problems.append(f"effect {got.effect} != {exp.effect}")
    if exp.approver_role is not None and got.approver_role != exp.approver_role:
        problems.append(f"approver_role {got.approver_role} != {exp.approver_role}")
    if exp.rule_ids is not None and got.rule_ids != exp.rule_ids:
        problems.append(f"rule_ids {got.rule_ids} != {exp.rule_ids}")
    detail = "; ".join(problems) or got.reason
    return CaseResult(pack, case.name, not problems, detail)


def run_cases(
    policies: PolicySet, files: list[CaseFile], pack: str | None = None
) -> list[CaseResult]:
    """Run every case (optionally only those for `pack`)."""
    return [
        run_case(policies, f.pack, c)
        for f in files
        if pack is None or f.pack == pack
        for c in f.cases
    ]
