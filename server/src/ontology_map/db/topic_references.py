"""Explicit product Topic reference activation/read boundary for #203.

This module never runs at application startup. #201 may call the activation
function from an explicit transaction after #203 is merged.
"""

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.schema import knowledge_item, node, node_type, topic_reference
from ontology_map.db.topic_reference_schema import (
    APPROVED_TOPIC_DEFINITIONS,
    PRODUCT_REFERENCE,
)

_APPROVED_BY_CODE = dict(APPROVED_TOPIC_DEFINITIONS)
_APPROVED_BY_NAME = {name: code for code, name in APPROVED_TOPIC_DEFINITIONS}


@dataclass(frozen=True)
class TopicReferenceRow:
    node_id: int
    topic_code: str
    canonical_display_name: str
    is_active: bool


def _row(value: sa.RowMapping) -> TopicReferenceRow:
    return TopicReferenceRow(
        node_id=int(value["node_id"]),
        topic_code=str(value["topic_code"]),
        canonical_display_name=str(value["canonical_display_name"]),
        is_active=bool(value["is_active"]),
    )


def _projection() -> tuple[sa.ColumnElement[object], ...]:
    return (
        topic_reference.c.node_id,
        topic_reference.c.topic_code,
        topic_reference.c.canonical_display_name,
        topic_reference.c.is_active,
    )


def _validate_definition(topic_code: str, canonical_display_name: str) -> None:
    expected = _APPROVED_BY_CODE.get(topic_code)
    if expected is None or expected != canonical_display_name:
        raise ValueError(
            "Topic reference definition is not in the approved #203 contract"
        )


def list_topic_references(session: Session) -> list[TopicReferenceRow]:
    """Return every product Topic reference, including inactive definitions."""

    values = session.execute(
        sa.select(*_projection()).order_by(
            topic_reference.c.canonical_display_name.asc(),
            topic_reference.c.node_id.asc(),
        )
    ).mappings()
    return [_row(value) for value in values]


def get_topic_reference(session: Session, node_id: int) -> TopicReferenceRow | None:
    value = (
        session.execute(
            sa.select(*_projection()).where(topic_reference.c.node_id == node_id)
        )
        .mappings()
        .one_or_none()
    )
    return None if value is None else _row(value)


def find_topic_reference_by_name(
    session: Session,
    canonical_display_name: str,
    *,
    active_only: bool,
) -> TopicReferenceRow | None:
    topic_code = _APPROVED_BY_NAME.get(canonical_display_name)
    if topic_code is None:
        return None
    statement = sa.select(*_projection()).where(
        topic_reference.c.topic_code == topic_code,
        topic_reference.c.canonical_display_name == canonical_display_name,
    )
    if active_only:
        statement = statement.where(topic_reference.c.is_active)
    value = session.execute(statement).mappings().one_or_none()
    return None if value is None else _row(value)


def ensure_topic_reference(
    session: Session,
    *,
    topic_code: str,
    canonical_display_name: str,
    is_active: bool,
) -> TopicReferenceRow:
    """Create/reuse one approved reference Topic in the caller's transaction."""

    if not session.in_transaction():
        raise ValueError("Topic reference activation requires an explicit transaction")
    _validate_definition(topic_code, canonical_display_name)

    existing = (
        session.execute(
            sa.select(*_projection()).where(topic_reference.c.topic_code == topic_code)
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        current = _row(existing)
        if current.canonical_display_name != canonical_display_name:
            raise ValueError("existing Topic reference definition does not match")
        if current.is_active != is_active:
            session.execute(
                topic_reference.update()
                .where(topic_reference.c.node_id == current.node_id)
                .values(is_active=is_active)
            )
            return TopicReferenceRow(
                node_id=current.node_id,
                topic_code=current.topic_code,
                canonical_display_name=current.canonical_display_name,
                is_active=is_active,
            )
        return current

    topic_type_id = session.scalar(
        sa.select(node_type.c.node_type_id).where(
            node_type.c.node_type_code == "TOPIC",
            node_type.c.is_active,
        )
    )
    if topic_type_id is None:
        raise ValueError("active TOPIC node type is required for reference activation")

    node_id = int(
        session.execute(
            knowledge_item.insert()
            .values(
                item_kind="NODE",
                lifecycle_kind=PRODUCT_REFERENCE,
                current_state=None,
                promotion_batch_id=None,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    session.execute(
        node.insert().values(node_id=node_id, node_type_id=int(topic_type_id))
    )
    session.execute(
        topic_reference.insert().values(
            node_id=node_id,
            topic_code=topic_code,
            canonical_display_name=canonical_display_name,
            is_active=is_active,
        )
    )
    return TopicReferenceRow(
        node_id=node_id,
        topic_code=topic_code,
        canonical_display_name=canonical_display_name,
        is_active=is_active,
    )


def set_topic_reference_active(
    session: Session,
    node_id: int,
    *,
    is_active: bool,
) -> TopicReferenceRow:
    if not session.in_transaction():
        raise ValueError("Topic reference update requires an explicit transaction")
    current = get_topic_reference(session, node_id)
    if current is None:
        raise ValueError("Topic reference does not exist")
    session.execute(
        topic_reference.update()
        .where(topic_reference.c.node_id == node_id)
        .values(is_active=is_active)
    )
    return TopicReferenceRow(
        node_id=current.node_id,
        topic_code=current.topic_code,
        canonical_display_name=current.canonical_display_name,
        is_active=is_active,
    )
