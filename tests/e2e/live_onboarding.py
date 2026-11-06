"""One real onboarding over HTTP, as HR and a joiner would do it. No test shortcuts.

Needs the API, a worker, Postgres and the mocks running, with the seed loaded:
    uv run python -m tests.e2e.live_onboarding [--scenario clean]
Exits 0 only if the run completes, each system holds one record for it, and the chain verifies.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from relay.db import models as m
from relay.db.session import session_scope
from relay.seeding.scenarios import CONSENTED, sidecars

API = "http://localhost:8000/api/v1"
MOCKS = "http://localhost:9000"
PDF = b"%PDF-1.7 synthetic document"
FIXTURES = Path("seed/fixtures/candidates.json")
SYSTEMS = {"directory": "accounts", "assets": "laptops", "access": "cards", "payroll": "employees"}


def say(message: str) -> None:
    sys.stdout.write(f"{message}\n")
    sys.stdout.flush()


def login(email: str) -> httpx.Client:
    token = httpx.post(f"{API}/auth/demo-login", json={"email": email}, timeout=10).raise_for_status().json()["token"]
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=30)


def steps_of(client: httpx.Client, run_id: str) -> dict[str, str]:
    detail = client.get(f"/runs/{run_id}").raise_for_status().json()
    return {step["step_id"]: step["status"] for step in detail["steps"]}


def wait_for(client: httpx.Client, run_id: str, step: str, wanted: str, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        statuses = steps_of(client, run_id)
        if statuses.get(step) == wanted:
            return
        stuck = [k for k, v in statuses.items() if v in ("FAILED", "PARKED", "WAITING_HUMAN")]
        if stuck:
            detail = client.get(f"/runs/{run_id}").json()
            errors = {s["step_id"]: s["error"] for s in detail["steps"] if s["error"]}
            raise RuntimeError(f"stuck at {stuck} while waiting for {step}: {errors}")
        time.sleep(0.25)
    raise RuntimeError(f"{step} did not reach {wanted}: {steps_of(client, run_id)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="clean")
    args = parser.parse_args()
    candidate: dict[str, Any] = next(
        c for c in json.loads(FIXTURES.read_text(encoding="utf-8")) if c["scenario"] == args.scenario
    )
    reference = candidate["candidate_ref"]
    with session_scope() as session:
        row = session.scalars(select(m.Candidate).where(m.Candidate.external_ref == reference)).one()
        person_id = str(row.person_id)
        offer = session.get(m.Offer, row.offer_id)
        assert offer is not None
        decision_task_id = str(offer.approved_decision_id)

    hr = login("hr.ops@relay.test")
    say(f"1. HR starts onboarding for {reference} ({args.scenario})")
    started = hr.post(
        "/events",
        json={
            "event": "candidate.offer_accepted",
            "subject_id": reference,
            "payload": {
                "candidate_ref": reference,
                "manager_code": candidate["offer"]["manager_code"],
                "decision_task_id": decision_task_id,
                "subject_label": f"{candidate['full_name']} · {candidate['offer']['role_title']}",
            },
        },
    )
    started.raise_for_status()
    run_id = started.json()["run_ids"][0]
    say(f"   run {run_id}")
    wait_for(hr, run_id, "await_documents", "WAITING_TIMER")
    say("2. The run is waiting for the joiner's documents")

    link = hr.post("/documents/links", json={"person_id": person_id, "run_id": run_id})
    link.raise_for_status()
    say("3. HR issues a signed upload link; the joiner uploads with no account")
    categories = ["pan", "aadhaar", "bank_proof"]
    uploaded = httpx.post(
        f"{API}/documents",
        data={
            "token": link.json()["token"],
            "categories": categories,
            "consents": CONSENTED,
            "sidecars": json.dumps(sidecars(candidate)),
        },
        files=[("files", (f"{c}.pdf", PDF, "application/pdf")) for c in categories],
        timeout=30,
    )
    say(f"   upload answered {uploaded.status_code}: {uploaded.text[:200]}")
    uploaded.raise_for_status()

    wait_for(hr, run_id, "policy_acknowledgements", "WAITING_TIMER", seconds=60)
    say("4. Everything provisioned automatically; the run waits for policy acknowledgement")

    acknowledged = httpx.post(
        f"{API}/policies/acknowledgements",
        json={"token": link.json()["token"]},
        timeout=30,
    )
    say(f"5. The joiner acknowledges policies: {acknowledged.status_code} {acknowledged.text[:200]}")
    acknowledged.raise_for_status()

    wait_for(hr, run_id, "readiness_gate", "SUCCEEDED", seconds=30)
    detail = hr.get(f"/runs/{run_id}").raise_for_status().json()
    say(f"6. Run status: {detail['status']}, {detail['steps_done']}/{detail['steps_total']} steps, {detail['subject_label']}")

    with session_scope() as session:
        code = session.scalar(select(m.Employee.employee_code).where(m.Employee.person_id == row_person(person_id)))
    counts = {}
    for system, collection in SYSTEMS.items():
        counts[system] = httpx.get(f"{MOCKS}/{system}/{collection}", params={"employee_code": code}, timeout=10).json()["count"]
    say(f"7. Records for {code}: {counts}")

    auditor = login("auditor@relay.test")
    verified = auditor.get("/audit/verify").raise_for_status().json()
    say(f"8. Audit chain ok={verified['ok']} over {verified['checked']} events")
    metrics = hr.get("/metrics/summary").raise_for_status().json()
    say(f"9. Dashboard: {metrics['completed_runs']} completed, automation {metrics['step_automation_rate']}")
    good = detail["status"] == "completed" and set(counts.values()) == {1} and verified["ok"]
    say("END TO END: " + ("YES" if good else "NO"))
    return 0 if good else 1


def row_person(person_id: str) -> Any:
    import uuid

    return uuid.UUID(person_id)


if __name__ == "__main__":
    raise SystemExit(main())
