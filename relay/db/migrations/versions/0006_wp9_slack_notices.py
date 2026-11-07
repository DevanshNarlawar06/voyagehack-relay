"""wp9 slack notices

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slack_notices",
        sa.Column("audit_seq", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.String(length=32), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audit_seq"),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON slack_notices TO relay_app")


def downgrade() -> None:
    op.drop_table("slack_notices")
