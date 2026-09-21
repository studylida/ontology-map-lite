"""Shared publication grounding helpers for approved product references.

Reference Topics participate in evidence-backed HAS_TOPIC meaning as validated
relation endpoints, but they are not evidence-backed knowledge and therefore
must never be added to search_document_basis or ordinary publication membership.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.topic_reference_schema import (
    APPROVED_TOPIC_DEFINITIONS,
    PRODUCT_REFERENCE,
)

_APPROVED_TOPIC_NAMES = dict(APPROVED_TOPIC_DEFINITIONS)


class ReferenceTopicIntegrityError(ValueError):
    """Raised when a PRODUCT_REFERENCE Topic endpoint violates #203 lifecycle."""


@dataclass(frozen=True)
class ReferenceTopicIdentity:
    node_id: int
    canonical_display_name: str


def reference_topic_identities(
    session: Session,
    node_ids: Iterable[int],
) -> dict[int, ReferenceTopicIdentity]:
    """Return validated Reference Topic identities among ``node_ids``.

    ``is_active`` intentionally does not gate this read. It controls creation of
    new memberships, while an existing evidence-backed HAS_TOPIC relation keeps
    its historical meaning after the reference is deactivated.
    """
    ids = sorted(set(node_ids))
    if not ids:
        return {}

    rows = (
        session.execute(
            sa.text(
                """
                SELECT n.node_id, nt.node_type_code,
                       k.item_kind, k.lifecycle_kind, k.current_state,
                       k.promotion_batch_id,
                       tr.topic_code, tr.canonical_display_name, tr.is_active
                FROM node n
                JOIN node_type nt ON nt.node_type_id = n.node_type_id
                JOIN knowledge_item k ON k.knowledge_item_id = n.node_id
                LEFT JOIN topic_reference tr ON tr.node_id = n.node_id
                WHERE n.node_id IN :ids
                  AND (
                    k.lifecycle_kind = :product_reference
                    OR tr.node_id IS NOT NULL
                  )
                ORDER BY n.node_id
                """
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": ids, "product_reference": PRODUCT_REFERENCE},
        )
        .mappings()
        .all()
    )

    result: dict[int, ReferenceTopicIdentity] = {}
    for row in rows:
        topic_code = None if row["topic_code"] is None else str(row["topic_code"])
        canonical_name = (
            None
            if row["canonical_display_name"] is None
            else str(row["canonical_display_name"])
        )
        valid = (
            row["node_type_code"] == "TOPIC"
            and row["item_kind"] == "NODE"
            and row["lifecycle_kind"] == PRODUCT_REFERENCE
            and row["current_state"] is None
            and row["promotion_batch_id"] is None
            and topic_code in _APPROVED_TOPIC_NAMES
            and canonical_name == _APPROVED_TOPIC_NAMES.get(topic_code)
        )
        if not valid:
            raise ReferenceTopicIntegrityError(
                f"node {int(row['node_id'])} violates PRODUCT_REFERENCE Topic integrity"
            )
        assert canonical_name is not None
        node_id = int(row["node_id"])
        result[node_id] = ReferenceTopicIdentity(
            node_id=node_id,
            canonical_display_name=canonical_name,
        )
    return result


__all__ = [
    "ReferenceTopicIdentity",
    "ReferenceTopicIntegrityError",
    "reference_topic_identities",
]
