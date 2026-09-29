"""add_insight_fingerprint

Revision ID: e3a1_add_insight_fingerprint
Revises: d2ce44c81da4
Create Date: 2026-09-29 08:30:00.000000
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "e3a1_add_insight_fingerprint"
down_revision: str | None = "d2ce44c81da4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "node_insights",
        sa.Column("input_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_node_insights_input_fingerprint"),
        "node_insights",
        ["input_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_node_insights_input_fingerprint"), table_name="node_insights"
    )
    op.drop_column("node_insights", "input_fingerprint")
