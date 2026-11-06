"""Loads the demo users. Separate from the dataset loader so the console works before any seed."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.db.models import User

DEMO_USERS = Path(__file__).parent / "demo_users.yaml"


def load_demo_users(session: Session, path: Path = DEMO_USERS) -> int:
    """Create or update each user in `path` and return how many there are. Safe to repeat."""
    with path.open(encoding="utf-8") as handle:
        entries = yaml.safe_load(handle)["users"]
    for entry in entries:
        user = session.scalar(select(User).where(User.email == entry["email"]))
        if user is None:
            session.add(User(**entry))
        else:
            user.display_name = entry["display_name"]
            user.roles = entry["roles"]
    session.flush()
    return len(entries)
