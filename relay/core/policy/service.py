"""Policy service: loads active packs, persists decisions, activates packs (PRD 12.1, 12.4, 12.6).

- Active packs = the files in `definitions/policies/` overlaid by the most recently activated
  `policy_versions` row per pack. A new activation is picked up on the very next `authorize()`
  call (one cheap `max(activated_at)` query), so no restart or deploy is needed.
- Kill switches are cached for at most `KILL_SWITCH_MAX_STALENESS` seconds (PRD: within 1s).
- Every `authorize()` writes a `decisions` row and hands an audit record to the audit hook. The
  hash-chained audit writer is WP2; until it is plugged in, the hook is a no-op.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relay.config import REPO_ROOT
from relay.core.policy.evaluate import EvaluationContext, KillSwitchState, evaluate
from relay.core.policy.packs import PackModel, PolicySet, content_hash, load_dir, parse_pack
from relay.core.policy.testing import CaseFile, CaseResult, load_case_files, run_cases
from relay.core.policy.types import ActionRequest, Decision
from relay.db import models as m

DEFAULT_POLICY_DIR = REPO_ROOT / "definitions" / "policies"
KILL_SWITCH_MAX_STALENESS = 0.5  # seconds

AuditHook = Callable[[Session, Mapping[str, Any]], None]


def _no_audit(session: Session, event: Mapping[str, Any]) -> None:
    """Placeholder until the WP2 hash-chained writer is registered."""


class PolicyTestFailure(ValueError):
    """Activation refused because at least one policy test failed."""

    def __init__(self, results: list[CaseResult]) -> None:
        self.results = results
        failed = [f"{r.pack}: {r.name}: {r.detail}" for r in results if not r.passed]
        super().__init__("policy tests failed:\n" + "\n".join(failed))


@dataclass
class _Cache:
    marker: datetime | None = None
    policies: PolicySet | None = None
    kills: KillSwitchState | None = None
    kills_at: float = 0.0


class PolicyService:
    """Entry point for `authorize()` and the policy lifecycle."""

    def __init__(
        self,
        policy_dir: Path = DEFAULT_POLICY_DIR,
        *,
        audit: AuditHook = _no_audit,
        confidence_thresholds: Mapping[str, float] | None = None,
    ) -> None:
        """`policy_dir` holds the bootstrap packs and `tests/`."""
        self.policy_dir = policy_dir
        self.audit = audit
        self.confidence_thresholds = dict(confidence_thresholds or {})
        self._bootstrap, _ = load_dir(policy_dir)
        self._cache = _Cache()

    # ------------------------------------------------------------ state

    def policies(self, session: Session) -> PolicySet:
        """The active policy set, reloaded only when a new activation exists."""
        marker = session.scalar(select(func.max(m.PolicyVersion.activated_at)))
        if self._cache.policies is None or marker != self._cache.marker:
            self._cache.policies = self._load_active(session)
            self._cache.marker = marker
        return self._cache.policies

    def _load_active(self, session: Session) -> PolicySet:
        policies = self._bootstrap
        latest = (
            select(m.PolicyVersion.pack, func.max(m.PolicyVersion.activated_at).label("at"))
            .where(m.PolicyVersion.activated_at.is_not(None))
            .group_by(m.PolicyVersion.pack)
            .subquery()
        )
        rows = session.scalars(
            select(m.PolicyVersion).join(
                latest,
                (m.PolicyVersion.pack == latest.c.pack)
                & (m.PolicyVersion.activated_at == latest.c.at),
            )
        )
        for row in rows:
            policies = policies.with_pack(parse_pack(row.content))
        return policies

    def kill_switches(self, session: Session, *, fresh: bool = False) -> KillSwitchState:
        """Active kill switches, at most `KILL_SWITCH_MAX_STALENESS` seconds old."""
        now = time.monotonic()
        if (
            fresh
            or self._cache.kills is None
            or now - self._cache.kills_at > KILL_SWITCH_MAX_STALENESS
        ):
            rows = session.execute(
                select(m.KillSwitch.scope, m.KillSwitch.target).where(m.KillSwitch.active)
            )
            self._cache.kills = KillSwitchState(frozenset((str(s), t) for s, t in rows))
            self._cache.kills_at = now
        return self._cache.kills

    def context(self, session: Session) -> EvaluationContext:
        """Evaluation context from current state."""
        return EvaluationContext(
            policies=self.policies(session),
            kill_switches=self.kill_switches(session),
            confidence_thresholds=self.confidence_thresholds,
        )

    # ------------------------------------------------------------ authorize

    def authorize(self, session: Session, req: ActionRequest) -> tuple[Decision, UUID]:
        """Decide `req`, persist the `decisions` row and audit it. Returns (decision, row id)."""
        decision = evaluate(req, self.context(session))
        row = m.Decision(
            request=req.model_dump(mode="json"),
            effect=m.Effect(decision.effect),
            approver_role=decision.approver_role,
            rule_ids=decision.rule_ids,
            policy_version=decision.policy_version,
            reason=decision.reason,
        )
        session.add(row)
        session.flush()
        self.audit(
            session,
            {
                "actor_type": req.actor.type,
                "actor_id": req.actor.id,
                "on_behalf_of": req.on_behalf_of,
                "action": f"policy.authorize:{req.action}",
                "subject_type": req.subject.type if req.subject else None,
                "subject_id": req.subject.id if req.subject else None,
                "run_id": req.run_id,
                "effect": decision.effect,
                "policy_version": decision.policy_version,
                "reasoning": decision.reason,
                "data": {"decision_id": str(row.id), "rule_ids": decision.rule_ids},
            },
        )
        return decision, row.id

    # ------------------------------------------------------------ lifecycle

    def test(
        self,
        session: Session,
        draft: PackModel | None = None,
        draft_tests: str | None = None,
    ) -> list[CaseResult]:
        """Run every policy test case against the active set.

        `draft` swaps in an edited pack; `draft_tests` (YAML case file) replaces the stored case
        file for its pack, so HR can change a threshold and its test cases together.
        """
        policies = self.policies(session)
        if draft is not None:
            policies = policies.with_pack(draft)
        files = load_case_files(self.policy_dir / "tests")
        if draft_tests is not None:
            override = CaseFile.model_validate(yaml.safe_load(draft_tests))
            files = [f for f in files if f.pack != override.pack] + [override]
        return run_cases(policies, files)

    def activate(
        self, session: Session, text: str, *, actor: str, draft_tests: str | None = None
    ) -> m.PolicyVersion:
        """Validate, test and activate a pack from YAML text. Applies to the next authorize()."""
        draft = parse_pack(text)
        current = getattr(self.policies(session), draft.pack)
        if draft.version <= current.version:
            raise ValueError(
                f"{draft.pack} version must increase (active {current.version}, got {draft.version})"
            )
        results = self.test(session, draft, draft_tests)
        if not all(r.passed for r in results):
            raise PolicyTestFailure(results)
        row = session.scalar(
            select(m.PolicyVersion).where(
                m.PolicyVersion.pack == draft.pack, m.PolicyVersion.version == draft.version
            )
        )
        if row is None:
            row = m.PolicyVersion(pack=draft.pack, version=draft.version)
            session.add(row)
        row.content = text
        row.content_hash = content_hash(text)
        row.tests_passed = True
        row.activated_by = actor
        row.activated_at = datetime.now(UTC)
        session.flush()
        self._audit_admin(session, actor, "policy.activate", draft.pack, {"version": draft.version})
        return row

    def rollback(self, session: Session, pack: str, version: int, *, actor: str) -> m.PolicyVersion:
        """Re-activate an earlier stored version (tests are re-run first)."""
        row = session.scalar(
            select(m.PolicyVersion).where(
                m.PolicyVersion.pack == pack, m.PolicyVersion.version == version
            )
        )
        if row is None:
            raise ValueError(f"no stored version {pack}@{version}")
        results = self.test(session, parse_pack(row.content))
        if not all(r.passed for r in results):
            raise PolicyTestFailure(results)
        row.activated_by = actor
        row.activated_at = datetime.now(UTC)
        session.flush()
        self._audit_admin(session, actor, "policy.rollback", pack, {"version": version})
        return row

    def set_kill_switch(
        self,
        session: Session,
        scope: str,
        target: str,
        *,
        active: bool,
        actor: str,
        note: str | None = None,
    ) -> m.KillSwitch:
        """Turn a kill switch on or off. Takes effect within `KILL_SWITCH_MAX_STALENESS`."""
        kscope = m.KillScope(scope)
        if kscope is m.KillScope.GLOBAL:
            target = "*"
        row = session.scalar(
            select(m.KillSwitch).where(m.KillSwitch.scope == kscope, m.KillSwitch.target == target)
        )
        if row is None:
            row = m.KillSwitch(scope=kscope, target=target, set_by=actor)
            session.add(row)
        row.active = active
        row.set_by = actor
        row.set_at = datetime.now(UTC)
        row.note = note
        session.flush()
        self._cache.kills = None
        self._audit_admin(
            session, actor, "kill_switch.set", f"{scope}:{target}", {"active": active, "note": note}
        )
        return row

    def _audit_admin(
        self, session: Session, actor: str, action: str, subject: str, data: dict[str, Any]
    ) -> None:
        self.audit(
            session,
            {
                "actor_type": "human",
                "actor_id": actor,
                "action": action,
                "subject_type": "policy",
                "subject_id": subject,
                "policy_version": self.policies(session).version,
                "data": data,
            },
        )
