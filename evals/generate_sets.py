"""Writes the labelled eval sets (PRD 13.4). Deterministic and entirely synthetic.

Run: python evals/generate_sets.py
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path
from typing import Any

from relay.models.recognizers import verhoeff_check_digit

HERE = Path(__file__).parent
NAMES = ["Asha Rao", "Vikram Sen", "Meena Iyer", "Rahul Nair", "Divya Shah", "Karan Mehta", "Sana Khan"]

INTAKE: dict[str, list[str]] = {
    "onboarding_status": [
        "what's my onboarding status", "status of my onboarding please", "where is my onboarding at",
        "how far along am I in onboarding", "can you check my onboarding status", "where am I in the process",
        "onboarding status?", "any update on my onboarding status", "how far along is my joining",
        "what is the status of my onboarding today", "tell me my onboarding status",
        # Paraphrases the keyword table is expected to miss.
        "has everything been set up for my first day", "is my joining paperwork moving",
        "any news on my onboarding status", "how far along are we", "where is my onboarding right now", "please share my onboarding status",
        "what is pending before I start", "am I all set for monday", "what's left for me to do before joining",
    ],
    "documents_uploaded": [
        "I've uploaded my documents", "just uploaded my pan", "submitted my documents", "I uploaded everything",
        "sent the documents", "sent my docs yesterday", "submitted my docs", "documents uploaded", "i have uploaded the aadhaar",
        "uploaded the files you asked for", "I already uploaded the bank proof",
        "I uploaded my degree certificate", "I have uploaded them all", "uploaded the photo too", "submitted my documents this morning",
        "the papers are in now", "you should have my scans", "done with the paperwork you wanted", "attached all of it earlier",
    ],
    "policy_question": [
        "how many leaves do I get in probation", "what is the notice period for G3", "what is my notice period",
        "am I eligible for gratuity", "how does provident fund work", "how much pf is deducted", "leave policy please",
        "what is the probation period", "when do I get full and final", "how many casual leaves do I get", "sick leave rules",
        "can I take leave during notice", "is earned leave allowed in probation", "how long is probation here", "gratuity rules",
        "what is the PF rate", "does probation affect leaves", "how many days of notice do I owe", "explain gratuity to me", "is there a provident fund",
        "how much time off can I take", "when do I get my last pay after leaving", "what happens to my retirement savings", "how long until I am confirmed",
    ],
    "resign": [
        "I want to resign", "I would like to resign", "I am resigning", "I want to quit my job", "putting in my papers",
        "I'd like to put in my notice", "how do I serve my notice", "I am going to resign next week", "please process my resignation",
        "I have decided to resign", "I want to submit my resignation",
        "I am ready to resign", "I want to quit my job next month", "how do I resign", "I wish to resign from my role",
        "I've accepted another offer and will be leaving", "this will be my last month here", "I'm moving on from the company", "time for me to leave TBO",
    ],
    "data_request": [
        "show me my data", "can I see my personal data", "export my data", "please delete my personal data", "erase my data",
        "correct my data", "I want to see my data", "show all my data", "please correct my personal data", "delete my data",
        "what information do you hold about me", "send me a copy of everything you have on me", "remove my records", "my address on file is wrong",
    ],
    "posh_report": [
        "I need to report sexual harassment", "my lead keeps sexually harassing me", "I want to file a POSH complaint",
        "someone touched me inappropriately at work", "report a posh issue", "this is about posh", "I was sexually harassed at the offsite",
        "a colleague keeps making comments about my body", "my manager asked me out repeatedly and it makes me unsafe",
    ],
    "grievance_report": [
        "I have a grievance", "I want to file a complaint about my manager", "I need to raise a grievance", "I want to make a formal complaint",
        "file a complaint for me", "I'm being bullied by my team", "raise a grievance about pay", "my manager is treating me unfairly",
        "I am being singled out at work",
    ],
    "whistleblower_report": [
        "I want to whistleblow", "I want to report fraud", "reporting corruption in procurement", "whistleblower report please",
        "I need to report a bribe", "I'm reporting fraud by a vendor", "someone in finance is taking kickbacks", "I found fake invoices",
    ],
    "unknown": [
        "the coffee machine is broken", "what's for lunch", "hello", "thanks", "can you book me a cab",
        "good morning", "the lift is out of order", "please reset my badge", "which floor is finance on", "the printer jammed", "any snacks left",
        "can I change my desk", "is today a holiday in Pune", "how do I book a meeting room", "who approves my travel", "can you share the holiday calendar link", "how do I join the sports club",
        "where do I collect my id", "is there a shuttle from the metro", "what is the guest wifi", "my monitor flickers",
        "who is the CEO", "my laptop is slow", "ok", "tell me a joke", "what time is the all hands",
        "is the office open on saturday", "where is the parking",
    ],
}

HELPDESK: list[tuple[str, str | None]] = [
    ("how many leaves do I get in probation", "leave.probation"),
    ("can I use earned leave during probation", "leave.probation"),
    ("is leave allowed while on probation", "leave.probation"),
    ("can I take casual leave in my probation", "leave.probation"),
    ("what is my annual leave entitlement", "leave.entitlement"),
    ("how many earned leave days per year", "leave.entitlement"),
    ("how many sick leaves do I get", "leave.entitlement"),
    ("when does leave accrual start", "leave.entitlement"),
    ("how many casual leave days do we get annually", "leave.entitlement"),
    ("can I take leave during my notice period", "leave.notice"),
    ("is earned leave allowed during notice", "leave.notice"),
    ("what is the rule for leave while serving notice", "leave.notice"),
    ("what is the notice period for grade G3", "notice.period"),
    ("what is my notice period if I resign", "notice.period"),
    ("how long is the notice period", "notice.period"),
    ("what notice do I serve during probation", "notice.period"),
    ("how many days notice for G5", "notice.period"),
    ("what is the probation period for G4", "probation.length"),
    ("how long is probation", "probation.length"),
    ("how many months of probation for grade G1", "probation.length"),
    ("what is the probation length by grade", "probation.length"),
    ("when is full and final settlement paid", "fnf.timeline"),
    ("how soon do I get my full and final", "fnf.timeline"),
    ("how many working days for settlement of wages", "fnf.timeline"),
    ("when are my dues settled after my last working day", "fnf.timeline"),
    ("what is the PF contribution", "pf.contribution"),
    ("how much provident fund does the employer pay", "pf.contribution"),
    ("what is the employee PF rate", "pf.contribution"),
    ("what is the provident fund wage ceiling", "pf.contribution"),
    ("is PF mandatory", "pf.contribution"),
    ("how much is the EPF contribution", "pf.contribution"),
    ("when is gratuity payable", "gratuity.eligibility"),
    ("how many years of service for gratuity", "gratuity.eligibility"),
    ("how is gratuity calculated", "gratuity.eligibility"),
    ("what is the gratuity ceiling", "gratuity.eligibility"),
    ("am I eligible for gratuity after 5 years", "gratuity.eligibility"),
    # Paraphrases with little word overlap, which lexical search should find hard.
    ("how much time off can I take in my first few months", "leave.probation"),
    ("when will I be confirmed in my job", "probation.length"),
    ("what is my retirement savings deduction", "pf.contribution"),
    ("what do I get when I retire after a long career", "gratuity.eligibility"),
    # Off-topic: the right answer is to route to HR.
    ("what is the wifi password", None),
    ("can I bring my dog to the office", None),
    ("who won the cricket match", None),
    ("where is the cafeteria", None),
    ("how do I reset my laptop password", None),
    ("what is the dress code", None),
    ("is there a gym in the building", None),
    ("how do I claim travel expenses", None),
    ("when is the next town hall", None),
    ("what are the office timings", None),
    ("how do I get a parking pass", None),
    ("can I work from home on fridays", None),
    ("what is our maternity policy", None),
    ("how do I apply for an internal transfer", None),
    ("what is the referral bonus", None),
    ("who is my HR partner", None),
    ("what insurance cover do I have", None),
    ("do you know a good restaurant nearby", None),
    ("how do I change my bank account", None),
    ("what is the appraisal cycle", None),
]


def _pan(rng: random.Random) -> str:
    letters = "".join(rng.choices(string.ascii_uppercase, k=5))
    return f"{letters}{rng.randint(1000, 9999)}{rng.choice(string.ascii_uppercase)}"


def _aadhaar(rng: random.Random) -> str:
    body = rng.choice("23456789") + "".join(rng.choices(string.digits, k=10))
    return body + verhoeff_check_digit(body)


def _bad_aadhaar(rng: random.Random) -> str:
    good = _aadhaar(rng)
    return good[:-1] + str((int(good[-1]) + 1) % 10)


def _dob(rng: random.Random) -> str:
    return f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1970, 2002)}"


def _ifsc(rng: random.Random) -> str:
    return rng.choice(["HDFC", "SBIN", "ICIC", "UTIB"]) + "0" + "".join(rng.choices(string.digits, k=6))


def _blur(rng: random.Random, value: str) -> str:
    """Damage one character the way OCR does, so format validators catch it."""
    i = rng.randrange(len(value))
    return value[:i] + "?" + value[i + 1 :]


def _lookalike(value: str) -> str:
    """A plausible misread that still passes format checks: only a human or a model could catch it."""
    swaps = {"8": "3", "3": "8", "1": "7", "7": "1", "0": "6", "6": "0", "5": "2", "2": "5", "4": "9", "9": "4"}
    for i, ch in enumerate(value):
        if ch in swaps:
            return value[:i] + swaps[ch] + value[i + 1 :]
    return value


def _document(rng: random.Random, condition: str) -> dict[str, Any]:
    category = rng.choice(["pan", "aadhaar", "bank_proof"])
    name = rng.choice(NAMES)
    dob = _dob(rng)
    if category == "pan":
        truth = {"pan": _pan(rng), "name": name, "dob": dob}
    elif category == "aadhaar":
        truth = {"aadhaar": _aadhaar(rng), "name": name, "dob": dob}
    else:
        truth = {"account": "".join(rng.choices(string.digits, k=14)), "ifsc": _ifsc(rng)}
    shown = dict(truth)
    key = next(iter(truth))
    if condition == "blurry":
        shown[key] = _blur(rng, shown[key])
    elif condition == "bad_checksum" and category == "aadhaar":
        shown[key] = _bad_aadhaar(rng)
    elif condition == "missing":
        del shown[key]
    elif condition == "lookalike":
        shown[key] = _lookalike(shown[key]) if category != "aadhaar" else shown[key]
    lines = {
        "pan": ["INCOME TAX DEPARTMENT", "Permanent Account Number"],
        "aadhaar": ["Government of India"],
        "bank_proof": ["Cancelled cheque"],
    }[category]
    labels = {"pan": "PAN: {}", "aadhaar": "{}", "name": "Name: {}", "dob": "DOB: {}", "account": "A/c No: {}", "ifsc": "IFSC: {}"}
    lines += [labels[field].format(value) for field, value in shown.items()]
    # Synthetic reader confidence: a damaged card reads lower, with overlap. It is an assumption
    # about OCR, stated in the report, until PaddleOCR gives real numbers on real scans.
    low, high = (0.55, 0.9) if condition in ("blurry", "lookalike") else (0.82, 0.99)
    ocr_confidence = round(rng.uniform(low, high), 3)
    return {"category": category, "text": "\n".join(lines), "truth": truth, "condition": condition, "ocr_confidence": ocr_confidence}


def main() -> None:
    rng = random.Random(2026)
    (HERE / "intake").mkdir(exist_ok=True)
    (HERE / "document_extractor").mkdir(exist_ok=True)
    (HERE / "helpdesk").mkdir(exist_ok=True)

    with (HERE / "intake" / "labelled.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for intent, texts in INTAKE.items():
            for text in texts:
                handle.write(json.dumps({"text": text, "intent": intent}) + "\n")

    conditions = ["clean"] * 62 + ["blurry"] * 16 + ["bad_checksum"] * 6 + ["missing"] * 8 + ["lookalike"] * 8
    rng.shuffle(conditions)
    with (HERE / "document_extractor" / "labelled.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for condition in conditions:
            handle.write(json.dumps(_document(rng, condition)) + "\n")

    with (HERE / "helpdesk" / "labelled.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for question, chunk in HELPDESK:
            handle.write(json.dumps({"question": question, "chunk": chunk}) + "\n")


if __name__ == "__main__":
    main()
