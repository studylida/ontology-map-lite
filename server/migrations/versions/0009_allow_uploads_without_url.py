"""Allow user-uploaded source documents without an external URL.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_source_document__url_nonblank", "source_document", type_="check"
    )
    op.alter_column("source_document", "canonical_url", nullable=True)
    op.create_check_constraint(
        "ck_source_document__url_nonblank",
        "source_document",
        "canonical_url IS NULL OR btrim(canonical_url) <> ''",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM source_document WHERE canonical_url IS NULL)"
        )
    ):
        raise RuntimeError("UPLOAD_SOURCE_DOCUMENT_URL_DOWNGRADE_REQUIRES_URLS")
    op.drop_constraint(
        "ck_source_document__url_nonblank", "source_document", type_="check"
    )
    op.alter_column("source_document", "canonical_url", nullable=False)
    op.create_check_constraint(
        "ck_source_document__url_nonblank",
        "source_document",
        "btrim(canonical_url) <> ''",
    )
