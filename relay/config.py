"""Typed loader for config/*.yaml and environment settings (PRD 8, 13.3, 17).

Config files hold tunables (model tiers, prices, SLAs, infra costs). Secrets only come from the
environment or a local `.env` file, never from YAML.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Price(_Strict):
    in_per_1k: float = Field(ge=0)
    out_per_1k: float = Field(ge=0)


class Provider(_Strict):
    litellm_model: str
    price_inr: Price
    region: str = "unspecified"  # shown in "what left our system" and the PII inventory
    # For reasoning models (e.g. Gemini 3). Hidden reasoning counts against the output token cap,
    # so a small cap can leave no room for the answer. "minimal" keeps short JSON replies whole.
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None


class ModelDefaults(_Strict):
    max_output_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0)
    json_mode: bool


class ModelsConfig(_Strict):
    """`config/models.yaml`: providers, tier mapping and per-skill thresholds."""

    providers: dict[str, Provider]
    tiers: dict[str, str]
    defaults: ModelDefaults
    thresholds: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _tiers_resolve(self) -> ModelsConfig:
        for tier in ("tier1", "tier2"):
            if tier not in self.tiers:
                raise ValueError(f"missing tier {tier}")
        for tier, provider in self.tiers.items():
            if provider not in self.providers:
                raise ValueError(f"tier {tier} names unknown provider {provider}")
        return self

    def provider_for_tier(self, tier: str) -> Provider:
        """Return the provider configured for a tier, e.g. `tier1`."""
        return self.providers[self.tiers[tier]]


class InfraCostsConfig(_Strict):
    """`config/infra_costs.yaml`: monthly fixed infrastructure costs in INR."""

    currency: Literal["INR"]
    monthly: dict[str, float]
    vm_vcpus: int = Field(default=2, gt=0)  # for p_cpu = VM cost / vCPU-seconds per month (PRD 17)

    @property
    def monthly_fixed_inr(self) -> float:
        """Sum of every monthly fixed cost line."""
        return float(sum(self.monthly.values()))


class EngineConfig(_Strict):
    lease_seconds: int = Field(gt=0)
    poll_interval_seconds: float = Field(gt=0)
    workers: int = Field(ge=1)


class SlaDefaults(_Strict):
    remind_at: list[str]
    escalate_after: str


class OcrConfig(_Strict):
    engine: Literal["sidecar", "paddle"]


class PiiConfig(_Strict):
    engine: Literal["regex", "presidio"]


class ChatConfig(_Strict):
    web: bool
    slack: bool


class CostAssumptions(_Strict):
    c_human_inr: float = Field(ge=0)
    c_error_inr: float = Field(ge=0)


class AppConfig(_Strict):
    """`config/app.yaml`: engine, SLA, OCR/PII engines, chat surfaces, demo mode."""

    engine: EngineConfig
    sla_defaults: SlaDefaults
    ocr: OcrConfig
    pii: PiiConfig
    chat: ChatConfig
    costs: CostAssumptions
    demo_mode: bool


class VolumesConfig(_Strict):
    """`config/volumes.yaml`: TBO headcount and monthly workflow volumes (may be unknown)."""

    headcount: int | None
    monthly: dict[str, int | None]


class Settings(BaseSettings):
    """Secrets and connection strings from the environment / `.env`."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://relay:relay@localhost:55432/relay"
    relay_encryption_key: str = ""
    relay_jwt_secret: str = "dev-only-secret"
    llm_provider_api_key: str = ""


class RelayConfig(_Strict):
    """All YAML config files, validated."""

    models: ModelsConfig
    infra_costs: InfraCostsConfig
    app: AppConfig
    volumes: VolumesConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def _with_tier_overrides(models: dict[str, Any]) -> dict[str, Any]:
    """Apply RELAY_MODEL_TIER1 / RELAY_MODEL_TIER2 from the environment (provider names)."""
    tiers = dict(models.get("tiers") or {})
    for tier in ("tier1", "tier2"):
        chosen = os.environ.get(f"RELAY_MODEL_{tier.upper()}", "").strip()
        if chosen:
            tiers[tier] = chosen
    return {**models, "tiers": tiers}


def load_config(config_dir: Path = CONFIG_DIR) -> RelayConfig:
    """Load and validate every file in `config_dir`. Raises on any schema error."""
    return RelayConfig(
        models=ModelsConfig.model_validate(
            _with_tier_overrides(_read_yaml(config_dir / "models.yaml"))
        ),
        infra_costs=InfraCostsConfig.model_validate(_read_yaml(config_dir / "infra_costs.yaml")),
        app=AppConfig.model_validate(_read_yaml(config_dir / "app.yaml")),
        volumes=VolumesConfig.model_validate(_read_yaml(config_dir / "volumes.yaml")),
    )


@lru_cache(maxsize=1)
def get_config() -> RelayConfig:
    """Process-wide cached config."""
    return load_config()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings."""
    return Settings()
