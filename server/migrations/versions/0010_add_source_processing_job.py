"""Add durable source processing jobs.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_processing_job",
        sa.Column(
            "source_processing_job_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("source_document_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'QUEUED'"), nullable=False
        ),
        sa.Column(
            "stage", sa.Text(), server_default=sa.text("'QUEUED'"), nullable=False
        ),
        sa.Column("extraction_task_id", sa.BigInteger(), nullable=True),
        sa.Column("promotion_batch_id", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'READY', 'FAILED', 'INTERRUPTED', "
            "'EXCLUDED_LANGUAGE')",
            name="ck_source_processing_job__status",
        ),
        sa.CheckConstraint(
            "btrim(stage) <> ''", name="ck_source_processing_job__stage_nonblank"
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR "
            "(btrim(error_code) <> '' AND error_code ~ '^[A-Z][A-Z0-9_]*$')",
            name="ck_source_processing_job__error_code",
        ),
        sa.CheckConstraint(
            "isfinite(created_at) AND "
            "(started_at IS NULL OR isfinite(started_at)) AND "
            "(finished_at IS NULL OR isfinite(finished_at))",
            name="ck_source_processing_job__timestamps_finite",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_source_processing_job__started_order",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= COALESCE(started_at, created_at)",
            name="ck_source_processing_job__finished_order",
        ),
        sa.CheckConstraint(
            "(status = 'QUEUED' AND started_at IS NULL AND finished_at IS NULL "
            "AND error_code IS NULL) OR "
            "(status = 'RUNNING' AND started_at IS NOT NULL AND finished_at IS NULL "
            "AND error_code IS NULL) OR "
            "(status = 'READY' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(status = 'FAILED' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND error_code IS NOT NULL) OR "
            "(status = 'INTERRUPTED' AND finished_at IS NOT NULL "
            "AND error_code IS NOT NULL) OR "
            "(status = 'EXCLUDED_LANGUAGE' AND finished_at IS NOT NULL "
            "AND error_code IS NULL)",
            name="ck_source_processing_job__state_shape",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_document.source_document_id"],
            name="fk_source_processing_job__source_document",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_task_id"],
            ["model_task.model_task_id"],
            name="fk_source_processing_job__extraction_task",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["promotion_batch_id"],
            ["promotion_batch.promotion_batch_id"],
            name="fk_source_processing_job__promotion_batch",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "source_processing_job_id", name="pk_source_processing_job"
        ),
        comment=(
            "업로드된 source_document 하나를 추출·승격·공개하는 제품 작업. provider "
            "응답이나 원문을 저장하지 않고 현재 단계와 안전한 실패 코드만 보존한다."
        ),
    )
    op.create_index(
        "ix_source_processing_job__latest",
        "source_processing_job",
        ["source_document_id", sa.text("source_processing_job_id DESC")],
        unique=False,
    )
    op.create_index(
        "uq_source_processing_job__reusable",
        "source_processing_job",
        ["source_document_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('QUEUED', 'RUNNING', 'READY', 'EXCLUDED_LANGUAGE')"
        ),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM source_processing_job)")
    ):
        raise RuntimeError("SOURCE_PROCESSING_DOWNGRADE_REFUSES_JOB_HISTORY")
    op.drop_index(
        "uq_source_processing_job__reusable", table_name="source_processing_job"
    )
    op.drop_index(
        "ix_source_processing_job__latest", table_name="source_processing_job"
    )
    op.drop_table("source_processing_job")
