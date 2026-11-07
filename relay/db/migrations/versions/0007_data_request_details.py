"""data request details: what to correct, or why to erase (issue #89)

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("data_requests", sa.Column("details", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("data_requests", "details")
