"""wp8 policy drafts

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_drafts",
        sa.Column("pack", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tests", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pack"),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON policy_drafts TO relay_app")
    op.execute("GRANT SELECT ON policy_drafts TO relay_readonly_auditor")


def downgrade() -> None:
    op.drop_table("policy_drafts")
