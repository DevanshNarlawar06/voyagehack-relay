from __future__ import annotations

from pathlib import Path

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


def test_key_from_dotenv_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #26: a key in .env (not in the process environment) must be the key used."""
    from relay.config import Settings
    from relay.db import crypto

    key = generate_key()
    env_file = tmp_path / ".env"
    env_file.write_text(f"RELAY_ENCRYPTION_KEY={key}\n", encoding="utf-8")
    monkeypatch.delenv("RELAY_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(_env_file=env_file))
    token = encrypt("ABCDE1234F")
    assert decrypt(token, key=key) == "ABCDE1234F"


def test_missing_key_outside_demo_mode_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    from relay.config import Settings, get_config
    from relay.db import crypto

    cfg = get_config()
    no_demo = cfg.model_copy(update={"app": cfg.app.model_copy(update={"demo_mode": False})})
    monkeypatch.delenv("RELAY_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(crypto, "get_config", lambda: no_demo)
    with pytest.raises(crypto.MissingEncryptionKey):
        encrypt("x")


def test_missing_key_in_demo_mode_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from relay.config import Settings
    from relay.db import crypto

    monkeypatch.delenv("RELAY_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(crypto, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(crypto, "_warned_dev_key", False)
    with caplog.at_level("WARNING"):
        assert decrypt(encrypt("x")) == "x"
        assert decrypt(encrypt("y")) == "y"
    assert caplog.text.count("dev key") == 1  # once per process, not per call
