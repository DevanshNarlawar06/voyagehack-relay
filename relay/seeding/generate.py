"""Deterministic synthetic HR dataset shaped like the TBO brief (PRD 22, issue #2).

Everything is fictional: names are random combinations, PANs follow the format (4th letter `P`,
5th letter = surname initial) and Aadhaar numbers pass Verhoeff, but none belong to anyone.
`relay seed generate` writes `seed/fixtures/*.json`; the same seed always gives the same files.

Scenario overlays (PRD 22): 25 clean hires across grades, 3 PAN-name mismatches, 2 joining-date
conflicts, 1 blurry Aadhaar, 2 high-spec laptops, 1 asset-outage window, 2 joiners who ignore
the document request, 1 offer without approval (refused trigger), 5 exits for V2.
"""

from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from relay.config import REPO_ROOT
from relay.models.recognizers.checksums import verhoeff_check_digit

FIXTURE_DIR = REPO_ROOT / "seed" / "fixtures"
SEED = 2026
TODAY = date(2026, 11, 9)

FIRST = [
    "Aarav", "Diya", "Ishaan", "Meera", "Kabir", "Ananya", "Rohan", "Saanvi", "Vivaan", "Aditi",
    "Arjun", "Kavya", "Reyansh", "Nisha", "Dhruv", "Tara", "Kiran", "Pooja", "Siddharth", "Riya",
    "Aniket", "Leela", "Farhan", "Zoya", "Tenzin", "Neha", "Joseph", "Sneha", "Harpreet", "Lakshmi",
]  # fmt: skip
LAST = [
    "Sharma", "Iyer", "Khan", "Nair", "Reddy", "Patel", "Ghosh", "Menon", "Singh", "Rao",
    "Kulkarni", "Das", "Pillai", "Bose", "Joshi", "Mehta", "Fernandes", "Chopra", "Banerjee", "Gill",
]  # fmt: skip
DEPARTMENTS = {
    "Engineering": ["Software Engineer", "Senior Software Engineer", "Engineering Manager"],
    "Product": ["Product Analyst", "Product Manager", "Group Product Manager"],
    "Operations": ["Operations Executive", "Operations Lead", "Operations Manager"],
    "Finance": ["Accounts Executive", "Finance Analyst", "Finance Manager"],
    "Sales": ["Sales Executive", "Key Account Manager", "Regional Sales Head"],
    "People": ["HR Executive", "HR Business Partner", "Head of HR"],
}
LOCATIONS = ["Gurugram", "Bengaluru", "Mumbai"]
GRADES = ["G1", "G2", "G3", "G4", "G5"]
CTC_BY_GRADE = {"G1": 600000, "G2": 1000000, "G3": 1800000, "G4": 3000000, "G5": 5000000}
BANKS = ["HDFC", "ICIC", "SBIN", "UTIB", "KKBK"]

# Demo users (PRD 4). Emails use the reserved .test TLD.
DEMO_USERS: list[dict[str, Any]] = [
    {"email": "hr.ops@relay.test", "display_name": "Asha (HR Ops)", "roles": ["hr_ops"]},
    {"email": "hr.admin@relay.test", "display_name": "Vikram (HR Admin)", "roles": ["hr_admin"]},
    {
        "email": "offer.approver@relay.test",
        "display_name": "Neel (Offer Approver)",
        "roles": ["approver_offer"],
    },
    {
        "email": "it.spend@relay.test",
        "display_name": "Ira (IT Spend Approver)",
        "roles": ["approver_it_spend"],
    },
    {
        "email": "finance.approver@relay.test",
        "display_name": "Mohan (Finance Approver)",
        "roles": ["approver_finance"],
    },
    {"email": "it.ops@relay.test", "display_name": "IT Ops", "roles": ["it_ops"]},
    {"email": "admin.ops@relay.test", "display_name": "Admin Ops", "roles": ["admin_ops"]},
    {"email": "finance.ops@relay.test", "display_name": "Finance Ops", "roles": ["finance_ops"]},
    {"email": "auditor@relay.test", "display_name": "Auditor", "roles": ["auditor"]},
    {"email": "developer@relay.test", "display_name": "Developer", "roles": ["developer"]},
]


def _pan(rng: random.Random, surname: str) -> str:
    return (
        "".join(rng.choices(string.ascii_uppercase, k=3))
        + "P"
        + surname[0].upper()
        + f"{rng.randint(0, 9999):04d}"
        + rng.choice(string.ascii_uppercase)
    )


def _aadhaar(rng: random.Random) -> str:
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    return body + verhoeff_check_digit(body)


def _dob(rng: random.Random, min_age: int, max_age: int) -> date:
    return TODAY - timedelta(days=rng.randint(min_age * 365, max_age * 365))


def _grade_for_title(titles: list[str], title: str) -> str:
    return ["G2", "G3", "G4"][titles.index(title)]


@dataclass
class Dataset:
    users: list[dict[str, Any]] = field(default_factory=list)
    employees: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    exits: list[dict[str, Any]] = field(default_factory=list)
    leave_balances: list[dict[str, Any]] = field(default_factory=list)

    def files(self) -> dict[str, Any]:
        """File name -> JSON content."""
        return {
            "users.json": self.users,
            "employees.json": self.employees,
            "candidates.json": self.candidates,
            "exits.json": self.exits,
            "leave_balances.json": self.leave_balances,
        }


def generate(seed: int = SEED) -> Dataset:
    """Build the dataset. Pure and deterministic for a given seed."""
    rng = random.Random(seed)
    ds = Dataset(users=[dict(u) for u in DEMO_USERS])
    used_names: set[str] = set()

    def person(min_age: int, max_age: int) -> dict[str, Any]:
        while True:
            first, last = rng.choice(FIRST), rng.choice(LAST)
            name = f"{first} {last}"
            if name not in used_names:
                used_names.add(name)
                break
        handle = f"{first}.{last}".lower()
        return {
            "full_name": name,
            "email": f"{handle}@relay.test",
            "phone": f"9{rng.randint(100000000, 999999999)}",
            "dob": _dob(rng, min_age, max_age).isoformat(),
            "pan": _pan(rng, last),
            "aadhaar": _aadhaar(rng),
            "bank_account": "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(11, 14))),
            "ifsc": f"{rng.choice(BANKS)}0{rng.randint(100000, 999999)}",
        }

    # ---- org: one head per department, three to six reports each, all existing employees
    code = 1000
    for dept, titles in DEPARTMENTS.items():
        head = {
            "employee_code": f"TBO{code}", **person(38, 52), "department": dept,
            "role_title": titles[-1], "grade": "G5", "location": rng.choice(LOCATIONS),
            "manager_code": None, "joining_date": (TODAY - timedelta(days=rng.randint(1500, 3500))).isoformat(),
        }  # fmt: skip
        ds.employees.append(head)
        code += 1
        for _ in range(rng.randint(4, 7)):
            title = rng.choice(titles[:-1])
            ds.employees.append({
                "employee_code": f"TBO{code}", **person(23, 45), "department": dept,
                "role_title": title, "grade": _grade_for_title(titles, title),
                "location": head["location"], "manager_code": head["employee_code"],
                "joining_date": (TODAY - timedelta(days=rng.randint(60, 2500))).isoformat(),
            })  # fmt: skip
            code += 1
    for emp in ds.employees:
        emp["slack_user_id"] = f"U{emp['employee_code']}"
        ds.leave_balances.append({
            "employee_code": emp["employee_code"],
            "earned": rng.randint(0, 24), "casual": rng.randint(0, 8), "sick": rng.randint(0, 8),
        })  # fmt: skip

    # Managers also get demo-user logins with the manager role.
    for emp in ds.employees:
        if emp["manager_code"] is None:
            ds.users.append({
                "email": emp["email"], "display_name": emp["full_name"], "roles": ["manager", "employee"],
                "employee_code": emp["employee_code"], "slack_user_id": emp["slack_user_id"],
            })  # fmt: skip

    # ---- candidates with offers: (scenario, count)
    plan = [
        ("clean", 25), ("pan_name_mismatch", 3), ("joining_date_conflict", 2),
        ("blurry_aadhaar", 1), ("high_spec_laptop", 2), ("asset_outage", 1),
        ("ignores_document_request", 2), ("unapproved_offer", 1),
    ]  # fmt: skip
    heads = [e for e in ds.employees if e["manager_code"] is None]
    n = 1
    for scenario, count in plan:
        for _ in range(count):
            p = person(21, 40)
            head = rng.choice(heads)
            titles = DEPARTMENTS[head["department"]]
            grade = "G5" if scenario == "high_spec_laptop" else rng.choice(GRADES[:4])
            title = titles[-1] if grade == "G5" else rng.choice(titles[:-1])
            joining = TODAY + timedelta(days=rng.randint(7, 30))
            doc = {
                "full_name": p["full_name"], "dob": p["dob"], "pan": p["pan"],
                "aadhaar": p["aadhaar"], "bank_account": p["bank_account"], "ifsc": p["ifsc"],
                "joining_date": joining.isoformat(), "ocr_confidence": round(rng.uniform(0.9, 0.99), 3),
            }  # fmt: skip
            if scenario == "pan_name_mismatch":
                other = rng.choice([x for x in LAST if x[0] != p["full_name"].split()[1][0]])
                doc["pan_name"] = f"{p['full_name'].split()[0]} {other}"
            if scenario == "joining_date_conflict":
                doc["joining_date"] = (joining + timedelta(days=rng.choice([-7, 14]))).isoformat()
            if scenario == "blurry_aadhaar":
                doc["ocr_confidence"] = 0.41
                doc["aadhaar"] = doc["aadhaar"][:6] + "??" + doc["aadhaar"][8:]
            ds.candidates.append({
                "candidate_ref": f"CAND-{n:03d}", "scenario": scenario, **p,
                "offer": {
                    "grade": grade, "role_title": title, "department": head["department"],
                    "location": head["location"], "ctc": CTC_BY_GRADE[grade],
                    "joining_date": joining.isoformat(), "manager_code": head["employee_code"],
                    "approved": scenario != "unapproved_offer",
                },
                "documents": None if scenario == "ignores_document_request" else doc,
                "upload_after_hours": None if scenario == "ignores_document_request" else rng.choice([2, 6, 20]),
                "fault": "assets_outage_window" if scenario == "asset_outage" else None,
            })  # fmt: skip
            n += 1

    # ---- exits (V2): five existing employees resign
    leavers = rng.sample([e for e in ds.employees if e["manager_code"] is not None], 5)
    for e in leavers:
        ds.exits.append({
            "employee_code": e["employee_code"],
            "resignation_date": (TODAY - timedelta(days=rng.randint(0, 5))).isoformat(),
            "requested_last_day": (TODAY + timedelta(days=rng.choice([30, 45, 60]))).isoformat(),
            "reason": rng.choice(["higher studies", "relocation", "career change", "personal"]),
            "buyout_requested": rng.random() < 0.4,
        })  # fmt: skip
    return ds


def write(ds: Dataset, directory: Path = FIXTURE_DIR) -> list[Path]:
    """Write the dataset as pretty JSON. Returns the files written."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in ds.files().items():
        path = directory / name
        path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def read(directory: Path = FIXTURE_DIR) -> Dataset:
    """Read fixtures back (the loader's input)."""

    def load(name: str) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = json.loads((directory / name).read_text(encoding="utf-8"))
        return data

    return Dataset(
        users=load("users.json"), employees=load("employees.json"),
        candidates=load("candidates.json"), exits=load("exits.json"),
        leave_balances=load("leave_balances.json"),
    )  # fmt: skip
