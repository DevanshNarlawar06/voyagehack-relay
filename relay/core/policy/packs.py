"""Policy pack schemas and the composed `PolicySet` (PRD 12.3, 12.5, 14.2).

Packs are YAML in `definitions/policies/`. Each has `pack`, an integer `version` and pack-specific
content. Loading validates schemas, compiles every CEL condition and checks that `reserved.yaml`
only adds to the baseline floor.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relay.core.policy import cel
from relay.core.policy.baseline import BASELINE_RESERVED, BASELINE_ROUTE_ONLY
from relay.core.policy.matching import matches, matches_any

PACK_NAMES = ("reserved", "approvals", "rbac", "labour_rules", "retention")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _PackHeader(_Model):
    pack: str
    version: int = Field(ge=1)
    owner: str = "hr_admin"
    description: str | None = None


class ConditionalRule(_Model):
    """A rule that fires when `when` (CEL) is true for a matching action."""

    id: str
    description: str
    applies_to: list[str] = Field(min_length=1)
    when: str = "true"
    effect: Literal["REQUIRE_HUMAN", "DENY"]
    approver_role: str | None = None
    source: str

    @field_validator("when")
    @classmethod
    def _compiles(cls, v: str) -> str:
        cel.check(v)
        return v

    @model_validator(mode="after")
    def _approver_for_require_human(self) -> ConditionalRule:
        if self.effect == "REQUIRE_HUMAN" and not self.approver_role:
            raise ValueError(f"rule {self.id}: REQUIRE_HUMAN needs approver_role")
        return self

    def applies(self, action: str) -> bool:
        """True if this rule covers `action`."""
        return matches_any(self.applies_to, action)


class ReservedEntry(_Model):
    action: str
    approver_role: str


class ReservedPack(_PackHeader):
    """HR-editable additions to the immutable reserved floor."""

    pack: Literal["reserved"]
    reserved: list[ReservedEntry] = Field(default_factory=list)
    route_only: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _only_adds(self) -> ReservedPack:
        for entry in self.reserved:
            for base in BASELINE_RESERVED:
                if matches(base, entry.action) or matches(entry.action, base):
                    raise ValueError(
                        f"reserved.yaml cannot redefine baseline-reserved {entry.action!r}; "
                        "the baseline floor is immutable (PRD 6.3)"
                    )
        return self


class ApprovalsPack(_PackHeader):
    pack: Literal["approvals"]
    rules: list[ConditionalRule] = Field(default_factory=list)


class ActionSpec(_Model):
    description: str = ""
    purpose: str | None = None  # DPDP purpose this action processes data for


class RbacPack(_PackHeader):
    """Roles -> permitted action patterns, the action catalogue and DPDP purposes."""

    pack: Literal["rbac"]
    roles: dict[str, list[str]]
    actions: dict[str, ActionSpec] = Field(default_factory=dict)
    purposes: dict[str, str] = Field(default_factory=dict)
    legitimate_use_purposes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _purposes_declared(self) -> RbacPack:
        known = set(self.purposes)
        used = {a.purpose for a in self.actions.values() if a.purpose}
        undeclared = (used | set(self.legitimate_use_purposes)) - known
        if undeclared:
            raise ValueError(f"purposes used but not declared: {sorted(undeclared)}")
        return self

    def permits(self, roles: list[str], action: str) -> bool:
        """True if any of `roles` grants `action`."""
        return any(matches_any(self.roles.get(r, []), action) for r in roles)

    def purpose_of(self, action: str) -> str | None:
        """DPDP purpose for `action`, if declared."""
        spec = self.actions.get(action)
        return spec.purpose if spec else None


class LabourRulesPack(_PackHeader):
    """Decision tables read by rule functions, plus statutory assertions (DENY rules)."""

    pack: Literal["labour_rules"]
    tables: dict[str, Any] = Field(default_factory=dict)
    assertions: list[ConditionalRule] = Field(default_factory=list)


class RetentionPack(_PackHeader):
    pack: Literal["retention"]
    categories: dict[str, Any] = Field(default_factory=dict)


PackModel = ReservedPack | ApprovalsPack | RbacPack | LabourRulesPack | RetentionPack
_PACK_TYPES: dict[str, type[PackModel]] = {
    "reserved": ReservedPack,
    "approvals": ApprovalsPack,
    "rbac": RbacPack,
    "labour_rules": LabourRulesPack,
    "retention": RetentionPack,
}


def content_hash(text: str) -> str:
    """sha256 of pack source text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_pack(text: str) -> PackModel:
    """Parse and validate one pack from YAML text."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or "pack" not in data:
        raise ValueError("a policy pack must be a mapping with a `pack` key")
    kind = data["pack"]
    if kind not in _PACK_TYPES:
        raise ValueError(f"unknown policy pack {kind!r}; expected one of {PACK_NAMES}")
    return _PACK_TYPES[kind].model_validate(data)


class PolicySet(_Model):
    """One version of every pack, as consulted by `authorize()`."""

    reserved: ReservedPack
    approvals: ApprovalsPack
    rbac: RbacPack
    labour_rules: LabourRulesPack
    retention: RetentionPack

    @property
    def version(self) -> str:
        """Composite version, e.g. `approvals@3,labour_rules@1,...`."""
        return ",".join(f"{n}@{getattr(self, n).version}" for n in sorted(PACK_NAMES))

    def reserved_approver(self, action: str) -> str | None:
        """Approver role if `action` is reserved (baseline first, then config additions)."""
        from relay.core.policy.baseline import baseline_approver

        role = baseline_approver(action)
        if role:
            return role
        for entry in self.reserved.reserved:
            if matches(entry.action, action):
                return entry.approver_role
        return None

    def is_route_only(self, action: str) -> bool:
        """True if `action` is in a route-only area (POSH, grievance, whistleblower, ...)."""
        return matches_any((*BASELINE_ROUTE_ONLY, *self.reserved.route_only), action)

    def with_pack(self, pack: PackModel) -> PolicySet:
        """Copy with one pack replaced (used to test a draft against the active set)."""
        return self.model_copy(update={pack.pack: pack})

    @classmethod
    def from_mapping(cls, packs: Mapping[str, PackModel]) -> PolicySet:
        """Build from a name -> pack mapping; every pack must be present."""
        missing = [n for n in PACK_NAMES if n not in packs]
        if missing:
            raise ValueError(f"missing policy packs: {missing}")
        return cls.model_validate({n: packs[n] for n in PACK_NAMES})


def load_dir(directory: Path) -> tuple[PolicySet, dict[str, str]]:
    """Load every `<pack>.yaml` in `directory`. Returns the set and each pack's source text."""
    packs: dict[str, PackModel] = {}
    sources: dict[str, str] = {}
    for name in PACK_NAMES:
        text = (directory / f"{name}.yaml").read_text(encoding="utf-8")
        pack = parse_pack(text)
        if pack.pack != name:
            raise ValueError(f"{name}.yaml declares pack {pack.pack!r}")
        packs[name] = pack
        sources[name] = text
    return PolicySet.from_mapping(packs), sources
