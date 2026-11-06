"""The demo users load repeatably and cover every role the console journey needs."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from relay.db.models import User
from seed.users import load_demo_users

pytestmark = pytest.mark.db


def test_loading_twice_changes_nothing(db_session: Session) -> None:
    first = load_demo_users(db_session)
    second = load_demo_users(db_session)
    assert first == second
    rows = db_session.scalars(select(User).where(User.email.like("%@tbo-demo.test"))).all()
    assert len(rows) == first


def test_a_changed_role_is_restored(db_session: Session) -> None:
    load_demo_users(db_session)
    admin = db_session.scalars(select(User).where(User.email == "hr.admin@tbo-demo.test")).one()
    admin.roles = ["employee"]
    load_demo_users(db_session)
    assert "hr_admin" in admin.roles


def test_every_decision_role_has_someone(db_session: Session) -> None:
    load_demo_users(db_session)
    rows = db_session.scalars(select(User).where(User.email.like("%@tbo-demo.test"))).all()
    roles = {role for user in rows for role in user.roles}
    assert {"hr_admin", "hr_ops", "manager", "approver_it_spend", "auditor"} <= roles
