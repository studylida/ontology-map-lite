"""Durable promotion-side provenance for canonical changes from issue #216.

This module records only immutable facts about canonical mutations that cannot be
attributed to a promotion batch from the canonical schema itself. It is not a
publication job, workflow table, event-sourcing ledger, or affected-node store.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.schema import metadata

CHANGE_KINDS = (
    "NODE_ALIAS_CHANGED",
    "NODE_ALIAS_EVIDENCE_ADDED",
    "CLAIM_OBSERVATION_ADDED",
    "CLAIM_RELATION_ADDED",
    "CLAIM_ATTRIBUTE_VALUE_ADDED",
    "EVENT_TEMPORAL_BASIS_ADDED",
)
_CHANGE_KINDS_SQL = ", ".join(f"'{value}'" for value in CHANGE_KINDS)

promotion_canonical_change = sa.Table(
    "promotion_canonical_change",
    metadata,
    sa.Column(
        "promotion_canonical_change_id",
        sa.BigInteger,
        sa.Identity(always=True),
        nullable=False,
    ),
    sa.Column("promotion_batch_id", sa.BigInteger, nullable=False),
    sa.Column("change_kind", sa.Text, nullable=False),
    sa.Column("node_alias_id", sa.BigInteger),
    sa.Column("observation_id", sa.BigInteger),
    sa.Column("claim_id", sa.BigInteger),
    sa.Column("relation_id", sa.BigInteger),
    sa.Column("claim_attribute_value_id", sa.BigInteger),
    sa.Column("event_node_id", sa.BigInteger),
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
        ("node_alias_evidence.node_alias_id", "node_alias_evidence.observation_id"),
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
        f"change_kind IN ({_CHANGE_KINDS_SQL})",
        name="ck_promotion_canonical_change__kind",
    ),
    sa.CheckConstraint(
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
        ")",
        name="ck_promotion_canonical_change__target_shape",
    ),
    comment=(
        "promotion transaction이 기존 canonical object를 재사용하면서 실제로 "
        "추가한 association/change의 불변 batch provenance. publication 상태나 "
        "affected node, workflow attempt, 임의 payload를 저장하지 않는다."
    ),
)
sa.Index(
    "ix_promotion_canonical_change__batch",
    promotion_canonical_change.c.promotion_batch_id,
    promotion_canonical_change.c.promotion_canonical_change_id,
)
sa.Index(
    "uq_promotion_canonical_change__node_alias_changed",
    promotion_canonical_change.c.promotion_batch_id,
    promotion_canonical_change.c.node_alias_id,
    unique=True,
    postgresql_where=sa.text("change_kind = 'NODE_ALIAS_CHANGED'"),
)
sa.Index(
    "uq_promotion_canonical_change__node_alias_evidence_added",
    promotion_canonical_change.c.promotion_batch_id,
    promotion_canonical_change.c.node_alias_id,
    promotion_canonical_change.c.observation_id,
    unique=True,
    postgresql_where=sa.text("change_kind = 'NODE_ALIAS_EVIDENCE_ADDED'"),
)
sa.Index(
    "uq_promotion_canonical_change__claim_observation_added",
    promotion_canonical_change.c.promotion_batch_id,
    promotion_canonical_change.c.claim_id,
    promotion_canonical_change.c.observation_id,
    unique=True,
    postgresql_where=sa.text("change_kind = 'CLAIM_OBSERVATION_ADDED'"),
)
sa.Index(
    "uq_promotion_canonical_change__claim_relation_added",
    promotion_canonical_change.c.promotion_batch_id,
    promotion_canonical_change.c.claim_id,
    promotion_canonical_change.c.relation_id,
    unique=True,
    postgresql_where=sa.text("change_kind = 'CLAIM_RELATION_ADDED'"),
)
sa.Index(
    "uq_promotion_canonical_change__claim_attribute_value_added",
    promotion_canonical_change.c.promotion_batch_id,
    promotion_canonical_change.c.claim_attribute_value_id,
    unique=True,
    postgresql_where=sa.text("change_kind = 'CLAIM_ATTRIBUTE_VALUE_ADDED'"),
)
sa.Index(
    "uq_promotion_canonical_change__event_temporal_basis_added",
    promotion_canonical_change.c.promotion_batch_id,
    promotion_canonical_change.c.event_node_id,
    promotion_canonical_change.c.claim_id,
    unique=True,
    postgresql_where=sa.text("change_kind = 'EVENT_TEMPORAL_BASIS_ADDED'"),
)


@dataclass(frozen=True)
class PromotionCanonicalChange:
    promotion_canonical_change_id: int
    promotion_batch_id: int
    change_kind: str
    node_alias_id: int | None
    observation_id: int | None
    claim_id: int | None
    relation_id: int | None
    claim_attribute_value_id: int | None
    event_node_id: int | None


def require_pending_batch(session: Session, batch_id: int) -> None:
    """Lock and validate the caller-owned promotion transaction."""
    if not session.in_transaction():
        raise ValueError("an explicit caller-owned promotion transaction is required")
    row = (
        session.execute(
            sa.text("""
                SELECT promotion_status, publication_status
                FROM promotion_batch
                WHERE promotion_batch_id = :batch_id
                FOR UPDATE
            """),
            {"batch_id": batch_id},
        )
        .mappings()
        .one()
    )
    if (row["promotion_status"], row["publication_status"]) != (
        "PENDING",
        "NOT_STARTED",
    ):
        raise ValueError("canonical writes require a pending promotion batch")


def _record_change(
    session: Session,
    batch_id: int,
    change_kind: str,
    *,
    node_alias_id: int | None = None,
    observation_id: int | None = None,
    claim_id: int | None = None,
    relation_id: int | None = None,
    claim_attribute_value_id: int | None = None,
    event_node_id: int | None = None,
) -> None:
    if change_kind not in CHANGE_KINDS:
        raise ValueError(f"unsupported promotion canonical change kind: {change_kind}")
    session.execute(
        sa.text("""
            INSERT INTO promotion_canonical_change (
                promotion_batch_id, change_kind, node_alias_id, observation_id,
                claim_id, relation_id, claim_attribute_value_id, event_node_id
            ) VALUES (
                :promotion_batch_id, :change_kind, :node_alias_id, :observation_id,
                :claim_id, :relation_id, :claim_attribute_value_id, :event_node_id
            )
            ON CONFLICT DO NOTHING
        """),
        {
            "promotion_batch_id": batch_id,
            "change_kind": change_kind,
            "node_alias_id": node_alias_id,
            "observation_id": observation_id,
            "claim_id": claim_id,
            "relation_id": relation_id,
            "claim_attribute_value_id": claim_attribute_value_id,
            "event_node_id": event_node_id,
        },
    )


def record_node_alias_changed(
    session: Session,
    batch_id: int,
    node_alias_id: int,
) -> None:
    """Record a node_alias row that this promotion actually inserted or changed."""
    require_pending_batch(session, batch_id)
    _record_change(
        session,
        batch_id,
        "NODE_ALIAS_CHANGED",
        node_alias_id=node_alias_id,
    )


def add_node_alias_evidence(
    session: Session,
    batch_id: int,
    node_alias_id: int,
    observation_id: int,
) -> bool:
    """Add exact alias evidence and provenance only when the association is new."""
    require_pending_batch(session, batch_id)
    inserted = session.execute(
        sa.text("""
            INSERT INTO node_alias_evidence (node_alias_id, observation_id)
            VALUES (:node_alias_id, :observation_id)
            ON CONFLICT (node_alias_id, observation_id) DO NOTHING
            RETURNING node_alias_id
        """),
        {"node_alias_id": node_alias_id, "observation_id": observation_id},
    ).scalar_one_or_none()
    if inserted is None:
        return False
    _record_change(
        session,
        batch_id,
        "NODE_ALIAS_EVIDENCE_ADDED",
        node_alias_id=node_alias_id,
        observation_id=observation_id,
    )
    return True


def add_claim_observation(
    session: Session,
    batch_id: int,
    claim_id: int,
    observation_id: int,
) -> bool:
    """Add Claim Evidence Trace and its exact promotion provenance."""
    require_pending_batch(session, batch_id)
    inserted = session.execute(
        sa.text("""
            INSERT INTO claim_observation (claim_id, observation_id)
            VALUES (:claim_id, :observation_id)
            ON CONFLICT (claim_id, observation_id) DO NOTHING
            RETURNING claim_id
        """),
        {"claim_id": claim_id, "observation_id": observation_id},
    ).scalar_one_or_none()
    if inserted is None:
        return False
    _record_change(
        session,
        batch_id,
        "CLAIM_OBSERVATION_ADDED",
        claim_id=claim_id,
        observation_id=observation_id,
    )
    return True


def add_claim_relation(
    session: Session,
    batch_id: int,
    claim_id: int,
    relation_id: int,
    stance: str,
) -> bool:
    """Add a Claim→Relation association; existing stance changes are not #216."""
    require_pending_batch(session, batch_id)
    inserted = session.execute(
        sa.text("""
            INSERT INTO claim_relation (claim_id, relation_id, stance)
            VALUES (:claim_id, :relation_id, :stance)
            ON CONFLICT (claim_id, relation_id) DO NOTHING
            RETURNING claim_id
        """),
        {"claim_id": claim_id, "relation_id": relation_id, "stance": stance},
    ).scalar_one_or_none()
    if inserted is None:
        existing_stance = session.execute(
            sa.text("""
                SELECT stance FROM claim_relation
                WHERE claim_id = :claim_id AND relation_id = :relation_id
            """),
            {"claim_id": claim_id, "relation_id": relation_id},
        ).scalar_one()
        if existing_stance != stance:
            raise ValueError(
                "existing claim_relation stance mutation has no approved "
                "#216 change kind"
            )
        return False
    _record_change(
        session,
        batch_id,
        "CLAIM_RELATION_ADDED",
        claim_id=claim_id,
        relation_id=relation_id,
    )
    return True


def add_claim_attribute_value(
    session: Session,
    batch_id: int,
    *,
    claim_id: int,
    target_node_id: int,
    attribute_revision_id: int,
    value_kind: str,
    string_value: str | None = None,
    number_value: Decimal | int | None = None,
    unit_code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    date_from_precision: str = "UNKNOWN",
    date_to_precision: str = "UNKNOWN",
    boolean_value: bool | None = None,
) -> int:
    """Insert one canonical attribute-value row and record its exact row provenance.

    This #216 boundary deliberately does not define semantic equality or reuse
    between separate claim_attribute_value rows. Reprocess/canonicalization
    decisions belong to their owning caller before this mutation is requested.
    """
    require_pending_batch(session, batch_id)
    values = {
        "claim_id": claim_id,
        "target_node_id": target_node_id,
        "attribute_revision_id": attribute_revision_id,
        "value_kind": value_kind,
        "string_value": string_value,
        "number_value": number_value,
        "unit_code": unit_code,
        "date_from": date_from,
        "date_to": date_to,
        "date_from_precision": date_from_precision,
        "date_to_precision": date_to_precision,
        "boolean_value": boolean_value,
    }
    value_id = int(
        session.execute(
            sa.text("""
                INSERT INTO claim_attribute_value (
                    claim_id, target_node_id, attribute_revision_id, value_kind,
                    string_value, number_value, unit_code, date_from, date_to,
                    date_from_precision, date_to_precision, boolean_value
                ) VALUES (
                    :claim_id, :target_node_id, :attribute_revision_id, :value_kind,
                    :string_value, :number_value, :unit_code, :date_from, :date_to,
                    :date_from_precision, :date_to_precision, :boolean_value
                )
                RETURNING claim_attribute_value_id
            """),
            values,
        ).scalar_one()
    )
    _record_change(
        session,
        batch_id,
        "CLAIM_ATTRIBUTE_VALUE_ADDED",
        claim_attribute_value_id=value_id,
    )
    return value_id


def add_event_temporal_basis(
    session: Session,
    batch_id: int,
    event_node_id: int,
    claim_id: int,
) -> bool:
    """Add an Event temporal basis Claim and its exact promotion provenance."""
    require_pending_batch(session, batch_id)
    inserted = session.execute(
        sa.text("""
            INSERT INTO event_temporal_basis (event_node_id, claim_id)
            VALUES (:event_node_id, :claim_id)
            ON CONFLICT (event_node_id, claim_id) DO NOTHING
            RETURNING event_node_id
        """),
        {"event_node_id": event_node_id, "claim_id": claim_id},
    ).scalar_one_or_none()
    if inserted is None:
        return False
    _record_change(
        session,
        batch_id,
        "EVENT_TEMPORAL_BASIS_ADDED",
        event_node_id=event_node_id,
        claim_id=claim_id,
    )
    return True


def mark_promotion_committed(session: Session, batch_id: int) -> None:
    """Finish the caller-owned promotion transaction without committing it."""
    require_pending_batch(session, batch_id)
    session.execute(
        sa.text("""
            UPDATE promotion_batch
            SET promotion_status = 'COMMITTED', committed_at = CURRENT_TIMESTAMP
            WHERE promotion_batch_id = :batch_id
            RETURNING promotion_batch_id
        """),
        {"batch_id": batch_id},
    ).scalar_one()


def changes_for_batch(
    session: Session,
    batch_id: int,
) -> tuple[PromotionCanonicalChange, ...]:
    """Reload the exact durable change-set without process-local state."""
    rows = (
        session.execute(
            sa.text("""
                SELECT promotion_canonical_change_id, promotion_batch_id,
                       change_kind, node_alias_id, observation_id, claim_id,
                       relation_id, claim_attribute_value_id, event_node_id
                FROM promotion_canonical_change
                WHERE promotion_batch_id = :batch_id
                ORDER BY promotion_canonical_change_id
            """),
            {"batch_id": batch_id},
        )
        .mappings()
        .all()
    )
    return tuple(
        PromotionCanonicalChange(
            promotion_canonical_change_id=int(row["promotion_canonical_change_id"]),
            promotion_batch_id=int(row["promotion_batch_id"]),
            change_kind=str(row["change_kind"]),
            node_alias_id=(
                None if row["node_alias_id"] is None else int(row["node_alias_id"])
            ),
            observation_id=(
                None if row["observation_id"] is None else int(row["observation_id"])
            ),
            claim_id=None if row["claim_id"] is None else int(row["claim_id"]),
            relation_id=(
                None if row["relation_id"] is None else int(row["relation_id"])
            ),
            claim_attribute_value_id=(
                None
                if row["claim_attribute_value_id"] is None
                else int(row["claim_attribute_value_id"])
            ),
            event_node_id=(
                None if row["event_node_id"] is None else int(row["event_node_id"])
            ),
        )
        for row in rows
    )


def legacy_committed_not_started_batch_ids(session: Session) -> tuple[int, ...]:
    """Return the cutover blocker set; never infer attribution or mutate it."""
    return tuple(
        int(value)
        for value in session.execute(
            sa.text("""
                SELECT promotion_batch_id
                FROM promotion_batch
                WHERE promotion_status = 'COMMITTED'
                  AND publication_status = 'NOT_STARTED'
                ORDER BY promotion_batch_id
            """)
        ).scalars()
    )


def assert_initial_publication_cutover_safe(session: Session) -> None:
    """Block #215 enable while any pre-enable COMMITTED+NOT_STARTED batch exists."""
    batch_ids = legacy_committed_not_started_batch_ids(session)
    if batch_ids:
        rendered = ", ".join(str(value) for value in batch_ids)
        raise RuntimeError(
            "initial publication coordinator cutover is blocked by "
            f"COMMITTED+NOT_STARTED promotion batch(es): {rendered}"
        )
