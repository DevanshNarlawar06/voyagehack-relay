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
