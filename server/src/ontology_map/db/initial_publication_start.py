"""Restart-safe affected-node projection and publication lifecycle for #215."""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import promotion_provenance
from ontology_map.db import schema as s
from ontology_map.db.initial_publication_contracts import (
    InitialPublicationStart,
    PublicationStateError,
)


def affected_node_ids(session: Session, batch_id: int) -> tuple[int, ...]:
    """Project direct affected evidence-backed Nodes from durable sources."""
    values = session.execute(
        sa.text(
            """
            WITH batch_items AS (
                SELECT knowledge_item_id, item_kind
                FROM knowledge_item
                WHERE promotion_batch_id = :batch_id
            ), projection_claims AS (
                SELECT knowledge_item_id AS claim_id
                FROM batch_items
                WHERE item_kind = 'CLAIM'

                UNION

                SELECT claim_id
                FROM promotion_canonical_change
                WHERE promotion_batch_id = :batch_id
                  AND change_kind = 'CLAIM_OBSERVATION_ADDED'
            ), affected(node_id) AS (
                SELECT n.node_id
                FROM batch_items b
                JOIN node n ON n.node_id = b.knowledge_item_id
                WHERE b.item_kind = 'NODE'

                UNION

                SELECT r.source_node_id
                FROM batch_items b
                JOIN relation r ON r.relation_id = b.knowledge_item_id
                WHERE b.item_kind = 'RELATION'

                UNION

                SELECT r.target_node_id
                FROM batch_items b
                JOIN relation r ON r.relation_id = b.knowledge_item_id
                WHERE b.item_kind = 'RELATION'

                UNION

                SELECT a.node_id
                FROM promotion_canonical_change p
                JOIN node_alias a ON a.node_alias_id = p.node_alias_id
                WHERE p.promotion_batch_id = :batch_id
                  AND p.change_kind IN (
                    'NODE_ALIAS_CHANGED',
                    'NODE_ALIAS_EVIDENCE_ADDED'
                  )

                UNION

                SELECT r.source_node_id
                FROM promotion_canonical_change p
                JOIN relation r ON r.relation_id = p.relation_id
                WHERE p.promotion_batch_id = :batch_id
                  AND p.change_kind = 'CLAIM_RELATION_ADDED'

                UNION

                SELECT r.target_node_id
                FROM promotion_canonical_change p
                JOIN relation r ON r.relation_id = p.relation_id
                WHERE p.promotion_batch_id = :batch_id
                  AND p.change_kind = 'CLAIM_RELATION_ADDED'

                UNION

                SELECT v.target_node_id
                FROM promotion_canonical_change p
                JOIN claim_attribute_value v
                  ON v.claim_attribute_value_id = p.claim_attribute_value_id
                WHERE p.promotion_batch_id = :batch_id
                  AND p.change_kind = 'CLAIM_ATTRIBUTE_VALUE_ADDED'

                UNION

                SELECT p.event_node_id
                FROM promotion_canonical_change p
                WHERE p.promotion_batch_id = :batch_id
                  AND p.change_kind = 'EVENT_TEMPORAL_BASIS_ADDED'

                UNION

                SELECT r.source_node_id
                FROM projection_claims c
                JOIN claim_relation cr ON cr.claim_id = c.claim_id
                JOIN relation r ON r.relation_id = cr.relation_id

                UNION

                SELECT r.target_node_id
                FROM projection_claims c
                JOIN claim_relation cr ON cr.claim_id = c.claim_id
                JOIN relation r ON r.relation_id = cr.relation_id

                UNION

                SELECT v.target_node_id
                FROM projection_claims c
                JOIN claim_attribute_value v ON v.claim_id = c.claim_id

                UNION

                SELECT e.event_node_id
                FROM projection_claims c
                JOIN event_temporal_basis e ON e.claim_id = c.claim_id
            )
            SELECT a.node_id
            FROM affected a
            JOIN knowledge_item k ON k.knowledge_item_id = a.node_id
            WHERE a.node_id IS NOT NULL
              AND k.item_kind = 'NODE'
              AND k.lifecycle_kind = 'EVIDENCE_BACKED'
            ORDER BY a.node_id
            """
        ),
        {"batch_id": batch_id},
    ).scalars()
    return tuple(int(value) for value in values)


def _lock_batch(session: Session, batch_id: int) -> dict[str, Any]:
    row = (
        session.execute(
            sa.text(
                """
                SELECT promotion_batch_id, promotion_status, publication_status,
                       committed_at, ready_at, publication_failure_reason
                FROM promotion_batch
                WHERE promotion_batch_id = :batch_id
                FOR UPDATE
                """
            ),
            {"batch_id": batch_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise PublicationStateError("promotion batch does not exist")
    return dict(row)


def membership(session: Session, batch_id: int) -> tuple[int, ...]:
    """Return the frozen affected-node membership for one publication generation."""
    values = session.execute(
        sa.text(
            """
            SELECT node_id
            FROM publication_affected_node
            WHERE promotion_batch_id = :batch_id
            ORDER BY node_id
            """
        ),
        {"batch_id": batch_id},
    ).scalars()
    return tuple(int(value) for value in values)


def start_initial_publication(
    session: Session,
    batch_id: int,
) -> InitialPublicationStart:
    """Fix membership and enter PREPARING in one caller-owned transaction."""
    if not session.in_transaction():
        raise PublicationStateError("caller-owned transaction is required")

    batch = _lock_batch(session, batch_id)
    if batch["promotion_status"] != "COMMITTED":
        raise PublicationStateError("initial publication requires COMMITTED promotion")

    status = str(batch["publication_status"])
    if status in {"PREPARING", "READY"}:
        return InitialPublicationStart(
            promotion_batch_id=batch_id,
            affected_node_ids=membership(session, batch_id),
            started=False,
            publication_status=status,
        )

    if status != "NOT_STARTED":
        raise PublicationStateError(
            f"initial coordinator does not automatically re-enter {status}"
        )

    expected = affected_node_ids(session, batch_id)
    existing = membership(session, batch_id)
    if existing:
        raise PublicationStateError(
            "NOT_STARTED batch already has publication membership"
        )
    if expected:
        session.execute(
            s.publication_affected_node.insert(),
            [
                {"promotion_batch_id": batch_id, "node_id": node_id}
                for node_id in expected
            ],
        )
    updated = session.scalar(
        s.promotion_batch.update()
        .where(
            s.promotion_batch.c.promotion_batch_id == batch_id,
            s.promotion_batch.c.promotion_status == "COMMITTED",
            s.promotion_batch.c.publication_status == "NOT_STARTED",
        )
        .values(publication_status="PREPARING")
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    if updated is None:
        raise PublicationStateError("publication start lost its locked state")
    return InitialPublicationStart(
        promotion_batch_id=batch_id,
        affected_node_ids=expected,
        started=True,
        publication_status="PREPARING",
    )


def mark_initial_publication_failed(
    session: Session,
    batch_id: int,
    reason: str,
) -> bool:
    """Preserve canonical/previous READY data while recording publication failure."""
    if not session.in_transaction():
        raise PublicationStateError("caller-owned transaction is required")
    reason = reason.strip()
    if not reason:
        raise PublicationStateError("publication failure reason must be nonblank")

    batch = _lock_batch(session, batch_id)
    if batch["promotion_status"] != "COMMITTED":
        raise PublicationStateError("publication failure requires COMMITTED promotion")
    if batch["publication_status"] == "FAILED":
        if batch["publication_failure_reason"] != reason:
            raise PublicationStateError("FAILED publication already has another reason")
        return False
    if batch["publication_status"] != "PREPARING":
        raise PublicationStateError("only PREPARING publication can fail")

    # PREPARING already fixed publication_affected_node atomically. Failure
    # lifecycle transitions consume that frozen membership and never re-project
    # #216 provenance or mutate membership.
    updated = session.scalar(
        s.promotion_batch.update()
        .where(
            s.promotion_batch.c.promotion_batch_id == batch_id,
            s.promotion_batch.c.promotion_status == "COMMITTED",
            s.promotion_batch.c.publication_status == "PREPARING",
        )
        .values(
            publication_status="FAILED",
            publication_failure_reason=reason,
        )
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    if updated is None:
        raise PublicationStateError(
            "publication failure transition lost its locked state"
        )
    return True


def retry_failed_initial_publication(session: Session, batch_id: int) -> bool:
    """Explicitly re-enter PREPARING on the same immutable publication membership."""
    if not session.in_transaction():
        raise PublicationStateError("caller-owned transaction is required")

    batch = _lock_batch(session, batch_id)
    if batch["promotion_status"] != "COMMITTED":
        raise PublicationStateError("publication retry requires COMMITTED promotion")
    if batch["publication_status"] == "PREPARING":
        return False
    if batch["publication_status"] != "FAILED":
        raise PublicationStateError("publication retry requires FAILED state")

    # Retry is the same publication generation. Its authoritative membership is
    # publication_affected_node; provenance is intentionally not consulted again.
    updated = session.scalar(
        s.promotion_batch.update()
        .where(
            s.promotion_batch.c.promotion_batch_id == batch_id,
            s.promotion_batch.c.promotion_status == "COMMITTED",
            s.promotion_batch.c.publication_status == "FAILED",
        )
        .values(
            publication_status="PREPARING",
            publication_failure_reason=None,
        )
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    if updated is None:
        raise PublicationStateError("publication retry lost its locked state")
    return True


def assert_initial_publication_enable_safe(session: Session) -> None:
    """Delegate the #216 one-time cutover guard without inventing remediation."""
    promotion_provenance.assert_initial_publication_cutover_safe(session)


__all__ = [
    "affected_node_ids",
    "assert_initial_publication_enable_safe",
    "mark_initial_publication_failed",
    "membership",
    "retry_failed_initial_publication",
    "start_initial_publication",
]
