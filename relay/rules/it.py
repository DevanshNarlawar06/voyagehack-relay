"""IT rule functions (PRD 5.1 step 9). Pure: the spec table lives in definitions/tables/."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from relay.config import REPO_ROOT

TABLE = REPO_ROOT / "definitions" / "tables" / "it_assets.yaml"


@cache
def _table(path: Path = TABLE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle)
    return data


def select_laptop(grade: str) -> dict[str, Any]:
    """Laptop spec for a grade. Whether it needs an approver is the approvals pack's call."""
    table = _table()
    row = table["rows"].get(grade, table["rows"]["default"])
    return {"sku": row["sku"], "price_inr": int(row["price_inr"]), "sources": [table["source"]]}
