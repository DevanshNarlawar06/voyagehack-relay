"""Connector manifests: `definitions/connectors/*.yaml` hold every endpoint shape (PRD 15.1, 24).

Adapting to TBO's real mock APIs means editing these files, not code.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFINITIONS_DIR = Path(__file__).resolve().parents[2] / "definitions" / "connectors"
_ENV_REFERENCE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")

ErrorKind = Literal["transient", "permanent", "conflict"]
IdempotencyMode = Literal["header", "reference_lookup", "natural_key"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReconcileSpec(_Strict):
    """How to ask the system whether an earlier attempt landed: GET `path`?`by`=value."""

    path: str
    by: str = "client_reference"


class OpSpec(_Strict):
    """One operation of a connector."""

    side_effect: bool
    method: Literal["GET", "POST"]
    path: str
    idempotency_mode: IdempotencyMode | None = None
    timeout_seconds: float = Field(default=10, gt=0)
    inject_reference: bool = False  # put `client_reference` in the body (PRD 11.5 step 3)
    defaults: dict[str, Any] = Field(default_factory=dict)  # fixed fields, e.g. a channel
    reconcile: ReconcileSpec | None = None  # None means the op is safe to simply repeat
    error_map: dict[int, ErrorKind] = Field(default_factory=dict)  # overrides the default mapping
    cost_inr: Decimal = Decimal(0)

    @model_validator(mode="after")
    def _side_effects_declare_a_mode(self) -> OpSpec:
        if self.side_effect and self.idempotency_mode is None:
            raise ValueError("an op with a side effect must declare idempotency_mode")
        return self


class ConnectorManifest(_Strict):
    """A connector definition file."""

    connector: str
    version: int = Field(ge=1)
    base_url: str
    health_path: str
    ops: dict[str, OpSpec] = Field(min_length=1)

    def resolved_base_url(self) -> str:
        """Expand `${VAR:-default}` from the environment. A missing variable with no default fails."""

        def expand(match: re.Match[str]) -> str:
            value = os.environ.get(match.group(1), match.group(2))
            if value is None:
                raise ValueError(f"{self.connector}: {match.group(1)} is not set")
            return value

        return _ENV_REFERENCE.sub(expand, self.base_url).rstrip("/")


def load_manifest(path: Path) -> ConnectorManifest:
    """Read and validate one manifest file."""
    with path.open(encoding="utf-8") as handle:
        return ConnectorManifest.model_validate(yaml.safe_load(handle))


def load_manifests(directory: Path = DEFINITIONS_DIR) -> dict[str, ConnectorManifest]:
    """Read every manifest in `directory`, keyed by connector name."""
    manifests = [load_manifest(path) for path in sorted(directory.glob("*.yaml"))]
    by_name = {manifest.connector: manifest for manifest in manifests}
    if len(by_name) != len(manifests):
        raise ValueError(f"duplicate connector name in {directory}")
    return by_name
