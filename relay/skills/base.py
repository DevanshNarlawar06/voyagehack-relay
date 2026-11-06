"""Skill manifests and prompts (PRD 13.1). Skills return outputs; the engine decides what happens."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from relay.config import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "definitions" / "skills"
PROMPTS_DIR = REPO_ROOT / "definitions" / "prompts"


class SkillManifest(BaseModel):
    """A `definitions/skills/*.yaml` file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill: str
    version: int = Field(ge=1)
    description: str
    principal: str
    allowed_actions: list[str] = Field(min_length=1)
    output_schema: dict[str, Any]
    prompt_version: str
    threshold: float | None = Field(default=None, ge=0, le=1)  # from evals/threshold_report.md
    max_output_tokens: int | None = Field(default=None, gt=0)
    # What docs/pii-inventory.md is generated from (PRD 14.1).
    data_seen: list[str] = Field(min_length=1)
    masking: str
    model_use: str
    retention: str


def load_manifest(name: str, directory: Path = SKILLS_DIR) -> SkillManifest:
    """Load and validate one skill manifest."""
    with (directory / f"{name}.yaml").open(encoding="utf-8") as handle:
        manifest = SkillManifest.model_validate(yaml.safe_load(handle))
    if manifest.skill != name:
        raise ValueError(f"{name}.yaml declares skill {manifest.skill}")
    return manifest


def load_prompt(manifest: SkillManifest, directory: Path = PROMPTS_DIR) -> str:
    """Return the prompt text a manifest points to."""
    return (directory / manifest.skill / f"{manifest.prompt_version}.md").read_text(
        encoding="utf-8"
    )
