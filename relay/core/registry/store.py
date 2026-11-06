"""Workflow definition registry: validate, store, activate, look up (PRD 9, 10.2, 11.1).

A stored `name@version` is immutable: registering the same version with different content is
refused. Activating a version retires the previously active version of that workflow; runs
already in flight keep the version they started with.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.config import REPO_ROOT
from relay.core.dsl.model import WorkflowDefinition, definition_hash, parse_definition
from relay.core.engine.ports import EngineDeps
from relay.core.policy.packs import PolicySet
from relay.core.registry.validate import validate_definition
from relay.db import models as m

DEFAULT_WORKFLOW_DIR = REPO_ROOT / "definitions" / "workflows"


class InvalidDefinition(ValueError):
    """The definition failed registry validation and cannot be activated."""

    def __init__(self, name: str, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{name} is invalid:\n- " + "\n- ".join(errors))


class DefinitionStore:
    """Caches parsed definitions; the database is the source of truth."""

    def __init__(self) -> None:
        """Start with an empty parse cache."""
        self._parsed: dict[tuple[str, int], WorkflowDefinition] = {}

    def register(
        self,
        session: Session,
        text: str,
        *,
        policies: PolicySet,
        deps: EngineDeps | None,
        actor: str,
        activate: bool = True,
    ) -> m.WorkflowDefinition:
        """Validate and store a definition, optionally activating it."""
        wf = parse_definition(text)
        errors = validate_definition(wf, policies, deps)
        if errors:
            raise InvalidDefinition(f"{wf.name}@{wf.version}", errors)
        digest = definition_hash(text)
        row = session.scalar(
            select(m.WorkflowDefinition).where(
                m.WorkflowDefinition.name == wf.name, m.WorkflowDefinition.version == wf.version
            )
        )
        if row is not None and row.content_hash != digest:
            raise InvalidDefinition(
                f"{wf.name}@{wf.version}",
                ["this version is already stored with different content; bump `version`"],
            )
        if row is None:
            row = m.WorkflowDefinition(
                name=wf.name, version=wf.version, content_hash=digest, yaml=text
            )
            session.add(row)
            session.flush()
        if activate and row.status is not m.DefinitionStatus.ACTIVE:
            for other in session.scalars(
                select(m.WorkflowDefinition).where(
                    m.WorkflowDefinition.name == wf.name,
                    m.WorkflowDefinition.status == m.DefinitionStatus.ACTIVE,
                )
            ):
                other.status = m.DefinitionStatus.RETIRED
            row.status = m.DefinitionStatus.ACTIVE
            row.activated_by = actor
            row.activated_at = datetime.now(UTC)
        session.flush()
        self._parsed[(wf.name, wf.version)] = wf
        return row

    def register_dir(
        self,
        session: Session,
        directory: Path = DEFAULT_WORKFLOW_DIR,
        *,
        policies: PolicySet,
        deps: EngineDeps | None,
        actor: str,
    ) -> list[m.WorkflowDefinition]:
        """Register and activate every `*.yaml` in `directory` (sorted, so v2 activates after v1)."""
        return [
            self.register(
                session, p.read_text(encoding="utf-8"), policies=policies, deps=deps, actor=actor
            )
            for p in sorted(directory.glob("*.yaml"))
        ]

    def get(self, session: Session, name: str, version: int) -> WorkflowDefinition:
        """A stored definition by name and version."""
        key = (name, version)
        if key not in self._parsed:
            row = session.scalar(
                select(m.WorkflowDefinition).where(
                    m.WorkflowDefinition.name == name, m.WorkflowDefinition.version == version
                )
            )
            if row is None:
                raise KeyError(f"no workflow definition {name}@{version}")
            self._parsed[key] = parse_definition(row.yaml)
        return self._parsed[key]

    def active_for_event(self, session: Session, event: str) -> list[WorkflowDefinition]:
        """Active definitions triggered by `event`."""
        rows = session.scalars(
            select(m.WorkflowDefinition).where(
                m.WorkflowDefinition.status == m.DefinitionStatus.ACTIVE
            )
        )
        found = [self.get(session, r.name, r.version) for r in rows]
        return [wf for wf in found if wf.trigger.event == event]

    def active(self, session: Session, name: str) -> WorkflowDefinition:
        """The active version of workflow `name`."""
        row = session.scalar(
            select(m.WorkflowDefinition).where(
                m.WorkflowDefinition.name == name,
                m.WorkflowDefinition.status == m.DefinitionStatus.ACTIVE,
            )
        )
        if row is None:
            raise KeyError(f"no active workflow {name!r}")
        return self.get(session, row.name, row.version)
