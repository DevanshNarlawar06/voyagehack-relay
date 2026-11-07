"""DPDP retention (PRD 14): find documents past their retention and purge them.

`retention.yaml` says, per document category, while which employee states a document is kept and
for how long after that. The sweeper workflow calls `find_expired` and then `purge`. A purge
deletes the encrypted file and clears the extracted fields; the step's audited result names each
purged document.
When the rules can't tell (no employee record, no exit date), the document is kept: never guess
towards deleting.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relay.db import models as m

PURGED = "purged"


def keep_until(
    rule: Mapping[str, Any],
    state: str | None,
    last_working_day: date | None,
    uploaded: date,
) -> date | None:
    """The last day a document must be kept, or None while it must be kept or nobody can tell.

    `state` is the employee's state (None when there is no employee record).
    """
    if state is None or state in rule.get("while", []):
        return None
    if "after_verification_days" in rule:
        return uploaded + timedelta(days=int(rule["after_verification_days"]))
    if "after_exit_days" in rule:
        if last_working_day is None:
            return None
        return last_working_day + timedelta(days=int(rule["after_exit_days"]))
    return None


class RetentionRules:
    """Retention rule functions bound to the database, the active retention pack and file store."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        categories: Callable[[], Mapping[str, Any]],
        storage_dir: Path,
    ) -> None:
        """Bind the database, a reader for retention.yaml's categories, and the document store."""
        self.sessions = sessions
        self.categories = categories
        self.storage_dir = storage_dir

    def functions(self) -> dict[str, Callable[..., Mapping[str, Any]]]:
        """Name -> function, as workflows reference them in `uses`."""
        return {
            "rules.retention.find_expired": self.find_expired,
            "rules.retention.purge": self.purge,
        }

    def find_expired(self, as_of: str) -> dict[str, Any]:
        """Documents whose retention ended before `as_of` (ISO date or time). Records keep-until dates."""
        today = date.fromisoformat(as_of[:10])
        rules = self.categories()
        expired: list[str] = []
        kept = undecided = 0
        with self.sessions.begin() as s:
            rows = s.execute(
                select(m.Document, m.Employee.state, m.Employee.last_working_day)
                .outerjoin(m.Employee, m.Employee.person_id == m.Document.person_id)
                .where(m.Document.storage_ref != PURGED)
            ).all()
            for doc, state, last_day in rows:
                rule = rules.get(doc.category.value)
                until = (
                    keep_until(
                        rule, state.value if state else None, last_day, doc.created_at.date()
                    )
                    if isinstance(rule, Mapping)
                    else None
                )
                if until is None:
                    undecided += 1
                    continue
                doc.retention_until = until
                if until < today:
                    expired.append(str(doc.id))
                else:
                    kept += 1
        return {"documents": expired, "count": len(expired), "kept": kept, "kept_open": undecided}

    def purge(self, document_ids: list[str]) -> dict[str, Any]:
        """Delete each document's file and extracted fields. Idempotent.

        The result names every purged document; the engine stores it on the step and audits the
        step, so each purge is on the record (rules never write the audit log themselves).
        """
        purged: list[dict[str, Any]] = []
        with self.sessions.begin() as s:
            for raw in document_ids:
                doc = s.get(m.Document, uuid.UUID(raw))
                if doc is None or doc.storage_ref == PURGED:
                    continue
                (self.storage_dir / Path(doc.storage_ref).name).unlink(missing_ok=True)
                doc.storage_ref, doc.extracted, doc.validation = PURGED, None, None
                purged.append(
                    {
                        "document_id": raw,
                        "person_id": str(doc.person_id),
                        "category": doc.category.value,
                        "retention_until": doc.retention_until.isoformat()
                        if doc.retention_until
                        else None,
                    }
                )
        return {"count": len(purged), "documents": purged}
