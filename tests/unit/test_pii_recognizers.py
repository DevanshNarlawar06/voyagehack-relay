"""Recognizer tests, plus the WP6a acceptance gate: precision >= 0.95 on a fixture set.

All identifiers are synthetic. Aadhaar-shaped values are computed so the checksum is real without
any literal in the repo being copied from a person.
"""

import random
from collections import Counter

import pytest

from relay.models.pii_gateway import PiiGateway
from relay.models.recognizers import Entity, verhoeff_check_digit, verhoeff_is_valid


def aadhaar(prefix: str) -> str:
    """Build a checksum-valid 12-digit number from an 11-digit synthetic prefix."""
    return prefix + verhoeff_check_digit(prefix)


def not_aadhaar(prefix: str) -> str:
    """Build a 12-digit number from `prefix` whose checksum deliberately fails."""
    return prefix + str((int(verhoeff_check_digit(prefix)) + 1) % 10)


def found(text: str) -> list[tuple[Entity, str]]:
    """Return (entity, text) for each span the gateway keeps."""
    return [(s.entity, s.text) for s in PiiGateway().analyze(text)]


@pytest.mark.parametrize(("digits", "valid"), [("2363", True), ("2364", False), ("123451", True)])
def test_verhoeff_known_vectors(digits: str, valid: bool) -> None:
    assert verhoeff_is_valid(digits) is valid


@pytest.mark.parametrize("bad", ["", "12a4", "٣٤"])
def test_verhoeff_rejects_non_digits(bad: str) -> None:
    assert verhoeff_is_valid(bad) is False


def test_verhoeff_check_digit_round_trips_and_catches_single_digit_errors() -> None:
    rng = random.Random(7)
    for _ in range(200):
        body = "".join(rng.choices("0123456789", k=11))
        number = body + verhoeff_check_digit(body)
        assert verhoeff_is_valid(number)
        i = rng.randrange(12)
        swapped = str((int(number[i]) + rng.randint(1, 9)) % 10)
        assert not verhoeff_is_valid(number[:i] + swapped + number[i + 1 :])


def test_check_digit_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        verhoeff_check_digit("12x")


@pytest.mark.parametrize("value", ["ABCDE1234F", "abcde1234f"])
def test_pan(value: str) -> None:
    assert found(f"PAN is {value}.") == [(Entity.PAN, value)]


@pytest.mark.parametrize("value", ["ABCDE12345", "ABCD1234F", "XABCDE1234FY"])
def test_pan_shape_negatives(value: str) -> None:
    assert found(f"ref {value}") == []


def test_aadhaar_requires_checksum_to_be_labelled_aadhaar() -> None:
    good = aadhaar("23456789012")
    bad = not_aadhaar("23456789012")
    assert found(f"id {good}") == [(Entity.AADHAAR, good)]
    assert found(f"id {bad}") == [(Entity.UAN, bad)]


@pytest.mark.parametrize("sep", [" ", "-"])
def test_aadhaar_grouped(sep: str) -> None:
    n = aadhaar("34567890123")
    value = sep.join((n[:4], n[4:8], n[8:]))
    assert found(f"Aadhaar: {value}") == [(Entity.AADHAAR, value)]


def test_aadhaar_cannot_start_with_0_or_1() -> None:
    n = aadhaar("13456789012")
    assert found(f"id {n}") == [(Entity.UAN, n)]


@pytest.mark.parametrize("value", ["XXXX XXXX 1234", "xxxx-xxxx-9876", "********4321"])
def test_masked_aadhaar_is_still_masked(value: str) -> None:
    assert found(f"Aadhaar {value}") == [(Entity.AADHAAR, value)]


def test_uan_context_outranks_a_passing_aadhaar_checksum() -> None:
    n = aadhaar("23456789012")
    assert found(f"UAN: {n}") == [(Entity.UAN, n)]


def test_context_on_a_previous_line_does_not_count() -> None:
    n = aadhaar("23456789012")
    assert found(f"UAN:\n{n}") == [(Entity.AADHAAR, n)]


def test_ifsc() -> None:
    assert found("IFSC HDFC0001234") == [(Entity.IFSC, "HDFC0001234")]
    assert found("code HDFC1001234") == []


@pytest.mark.parametrize(
    "value", ["9876543210", "+91 9876543210", "+91-98765-43210", "09876543210", "98765 43210"]
)
def test_mobile(value: str) -> None:
    assert found(f"call {value} now") == [(Entity.MOBILE, value)]


@pytest.mark.parametrize("value", ["5876543210", "987654321", "98765432101234"])
def test_mobile_negatives(value: str) -> None:
    assert found(f"call {value} now") == []


def test_email() -> None:
    assert found("mail a.b+c@mail.tbo-test.co.in today") == [
        (Entity.EMAIL, "a.b+c@mail.tbo-test.co.in")
    ]


@pytest.mark.parametrize(
    "value",
    [
        "14/03/1995",
        "14-03-1995",
        "14.03.1995",
        "1995-03-14",
        "14 March 1995",
        "14-Mar-1995",
        "March 14, 1995",
        "03/14/1995",
    ],
)
def test_dob_with_context(value: str) -> None:
    assert found(f"Date of Birth: {value}") == [(Entity.DOB, value)]


@pytest.mark.parametrize(
    "text",
    [
        "Joining date 14/03/2027",  # a business date, needed by cross_check
        "DOB: 31/02/1995",  # not a real date
        "DOB: 14/03/1850",  # implausibly old
        "DOB: 14/03/2999",  # future
    ],
)
def test_dob_negatives(text: str) -> None:
    assert found(text) == []


def test_bank_account_needs_a_label_and_beats_other_digit_recognizers() -> None:
    assert found("A/c No: 50100123456789") == [(Entity.BANK_ACCOUNT, "50100123456789")]
    assert found("Account number 9876543210") == [(Entity.BANK_ACCOUNT, "9876543210")]
    assert found("Invoice total 50100123456789") == []


def test_span_repr_hides_the_value() -> None:
    spans = PiiGateway().analyze("PAN ABCDE1234F")
    assert "ABCDE1234F" not in repr(spans)


def fixture_set() -> list[tuple[str, list[tuple[Entity, str]]]]:
    """Return labelled sentences: HR-style prose, OCR-style lines and hard negatives."""
    a1, a2, a3 = aadhaar("23456789012"), aadhaar("45678901234"), aadhaar("78901234567")
    grouped = f"{a2[:4]} {a2[4:8]} {a2[8:]}"
    u1, u2 = not_aadhaar("10012345678"), "100987654321"
    return [
        ("My PAN is ABCPK1234L", [(Entity.PAN, "ABCPK1234L")]),
        ("Income Tax Dept PAN: ZZTPS9876Q Name: Asha Rao", [(Entity.PAN, "ZZTPS9876Q")]),
        ("pan abcde1234f attached", [(Entity.PAN, "abcde1234f")]),
        (f"Aadhaar {a1} issued", [(Entity.AADHAAR, a1)]),
        (f"Your Aadhaar No. : {grouped}", [(Entity.AADHAAR, grouped)]),
        (f"VID-linked number {a3}", [(Entity.AADHAAR, a3)]),
        ("Aadhaar XXXX XXXX 4821 verified", [(Entity.AADHAAR, "XXXX XXXX 4821")]),
        ("IFSC: SBIN0004567, Branch MG Road", [(Entity.IFSC, "SBIN0004567")]),
        ("Bank HDFC0001234 Pune", [(Entity.IFSC, "HDFC0001234")]),
        ("ICIC0A12B34 is the code", [(Entity.IFSC, "ICIC0A12B34")]),
        (f"UAN: {u1}", [(Entity.UAN, u1)]),
        (f"My PF UAN number {u2}", [(Entity.UAN, u2)]),
        ("Mobile +91 9876543210", [(Entity.MOBILE, "+91 9876543210")]),
        ("reach me on 7012345678 after 6", [(Entity.MOBILE, "7012345678")]),
        ("Ph: 98201-23456", [(Entity.MOBILE, "98201-23456")]),
        ("mail asha.rao@example.com please", [(Entity.EMAIL, "asha.rao@example.com")]),
        ("cc hr+onb@tbo-demo.co.in", [(Entity.EMAIL, "hr+onb@tbo-demo.co.in")]),
        ("DOB: 14/03/1995", [(Entity.DOB, "14/03/1995")]),
        ("Date of Birth 02 Jan 1988", [(Entity.DOB, "02 Jan 1988")]),
        ("born on 1991-11-30 in Pune", [(Entity.DOB, "1991-11-30")]),
        ("A/c No 50100123456789", [(Entity.BANK_ACCOUNT, "50100123456789")]),
        (
            f"PAN ABCPK1234L, IFSC SBIN0004567, Aadhaar {a1}, asha@example.com, 9876543210",
            [
                (Entity.PAN, "ABCPK1234L"),
                (Entity.IFSC, "SBIN0004567"),
                (Entity.AADHAAR, a1),
                (Entity.EMAIL, "asha@example.com"),
                (Entity.MOBILE, "9876543210"),
            ],
        ),
        # Hard negatives that should stay untouched.
        ("Laptop cost is 150000 rupees", []),
        ("Grade L5, role Senior Engineer, team Payments", []),
        ("Joining date 01/12/2026, probation 6 months", []),
        ("How many leaves do I get in probation?", []),
        ("Order ORD12345 shipped, ticket 45213", []),
        ("Version 1.2.3 released on 2026-11-07", []),
        ("Employee code TBO000123 is active", []),
        ("Salary band 1200000 to 1800000", []),
        ("Laptop SKU DELL5420I7 approved", []),
        ("SLA is 72h and the reminder is at 24h", []),
        ("Call at 10:30 in room 402", []),
        # Known, accepted false positive: fail towards masking on bare 12-digit numbers.
        ("Tracking number 123456789012", []),
    ]


def test_precision_and_recall_on_fixture_set() -> None:
    tp: Counter[Entity] = Counter()
    fp: Counter[Entity] = Counter()
    fn: Counter[Entity] = Counter()
    for text, expected in fixture_set():
        got, want = Counter(found(text)), Counter(expected)
        for key in got | want:
            tp[key[0]] += min(got[key], want[key])
            fp[key[0]] += max(got[key] - want[key], 0)
            fn[key[0]] += max(want[key] - got[key], 0)
    precision = sum(tp.values()) / (sum(tp.values()) + sum(fp.values()))
    recall = sum(tp.values()) / (sum(tp.values()) + sum(fn.values()))
    assert precision >= 0.95, (precision, fp)
    assert recall >= 0.95, (recall, fn)
    # PERSON comes from known values (test_skills.py), not from a pattern, until NER exists.
    assert all(tp[entity] > 0 for entity in Entity if entity is not Entity.PERSON)
