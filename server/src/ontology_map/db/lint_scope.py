"""Lifecycle-scoped lint boundaries for persisted knowledge.

Reference Topics are controlled product reference objects, not evidence-backed
knowledge. Evidence Trace lint therefore keys off ``knowledge_item.lifecycle_kind``
only; node type must never be used as a blanket lint exemption.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.schema import knowledge_item
from ontology_map.db.topic_reference_schema import EVIDENCE_BACKED, PRODUCT_REFERENCE


def requires_evidence_trace_lint(session: Session, knowledge_item_id: int) -> bool:
    """Return whether the persisted item participates in Evidence Trace lint.

    Existing evidence-backed Nodes, including legacy/evidence-backed TOPIC Nodes,
    remain lint targets. Only the explicit product-reference lifecycle is exempt.
    Unknown lifecycle values fail closed even though the DB CHECK should reject them.
    """

    lifecycle_kind = session.scalar(
        sa.select(knowledge_item.c.lifecycle_kind).where(
            knowledge_item.c.knowledge_item_id == knowledge_item_id
        )
    )
    if lifecycle_kind is None:
        raise ValueError("knowledge item does not exist")
    if lifecycle_kind == EVIDENCE_BACKED:
        return True
    if lifecycle_kind == PRODUCT_REFERENCE:
        return False
    raise ValueError("unknown knowledge lifecycle")
