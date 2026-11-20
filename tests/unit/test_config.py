from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from relay.config import CONFIG_DIR, load_config


def test_repo_config_loads() -> None:
    cfg = load_config()
    assert cfg.models.provider_for_tier("tier1").litellm_model
    assert cfg.app.engine.workers >= 2  # PRD 11.2: at least two workers to prove safety
    assert cfg.infra_costs.currency == "INR"


def test_infra_monthly_fixed_is_sum(tmp_path: Path) -> None:
    shutil.copytree(CONFIG_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / "infra_costs.yaml").write_text(
        "currency: INR\nmonthly: {vm: 1000, postgres: 500.5, gpu: 0}\n", encoding="utf-8"
    )
    assert load_config(tmp_path).infra_costs.monthly_fixed_inr == 1500.5


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    shutil.copytree(CONFIG_DIR, tmp_path, dirs_exist_ok=True)
    app = (tmp_path / "app.yaml").read_text(encoding="utf-8")
    (tmp_path / "app.yaml").write_text(app + "surprise: true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(tmp_path)


def test_bad_enum_is_rejected(tmp_path: Path) -> None:
    shutil.copytree(CONFIG_DIR, tmp_path, dirs_exist_ok=True)
    app = (tmp_path / "app.yaml").read_text(encoding="utf-8")
    (tmp_path / "app.yaml").write_text(
        app.replace("engine: sidecar", "engine: cloud_ocr"), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_config(tmp_path)


def test_env_overrides_a_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_MODEL_TIER1", "gemini_flash_lite")
    monkeypatch.delenv("RELAY_MODEL_TIER2", raising=False)
    models = load_config().models
    assert models.tiers["tier1"] == "gemini_flash_lite"
    assert models.tiers["tier2"] == "mock"


def test_env_override_naming_an_unknown_provider_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RELAY_MODEL_TIER2", "no_such_provider")
    with pytest.raises(ValidationError):
        load_config()


def test_gemini_providers_keep_reasoning_minimal() -> None:
    # Gemini 3 spends hidden reasoning tokens from the output cap; without this, JSON is cut off.
    providers = load_config().models.providers
    for name in ("gemini_flash_lite", "gemini_flash"):
        assert providers[name].reasoning_effort == "minimal"
    assert providers["mock"].reasoning_effort is None
