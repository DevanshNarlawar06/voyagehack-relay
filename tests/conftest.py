"""Shared fixtures.

`db_url` creates a throwaway database on the server in DATABASE_URL, migrates it to head and drops
it after the session. DB tests are skipped when no server is reachable, unless
RELAY_REQUIRE_DB=1 (set in CI), in which case they fail.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from relay.config import get_settings


def _admin_engine() -> Engine:
    url = make_url(get_settings().database_url).set(database="postgres")
    return create_engine(url, isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    """URL of a fresh, fully migrated database."""
    admin = _admin_engine()
    try:
        with admin.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        if os.environ.get("RELAY_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"no Postgres reachable ({exc.__class__.__name__}); run docker compose up")
    name = f"relay_test_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(get_settings().database_url).set(database=name).render_as_string(False)
    try:
        from relay.db.migrate import upgrade

        upgrade("head", url=url)
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def db_engine(db_url: str) -> Iterator[Engine]:
    engine = create_engine(db_url)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Session inside a transaction that is rolled back after the test."""
    conn = db_engine.connect()
    trans = conn.begin()
    session = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()
