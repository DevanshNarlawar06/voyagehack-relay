"""Exactly-once chaos harness (PRD 11.5, 21; issue #5).

Real processes, real HTTP, real Postgres. The mock service runs in its own process; two engine
workers run in theirs. Workers die hard in two ways:

* self-kill: right after the mock has returned 200 (the side effect landed) and before the ledger
  and step commit, a worker calls `os._exit` with probability `--kill-rate`. That is the window the
  idempotency ledger exists for.
* parent kill: every so often the driver kills a random worker (SIGKILL on POSIX, TerminateProcess
  on Windows), wherever it happens to be, and starts a replacement.

At the end every run must be COMPLETED and each mock system must hold exactly one record per run.

Live demo:  uv run python -m tests.chaos.chaos run --runs 100 --workers 2
(uses DATABASE_URL; it registers its own `chaos_check` workflow and touches nothing else.)
"""

from __future__ import annotations

import argparse
import os
import random
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKFLOW = """
name: chaos_check
version: 1
subject: employee
trigger: { event: test.chaos_check }
context:
  code: "trigger.code"
  consented_purposes: "['payroll_setup']"
steps:
  - id: order_laptop
    kind: connector
    uses: assets.allocate_laptop
    action: assets.laptop.allocate
    inputs: { employee_code: "ctx.code", sku: "'LAT-14'" }
    attributes: { amount_inr: "90000" }
    retry: { max_attempts: 50, base_seconds: 1 }
  - id: issue_card
    kind: connector
    uses: access.issue_card
    action: access.card.issue
    needs: [order_laptop]
    inputs: { employee_code: "ctx.code", photo_ref: "'photo-1'" }
    retry: { max_attempts: 50, base_seconds: 1 }
  - id: setup_payroll
    kind: connector
    uses: payroll.setup_employee
    action: payroll.employee.setup
    needs: [issue_card]
    inputs: { employee_code: "ctx.code", ctc_inr: "1200000" }
    attributes: { ctc_matches_offer: "true" }
    retry: { max_attempts: 50, base_seconds: 1 }
"""
SYSTEMS = ("assets", "access", "payroll")
ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------- worker process


class _DieAfterCall:
    """Wraps a connector: after a successful external call, maybe die before anything commits."""

    def __init__(self, inner: Any, kill_rate: float, rng: random.Random) -> None:
        self._inner, self._kill_rate, self._rng = inner, kill_rate, rng
        self.name = inner.name

    def ops(self) -> Any:
        return self._inner.ops()

    async def call(self, op: str, payload: dict[str, Any], *, idempotency_key: str) -> Any:
        result = await self._inner.call(op, payload, idempotency_key=idempotency_key)
        if self._rng.random() < self._kill_rate:
            os._exit(137)  # the side effect landed; the ledger never hears about it
        return result

    async def reconcile(self, op: str, idempotency_key: str, payload: dict[str, Any]) -> Any:
        return await self._inner.reconcile(op, idempotency_key, payload)


def worker_main(db_url: str, worker_id: str, kill_rate: float, lease_seconds: int) -> None:
    """Run engine ticks forever (until killed)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from relay.connectors.http import load_connectors
    from relay.core.engine.engine import Engine
    from relay.core.engine.ports import EngineDeps
    from relay.core.registry.store import DefinitionStore

    rng = random.Random()
    connectors = {
        name: _DieAfterCall(conn, kill_rate, rng) for name, conn in load_connectors().items()
    }
    sessions = sessionmaker(bind=create_engine(db_url), expire_on_commit=False)
    engine = Engine(
        sessions,
        EngineDeps(connectors=connectors),  # type: ignore[arg-type]
        definitions=DefinitionStore(),
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    while True:
        if not engine.tick(max_steps=5).busy:
            time.sleep(0.05)


# ---------------------------------------------------------------- driver


@dataclass
class ChaosReport:
    runs: int
    completed: int
    records: dict[str, int]
    self_kills: int
    parent_kills: int
    seconds: float
    statuses: dict[str, int] = field(default_factory=dict)

    @property
    def exactly_once(self) -> bool:
        """Every run completed, and each system holds exactly one record per run."""
        return self.completed == self.runs and all(n == self.runs for n in self.records.values())


def run_chaos(
    db_url: str,
    *,
    runs: int = 100,
    workers: int = 2,
    kill_rate: float = 0.1,
    parent_kill_every: float = 1.5,
    lease_seconds: int = 2,
    timeout: float = 600,
    seed: int | None = None,
) -> ChaosReport:
    """Start mocks and workers, run `runs` workflows under kills, and report what landed."""
    import httpx
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import sessionmaker

    from relay.connectors.http import load_connectors
    from relay.core.engine.engine import Engine
    from relay.core.engine.ports import EngineDeps
    from relay.core.policy import PolicyService
    from relay.core.registry.store import DefinitionStore
    from relay.db import models as m

    rng = random.Random(seed)
    port = _free_port()
    mocks_url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "RELAY_MOCKS_URL": mocks_url, "PYTHONPATH": str(ROOT)}
    previous_mocks_url = os.environ.get("RELAY_MOCKS_URL")
    os.environ["RELAY_MOCKS_URL"] = mocks_url
    mock = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mocks.app:create_app", "--factory",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env,
    )  # fmt: skip
    procs: dict[str, subprocess.Popen[bytes]] = {}
    self_kills = parent_kills = 0
    started = time.monotonic()
    sa = create_engine(db_url)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{mocks_url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("mock service did not start")

        sessions = sessionmaker(bind=sa, expire_on_commit=False)
        deps = EngineDeps(connectors=load_connectors())  # type: ignore[arg-type]
        store = DefinitionStore()
        with sessions.begin() as s:
            store.register(
                s, WORKFLOW, policies=PolicyService().policies(s), deps=deps, actor="chaos"
            )
        starter = Engine(sessions, deps, definitions=store, worker_id="chaos-driver")
        run_ids = [
            starter.start_run(
                "chaos_check",
                {"code": f"CHAOS-{uuid.uuid4().hex[:10]}"},
                actor="chaos",
                subject_id=str(uuid.uuid4()),
            )
            for _ in range(runs)
        ]

        def spawn(n: int) -> None:
            wid = f"chaos-w{n}-{uuid.uuid4().hex[:4]}"
            procs[wid] = subprocess.Popen(
                [sys.executable, "-m", "tests.chaos.chaos", "worker", "--db", db_url,
                 "--worker-id", wid, "--kill-rate", str(kill_rate), "--lease", str(lease_seconds)],
                cwd=ROOT, env=env,
            )  # fmt: skip

        for n in range(workers):
            spawn(n)
        next_kill = time.monotonic() + parent_kill_every
        done = 0
        while time.monotonic() - started < timeout:
            for wid, proc in list(procs.items()):
                if proc.poll() is not None:  # died by its own hand
                    self_kills += 1
                    del procs[wid]
                    spawn(len(procs))
            if parent_kill_every and time.monotonic() >= next_kill:
                wid = rng.choice(list(procs))
                procs.pop(wid).kill()
                parent_kills += 1
                spawn(len(procs))
                next_kill = time.monotonic() + rng.uniform(0.5, 2) * parent_kill_every
            with sessions() as s:
                done = (
                    s.scalar(
                        select(func.count())
                        .select_from(m.WorkflowRun)
                        .where(
                            m.WorkflowRun.id.in_(run_ids),
                            m.WorkflowRun.status == m.RunStatus.COMPLETED,
                        )
                    )
                    or 0
                )
            if done == runs:
                break
            time.sleep(0.2)

        with sessions() as s:
            statuses = {
                str(st): int(n)
                for st, n in s.execute(
                    select(m.WorkflowRun.status, func.count())
                    .where(m.WorkflowRun.id.in_(run_ids))
                    .group_by(m.WorkflowRun.status)
                )
            }
        stats = httpx.get(f"{mocks_url}/_control/stats", timeout=5).json()
        return ChaosReport(
            runs=runs,
            completed=done,
            records={sys_: int(stats[sys_]["records"]) for sys_ in SYSTEMS},
            self_kills=self_kills,
            parent_kills=parent_kills,
            seconds=round(time.monotonic() - started, 1),
            statuses=statuses,
        )
    finally:
        for proc in procs.values():
            proc.kill()
        for proc in procs.values():
            proc.wait()
        mock.kill()
        mock.wait()
        sa.dispose()
        if previous_mocks_url is None:
            os.environ.pop("RELAY_MOCKS_URL", None)
        else:
            os.environ["RELAY_MOCKS_URL"] = previous_mocks_url


def main(argv: list[str] | None = None) -> int:
    """CLI: `run` drives a chaos session; `worker` is what the driver spawns."""
    parser = argparse.ArgumentParser(prog="python -m tests.chaos.chaos")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker")
    w.add_argument("--db", required=True)
    w.add_argument("--worker-id", required=True)
    w.add_argument("--kill-rate", type=float, default=0.1)
    w.add_argument("--lease", type=int, default=2)
    r = sub.add_parser("run")
    r.add_argument("--db", default=None, help="defaults to DATABASE_URL / Settings")
    r.add_argument("--runs", type=int, default=100)
    r.add_argument("--workers", type=int, default=2)
    r.add_argument("--kill-rate", type=float, default=0.1)
    args = parser.parse_args(argv)
    if args.cmd == "worker":
        worker_main(args.db, args.worker_id, args.kill_rate, args.lease)
        return 0
    from relay.config import Settings

    db = args.db or Settings().database_url
    report = run_chaos(db, runs=args.runs, workers=args.workers, kill_rate=args.kill_rate)
    sys.stdout.write(
        f"runs={report.runs} completed={report.completed} records={report.records} "
        f"self_kills={report.self_kills} parent_kills={report.parent_kills} "
        f"seconds={report.seconds}\n"
        f"EXACTLY ONCE: {'YES' if report.exactly_once else 'NO'}\n"
    )
    return 0 if report.exactly_once else 1


if __name__ == "__main__":
    raise SystemExit(main())
