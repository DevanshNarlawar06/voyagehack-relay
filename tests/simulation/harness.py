"""Simulation harness: fake clock, in-memory connector with fault injection, toy workflow."""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine as SAEngine
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from relay.core.engine.engine import Engine
from relay.core.engine.ports import EngineDeps
from relay.core.errors import ConflictError, PermanentError, TransientError
from relay.core.policy import PolicyService
from relay.core.registry.store import DefinitionStore
from relay.db import models as m


class FakeClock:
    """Deterministic clock that tests advance by hand."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 11, 9, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class Spec:
    side_effect: bool


@dataclass
class Result:
    external_id: str | None
    response: dict[str, Any]
    reconciled: bool = False
    duplicates: int = 0
    cost_inr: Decimal = Decimal(0)


@dataclass
class FakeConnector:
    """Idempotent in-memory system. `faults` is a list consumed per call: 'transient',
    'permanent', 'conflict', or 'crash_after' (the effect lands, then the call raises)."""

    name: str
    op_names: tuple[str, ...] = ("allocate", "create", "lookup")
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: Counter[str] = field(default_factory=Counter)
    faults: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def ops(self) -> dict[str, Spec]:
        return {op: Spec(side_effect=op != "lookup") for op in self.op_names}

    async def call(self, op: str, payload: dict[str, Any], *, idempotency_key: str) -> Result:
        with self.lock:
            self.calls[op] += 1
            fault = self.faults.pop(0) if self.faults else None
            if fault == "transient":
                raise TransientError("503 from fake")
            if fault == "permanent":
                raise PermanentError("400 from fake")
            if fault == "conflict":
                raise ConflictError("name mismatch", fields=["full_name"])
            rec = self.records.setdefault(
                idempotency_key, {"id": f"{self.name}-{len(self.records) + 1}", **payload}
            )
            if fault == "crash_after":
                raise RuntimeError("worker died after the external call")
            return Result(external_id=rec["id"], response=dict(rec))

    async def reconcile(
        self, op: str, idempotency_key: str, payload: dict[str, Any]
    ) -> Result | None:
        rec = self.records.get(idempotency_key)
        return Result(rec["id"], dict(rec), reconciled=True) if rec else None


def rule_prehire(candidate: str) -> Mapping[str, Any]:
    return {"employee_code": f"E-{candidate}"}


def rule_terms(grade: str) -> Mapping[str, Any]:
    return {"probation_months": 6 if grade != "G4" else 3}


def rule_laptop(grade: str) -> Mapping[str, Any]:
    return {
        "sku": "PRO" if grade == "G5" else "STD",
        "price_inr": 210000 if grade == "G5" else 90000,
    }


TOY = """
name: toy_onboarding
version: 1
subject: employee
trigger: { event: toy.hired }
context:
  candidate: "trigger.candidate"
  grade: "trigger.grade"
  consented_purposes: "['onboarding_verification', 'payroll_setup']"
steps:
  - id: create_prehire
    kind: rule
    uses: rules.prehire
    action: employee.create
    inputs: { candidate: "ctx.candidate" }
    outputs: { code: "result.employee_code" }
  - id: compute_terms
    kind: rule
    uses: rules.terms
    action: hr.terms.compute
    needs: [create_prehire]
    inputs: { grade: "ctx.grade" }
    outputs: { probation_months: "result.probation_months" }
  - id: await_documents
    kind: wait
    action: documents.request
    needs: [compute_terms]
    for_subject_action: documents.uploaded
    sla: { remind_at: [24h, 48h], escalate_after: 72h, escalate_to: hr_ops }
  - parallel:
      id: provision
      needs: [await_documents]
      branches:
        - - { id: provision_identity, kind: connector, uses: directory.create, action: directory.account.create, inputs: { code: "ctx.code" } }
        - - { id: select_laptop, kind: rule, uses: rules.laptop, action: assets.laptop.select, inputs: { grade: "ctx.grade" }, outputs: { laptop: "result" } }
          - id: order_laptop
            kind: connector
            uses: assets.allocate
            action: assets.laptop.allocate
            inputs: { code: "ctx.code", sku: "ctx.laptop.sku" }
            attributes: { amount_inr: "ctx.laptop.price_inr" }
            retry: { max_attempts: 3, base_seconds: 1 }
  - id: probation_confirmation
    kind: human
    action: employment.probation.confirm
    needs: [provision]
    when: "ctx.probation_months > 3"
    task: { assignee_role: manager, options: [confirm, extend, refer_to_hr] }
"""


@dataclass
class World:
    engine: Engine
    clock: FakeClock
    assets: FakeConnector
    directory: FakeConnector
    sessions: sessionmaker[Session]

    def user(self, *roles: str) -> uuid.UUID:
        with self.sessions.begin() as s:
            u = m.User(
                email=f"{uuid.uuid4().hex[:8]}@relay.test", display_name="Test", roles=list(roles)
            )
            s.add(u)
            s.flush()
            return u.id

    def worker(self, worker_id: str) -> Engine:
        return Engine(
            self.sessions,
            self.engine.deps,
            definitions=self.engine.definitions,
            worker_id=worker_id,
        )


RUNTIME_TABLES = (
    "timers", "human_tasks", "cost_events", "step_runs", "idempotency_ledger", "messages",
    "conversations", "workflow_runs", "workflow_definitions", "kill_switches", "policy_versions",
    "users",
)  # fmt: skip


def clean(engine: SAEngine) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE offers SET approved_decision_id = NULL"))
        for t in RUNTIME_TABLES:
            conn.execute(text(f"DELETE FROM {t}"))


def build_world(engine: SAEngine, workflow_text: str = TOY) -> World:
    clean(engine)
    clock = FakeClock()
    assets, directory = FakeConnector("assets"), FakeConnector("directory")
    deps = EngineDeps(
        rules={
            "rules.prehire": rule_prehire,
            "rules.terms": rule_terms,
            "rules.laptop": rule_laptop,
        },
        connectors={"assets": assets, "directory": directory},  # type: ignore[dict-item]
        clock=clock,
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    policy = PolicyService()
    store = DefinitionStore()
    with sessions.begin() as s:
        store.register(s, workflow_text, policies=policy.policies(s), deps=deps, actor="test")
    eng = Engine(sessions, deps, definitions=store, worker_id="w1")
    return World(eng, clock, assets, directory, sessions)
