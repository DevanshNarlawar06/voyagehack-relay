"""wp2 one infra allocation per run

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_cost_events_infra_alloc_run",
        "cost_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'infra_alloc'"),
    )


def downgrade() -> None:
    op.drop_index("uq_cost_events_infra_alloc_run", table_name="cost_events")
