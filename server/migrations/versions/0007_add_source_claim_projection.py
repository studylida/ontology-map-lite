"""Add durable source-backed claims before graph projection.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_claim",
        sa.Column(
            "source_claim_id", sa.BigInteger(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("model_task_id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_id", sa.Text(), nullable=False),
        sa.Column("statement_text", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("modality", sa.Text(), nullable=False),
        sa.Column("projection_status", sa.Text(), nullable=False),
        sa.Column(
            "proposal_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "projection_outcomes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source_claim_id", name="pk_source_claim"),
        sa.ForeignKeyConstraint(
            ("model_task_id",),
            ("model_task.model_task_id",),
            name="fk_source_claim__model_task",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.UniqueConstraint(
            "model_task_id", "candidate_id", name="uq_source_claim__task_candidate"
        ),
        sa.CheckConstraint(
            "btrim(candidate_id) <> ''", name="ck_source_claim__candidate_nonblank"
        ),
        sa.CheckConstraint(
            "btrim(statement_text) <> ''", name="ck_source_claim__statement_nonblank"
        ),
        sa.CheckConstraint(
            "btrim(language) <> ''", name="ck_source_claim__language_nonblank"
        ),
        sa.CheckConstraint(
            "modality IN ('FACT', 'PLAN_OR_TARGET', 'PREDICTION_OR_ESTIMATE', "
            "'OPINION_OR_EVALUATION')",
            name="ck_source_claim__modality",
        ),
        sa.CheckConstraint(
            "projection_status IN ('NONE', 'PARTIAL', 'COMPLETE')",
            name="ck_source_claim__projection_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(proposal_json) = 'object'",
            name="ck_source_claim__proposal_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(projection_outcomes_json) = 'object'",
            name="ck_source_claim__outcomes_object",
        ),
        sa.CheckConstraint(
            "isfinite(created_at)", name="ck_source_claim__created_at_finite"
        ),
        comment=(
            "정확한 Observation이 statement와 modality를 지지한다고 검증된 불변 Claim. "
            "graph 투영 성공과 독립된 제품 결과이며 provider 원문이나 reasoning이 아니다."
        ),
    )
    op.create_index(
        "ix_source_claim__task",
        "source_claim",
        ("model_task_id", "source_claim_id"),
    )
    op.create_index(
        "ix_source_claim__projection_status",
        "source_claim",
        ("projection_status", "source_claim_id"),
    )
    op.create_table(
        "source_claim_observation",
        sa.Column("source_claim_id", sa.BigInteger(), nullable=False),
        sa.Column("observation_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_claim_id", "observation_id", name="pk_source_claim_observation"
        ),
        sa.ForeignKeyConstraint(
            ("source_claim_id",),
            ("source_claim.source_claim_id",),
            name="fk_source_claim_observation__source_claim",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("observation_id",),
            ("observation.observation_id",),
            name="fk_source_claim_observation__observation",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        comment="Source Claim과 이를 직접 지지하는 정확한 원문 범위의 Evidence Trace.",
    )
    op.create_index(
        "ix_source_claim_observation__observation",
        "source_claim_observation",
        ("observation_id", "source_claim_id"),
    )
    op.create_table(
        "source_claim_projection",
        sa.Column("source_claim_id", sa.BigInteger(), nullable=False),
        sa.Column("claim_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_claim_id", "claim_id", name="pk_source_claim_projection"
        ),
        sa.ForeignKeyConstraint(
            ("source_claim_id",),
            ("source_claim.source_claim_id",),
            name="fk_source_claim_projection__source_claim",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("claim_id",),
            ("claim.claim_id",),
            name="fk_source_claim_projection__claim",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        comment=(
            "Source Claim에서 기존 공개 graph 수명주기의 canonical Claim으로 성공한 "
            "투영 연결. NONE 상태에는 행이 없다."
        ),
    )
    op.create_index(
        "ix_source_claim_projection__claim",
        "source_claim_projection",
        ("claim_id", "source_claim_id"),
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE source_claim, source_claim_observation, "
        "source_claim_projection IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM source_claim)")):
        raise RuntimeError("source_claim contains product data; downgrade refused")
    op.drop_index(
        "ix_source_claim_projection__claim", table_name="source_claim_projection"
    )
    op.drop_table("source_claim_projection")
    op.drop_index(
        "ix_source_claim_observation__observation",
        table_name="source_claim_observation",
    )
    op.drop_table("source_claim_observation")
    op.drop_index("ix_source_claim__projection_status", table_name="source_claim")
    op.drop_index("ix_source_claim__task", table_name="source_claim")
    op.drop_table("source_claim")
