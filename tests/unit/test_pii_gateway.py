"""Gateway tests: masking, stable per-run tokens, unmasking, and that the vault stays in memory."""

import copy
import logging
import pickle
import random

import pytest

from relay.models.pii_gateway import PiiGateway, PiiVault, resolve_overlaps
from relay.models.recognizers import Entity, Span, verhoeff_check_digit

TEXT = "PAN ABCPK1234L, mail asha@example.com, phone 9876543210."


def test_mask_replaces_values_with_tokens_and_reports_counts() -> None:
    result = PiiGateway().mask(TEXT, PiiVault())
    assert result.text == "PAN <PAN_1>, mail <EMAIL_1>, phone <MOBILE_1>."
    assert result.entity_counts == {Entity.PAN: 1, Entity.EMAIL: 1, Entity.MOBILE: 1}


def test_tokens_are_stable_within_a_run_across_calls_and_spellings() -> None:
    gateway, vault = PiiGateway(), PiiVault()
    first = gateway.mask("ABCPK1234L and PQRST6789Z", vault).text
    second = gateway.mask("again abcpk1234l then PQRST6789Z", vault).text
    assert first == "<PAN_1> and <PAN_2>"
    assert second == "again <PAN_1> then <PAN_2>"
    phones = gateway.mask("+91 9876543210 / 98765-43210 / 09876543210", vault).text
    assert phones == "<MOBILE_1> / <MOBILE_1> / <MOBILE_1>"


def test_each_run_numbers_its_tokens_independently() -> None:
    gateway = PiiGateway()
    assert gateway.mask("ABCPK1234L", PiiVault()).text == "<PAN_1>"
    assert gateway.mask("PQRST6789Z", PiiVault()).text == "<PAN_1>"


def test_unmask_round_trips_and_keeps_the_original_spelling() -> None:
    gateway, vault = PiiGateway(), PiiVault()
    masked = gateway.mask(TEXT, vault)
    assert gateway.unmask(masked.text, vault) == TEXT


def test_unmask_leaves_tokens_it_did_not_issue() -> None:
    gateway, vault = PiiGateway(), PiiVault()
    gateway.mask("ABCPK1234L", vault)
    assert gateway.unmask("<PAN_1> <PAN_9> <b>", vault) == "ABCPK1234L <PAN_9> <b>"


def test_text_without_pii_is_unchanged() -> None:
    result = PiiGateway().mask("How many leaves in probation?", PiiVault())
    assert result.text == "How many leaves in probation?"
    assert result.entity_counts == {}


def test_random_synthetic_values_never_survive_masking() -> None:
    rng = random.Random(11)
    gateway = PiiGateway()
    for _ in range(300):
        body = rng.choice("23456789") + "".join(rng.choices("0123456789", k=10))
        aadhaar = body + verhoeff_check_digit(body)
        letters = "".join(rng.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=5))
        pan = f"{letters}{rng.randint(1000, 9999)}Z"
        mobile = rng.choice("6789") + "".join(rng.choices("0123456789", k=9))
        email = f"x{letters.lower()}@example.com"
        vault = PiiVault()
        text = f"Aadhaar {aadhaar}; PAN {pan}; Mobile {mobile}; {email}"
        masked = gateway.mask(text, vault).text
        for value in (aadhaar, pan, mobile, email):
            assert value not in masked
        assert gateway.unmask(masked, vault) == text


def test_overlaps_prefer_score_then_length() -> None:
    low = Span(Entity.UAN, 0, 12, 0.6, "x" * 12)
    high = Span(Entity.AADHAAR, 0, 12, 0.95, "x" * 12)
    short = Span(Entity.MOBILE, 2, 12, 0.95, "x" * 10)
    apart = Span(Entity.EMAIL, 20, 25, 0.1, "x" * 5)
    assert resolve_overlaps([low, short, apart, high]) == [high, apart]


def test_vault_is_never_serializable_or_visible_in_repr() -> None:
    vault = PiiVault()
    PiiGateway().mask("ABCPK1234L", vault)
    assert "ABCPK1234L" not in repr(vault)
    with pytest.raises(TypeError):
        pickle.dumps(vault)
    with pytest.raises(TypeError):
        copy.deepcopy(vault)


def test_masking_logs_nothing_sensitive(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        PiiGateway().mask(TEXT, PiiVault())
    assert "ABCPK1234L" not in caplog.text
