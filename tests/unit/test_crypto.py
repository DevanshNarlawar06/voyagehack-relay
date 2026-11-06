from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from relay.db.crypto import decrypt, encrypt, generate_key


def test_roundtrip() -> None:
    assert decrypt(encrypt("ABCDE1234F")) == "ABCDE1234F"


def test_fresh_nonce_per_call() -> None:
    assert encrypt("same") != encrypt("same")


def test_ciphertext_hides_plaintext() -> None:
    assert "ABCDE1234F" not in encrypt("ABCDE1234F")


def test_tampering_is_detected() -> None:
    token = encrypt("secret")
    flipped = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises((InvalidTag, ValueError)):
        decrypt(flipped)


def test_wrong_key_fails() -> None:
    token = encrypt("secret", key=generate_key())
    with pytest.raises(InvalidTag):
        decrypt(token, key=generate_key())


def test_key_must_be_32_bytes() -> None:
    with pytest.raises(ValueError):
        encrypt("x", key="c2hvcnQ=")
