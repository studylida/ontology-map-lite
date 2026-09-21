"""add durable promotion canonical-change provenance

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-16 10:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANGE_KINDS = (
    "NODE_ALIAS_CHANGED",
    "NODE_ALIAS_EVIDENCE_ADDED",
    "CLAIM_OBSERVATION_ADDED",
    "CLAIM_RELATION_ADDED",
    "CLAIM_ATTRIBUTE_VALUE_ADDED",
    "EVENT_TEMPORAL_BASIS_ADDED",
)
CHANGE_KINDS_SQL = ", ".join(f"'{value}'" for value in CHANGE_KINDS)

TARGET_SHAPE_CHECK = (
    "("
    "change_kind = 'NODE_ALIAS_CHANGED' "
    "AND node_alias_id IS NOT NULL "
    "AND observation_id IS NULL AND claim_id IS NULL "
    "AND relation_id IS NULL AND claim_attribute_value_id IS NULL "
    "AND event_node_id IS NULL"
    ") OR ("
    "change_kind = 'NODE_ALIAS_EVIDENCE_ADDED' "
    "AND node_alias_id IS NOT NULL AND observation_id IS NOT NULL "
    "AND claim_id IS NULL AND relation_id IS NULL "
    "AND claim_attribute_value_id IS NULL AND event_node_id IS NULL"
    ") OR ("
    "change_kind = 'CLAIM_OBSERVATION_ADDED' "
    "AND node_alias_id IS NULL AND observation_id IS NOT NULL "
    "AND claim_id IS NOT NULL AND relation_id IS NULL "
    "AND claim_attribute_value_id IS NULL AND event_node_id IS NULL"
    ") OR ("
    "change_kind = 'CLAIM_RELATION_ADDED' "
    "AND node_alias_id IS NULL AND observation_id IS NULL "
    "AND claim_id IS NOT NULL AND relation_id IS NOT NULL "
    "AND claim_attribute_value_id IS NULL AND event_node_id IS NULL"
    ") OR ("
    "change_kind = 'CLAIM_ATTRIBUTE_VALUE_ADDED' "
    "AND node_alias_id IS NULL AND observation_id IS NULL "
    "AND claim_id IS NULL AND relation_id IS NULL "
    "AND claim_attribute_value_id IS NOT NULL AND event_node_id IS NULL"
    ") OR ("
    "change_kind = 'EVENT_TEMPORAL_BASIS_ADDED' "
    "AND node_alias_id IS NULL AND observation_id IS NULL "
    "AND claim_id IS NOT NULL AND relation_id IS NULL "
    "AND claim_attribute_value_id IS NULL AND event_node_id IS NOT NULL"
    ")"
)


def upgrade() -> None:
    op.create_table(
        "promotion_canonical_change",
        sa.Column(
            "promotion_canonical_change_id",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("promotion_batch_id", sa.BigInteger(), nullable=False),
        sa.Column("change_kind", sa.Text(), nullable=False),
        sa.Column("node_alias_id", sa.BigInteger(), nullable=True),
        sa.Column("observation_id", sa.BigInteger(), nullable=True),
        sa.Column("claim_id", sa.BigInteger(), nullable=True),
        sa.Column("relation_id", sa.BigInteger(), nullable=True),
        sa.Column("claim_attribute_value_id", sa.BigInteger(), nullable=True),
        sa.Column("event_node_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint(
            "promotion_canonical_change_id",
            name="pk_promotion_canonical_change",
        ),
        sa.ForeignKeyConstraint(
            ("promotion_batch_id",),
            ("promotion_batch.promotion_batch_id",),
            name="fk_promotion_canonical_change__promotion_batch",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("node_alias_id",),
            ("node_alias.node_alias_id",),
            name="fk_promotion_canonical_change__node_alias",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("node_alias_id", "observation_id"),
            (
                "node_alias_evidence.node_alias_id",
                "node_alias_evidence.observation_id",
            ),
            name="fk_promotion_canonical_change__node_alias_evidence",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("claim_id", "observation_id"),
            ("claim_observation.claim_id", "claim_observation.observation_id"),
            name="fk_promotion_canonical_change__claim_observation",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("claim_id", "relation_id"),
            ("claim_relation.claim_id", "claim_relation.relation_id"),
            name="fk_promotion_canonical_change__claim_relation",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("claim_attribute_value_id",),
            ("claim_attribute_value.claim_attribute_value_id",),
            name="fk_promotion_canonical_change__attribute_value",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("event_node_id", "claim_id"),
            ("event_temporal_basis.event_node_id", "event_temporal_basis.claim_id"),
            name="fk_promotion_canonical_change__event_temporal_basis",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.CheckConstraint(
            f"change_kind IN ({CHANGE_KINDS_SQL})",
            name="ck_promotion_canonical_change__kind",
        ),
        sa.CheckConstraint(
            TARGET_SHAPE_CHECK,
            name="ck_promotion_canonical_change__target_shape",
        ),
        comment=(
            "promotion transaction이 기존 canonical object를 재사용하면서 실제로 "
            "추가한 association/change의 불변 batch provenance. publication 상태나 "
            "affected node, workflow attempt, 임의 payload를 저장하지 않는다."
        ),
    )
    op.create_index(
        "ix_promotion_canonical_change__batch",
        "promotion_canonical_change",
        ("promotion_batch_id", "promotion_canonical_change_id"),
        unique=False,
    )
    op.create_index(
        "uq_promotion_canonical_change__node_alias_changed",
        "promotion_canonical_change",
        ("promotion_batch_id", "node_alias_id"),
        unique=True,
        postgresql_where=sa.text("change_kind = 'NODE_ALIAS_CHANGED'"),
    )
    op.create_index(
        "uq_promotion_canonical_change__node_alias_evidence_added",
        "promotion_canonical_change",
        ("promotion_batch_id", "node_alias_id", "observation_id"),
        unique=True,
        postgresql_where=sa.text("change_kind = 'NODE_ALIAS_EVIDENCE_ADDED'"),
    )
    op.create_index(
        "uq_promotion_canonical_change__claim_observation_added",
        "promotion_canonical_change",
        ("promotion_batch_id", "claim_id", "observation_id"),
        unique=True,
        postgresql_where=sa.text("change_kind = 'CLAIM_OBSERVATION_ADDED'"),
    )
    op.create_index(
        "uq_promotion_canonical_change__claim_relation_added",
        "promotion_canonical_change",
        ("promotion_batch_id", "claim_id", "relation_id"),
        unique=True,
        postgresql_where=sa.text("change_kind = 'CLAIM_RELATION_ADDED'"),
    )
    op.create_index(
        "uq_promotion_canonical_change__claim_attribute_value_added",
        "promotion_canonical_change",
        ("promotion_batch_id", "claim_attribute_value_id"),
        unique=True,
        postgresql_where=sa.text("change_kind = 'CLAIM_ATTRIBUTE_VALUE_ADDED'"),
    )
    op.create_index(
        "uq_promotion_canonical_change__event_temporal_basis_added",
        "promotion_canonical_change",
        ("promotion_batch_id", "event_node_id", "claim_id"),
        unique=True,
        postgresql_where=sa.text("change_kind = 'EVENT_TEMPORAL_BASIS_ADDED'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_promotion_canonical_change__event_temporal_basis_added",
        table_name="promotion_canonical_change",
    )
    op.drop_index(
        "uq_promotion_canonical_change__claim_attribute_value_added",
        table_name="promotion_canonical_change",
    )
    op.drop_index(
        "uq_promotion_canonical_change__claim_relation_added",
        table_name="promotion_canonical_change",
    )
    op.drop_index(
        "uq_promotion_canonical_change__claim_observation_added",
        table_name="promotion_canonical_change",
    )
    op.drop_index(
        "uq_promotion_canonical_change__node_alias_evidence_added",
        table_name="promotion_canonical_change",
    )
    op.drop_index(
        "uq_promotion_canonical_change__node_alias_changed",
        table_name="promotion_canonical_change",
    )
    op.drop_index(
        "ix_promotion_canonical_change__batch",
        table_name="promotion_canonical_change",
    )
    op.drop_table("promotion_canonical_change")
