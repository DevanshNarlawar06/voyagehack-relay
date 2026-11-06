"""Programmatic Alembic helpers (used by the CLI and tests)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from relay.config import REPO_ROOT


def alembic_config() -> Config:
    """Alembic config pointing at our migrations."""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "relay" / "db" / "migrations"))
    return cfg


def upgrade(revision: str = "head", url: str | None = None) -> None:
    """Apply migrations up to `revision`; `url` overrides DATABASE_URL."""
    cfg = alembic_config()
    if url:
        from sqlalchemy import create_engine, pool

        engine = create_engine(url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, revision)
        engine.dispose()
    else:
        command.upgrade(cfg, revision)


def downgrade(revision: str, url: str | None = None) -> None:
    """Downgrade to `revision`; `url` overrides DATABASE_URL."""
    cfg = alembic_config()
    if url:
        from sqlalchemy import create_engine, pool

        engine = create_engine(url, poolclass=pool.NullPool)
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, revision)
        engine.dispose()
    else:
        command.downgrade(cfg, revision)
