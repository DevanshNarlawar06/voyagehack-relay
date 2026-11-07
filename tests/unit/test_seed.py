"""Synthetic dataset: determinism, scenario coverage, identifier validity, idempotent loading."""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relay.db import models as m
from relay.models.recognizers.checksums import verhoeff_is_valid
from relay.seeding.generate import FIXTURE_DIR, generate, read
from relay.seeding.load import load

PAN = re.compile(r"^[A-Z]{3}P[A-Z][0-9]{4}[A-Z]$")
IFSC = re.compile(r"^[A-Z]{4}0[0-9]{6}$")


def test_generation_is_deterministic_and_matches_committed_fixtures() -> None:
    ds = generate()
    assert ds.files() == generate().files()
    for name, content in ds.files().items():
        on_disk = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
        assert on_disk == content, f"{name} is stale: run `relay seed generate`"


def test_every_prd_scenario_is_present() -> None:
    counts = Counter(c["scenario"] for c in generate().candidates)
    assert counts == {
        "clean": 25, "pan_name_mismatch": 3, "joining_date_conflict": 2, "blurry_aadhaar": 1,
        "high_spec_laptop": 2, "asset_outage": 1, "ignores_document_request": 2,
        "unapproved_offer": 1,
    }  # fmt: skip
    assert len(generate().exits) == 5


def test_identifiers_are_well_formed_and_names_unique() -> None:
    ds = generate()
    people = ds.employees + ds.candidates
    assert len({p["full_name"] for p in people}) == len(people)
    for p in people:
        assert PAN.match(p["pan"]), p["pan"]
        assert p["pan"][4] == p["full_name"].split()[1][0]
        assert verhoeff_is_valid(p["aadhaar"])
        assert IFSC.match(p["ifsc"])
        assert p["email"].endswith("@relay.test")


def test_scenario_overlays_are_applied() -> None:
    for c in generate().candidates:
        doc = c["documents"]
        if c["scenario"] == "pan_name_mismatch":
            assert doc["pan_name"] != c["full_name"]
        elif c["scenario"] == "joining_date_conflict":
            assert doc["joining_date"] != c["offer"]["joining_date"]
        elif c["scenario"] == "blurry_aadhaar":
            assert doc["ocr_confidence"] < 0.5 and "?" in doc["aadhaar"]
        elif c["scenario"] == "high_spec_laptop":
            assert c["offer"]["grade"] == "G5"
        elif c["scenario"] == "ignores_document_request":
            assert doc is None
        elif c["scenario"] == "unapproved_offer":
            assert c["offer"]["approved"] is False


@pytest.mark.db
def test_load_is_idempotent_and_records_offer_approvals(db_session: Session) -> None:
    ds = read()
    first = load(db_session, ds)
    second = load(db_session, ds)
    assert first == second
    assert first.approved_offers == 36
    # The test database is shared, so count only the seed's own rows.
    refs = [c["candidate_ref"] for c in ds.candidates]
    codes = [e["employee_code"] for e in ds.employees]
    count = lambda q: db_session.scalar(select(func.count()).select_from(q.subquery()))  # noqa: E731
    assert count(select(m.Candidate.id).where(m.Candidate.external_ref.in_(refs))) == 37
    assert count(select(m.Employee.id).where(m.Employee.employee_code.in_(codes))) == 46
    approval_ids = db_session.scalars(
        select(m.Offer.approved_decision_id)
        .join(m.Candidate, m.Candidate.offer_id == m.Offer.id)
        .where(m.Candidate.external_ref.in_(refs), m.Offer.approved_decision_id.is_not(None))
    ).all()
    approvals = [db_session.get(m.HumanTask, i) for i in approval_ids]
    assert len(approvals) == 36
    approver = db_session.scalar(select(m.User).where(m.User.email == "offer.approver@relay.test"))
    assert approver is not None and all(
        t is not None and t.decided_by == approver.id and t.action == "hiring.offer.approve"
        for t in approvals
    )
    managed = count(
        select(m.Employee.id).where(
            m.Employee.employee_code.in_(codes), m.Employee.manager_id.is_not(None)
        )
    )
    assert managed == 40
    # Minimisation: only the last 4 Aadhaar digits are stored
    person = db_session.scalars(select(m.Person)).first()
    assert person is not None and len(person.aadhaar_last4 or "") == 4
