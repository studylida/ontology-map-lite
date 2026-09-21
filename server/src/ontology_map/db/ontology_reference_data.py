"""Explicit idempotent product ontology reference-data activation for #201.

This module is never imported for application startup side effects. Product reference
rows are activated only when ``activate_approved_ontology_reference_data`` is called
inside a caller-owned transaction (or through this module's explicit ``main`` entry
point).
"""

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.schema import (
    attribute,
    attribute_revision,
    attribute_revision_allowed_unit,
    node_type,
    relation_endpoint_rule,
    relation_type,
    relation_type_revision,
)
from ontology_map.db.session import get_engine
from ontology_map.db.topic_reference_schema import APPROVED_TOPIC_DEFINITIONS
from ontology_map.db.topic_references import ensure_topic_reference

NODE_TYPE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("PERSON", "사람"),
    ("COMPANY", "회사"),
    ("TECHNOLOGY", "기술"),
    ("TOPIC", "주제"),
    ("EVENT", "사건"),
)

# node_type rows are shared prerequisites, not #201's product ontology payload.  The
# frozen schema already defines these five initial stable codes; #201 only makes a
# clean migrated database usable without depending on the development HBF fixture.
_NODE_CREATION_RULE = "공개 원문 근거와 대표 alias가 필요하다."


@dataclass(frozen=True)
class RelationDefinition:
    code: str
    directionality: str
    endpoint_pairs: tuple[tuple[str, str], ...]


RELATION_DEFINITIONS: tuple[RelationDefinition, ...] = (
    RelationDefinition("AFFILIATED_WITH", "DIRECTED", (("PERSON", "COMPANY"),)),
    RelationDefinition(
        "DEVELOPS",
        "DIRECTED",
        (("COMPANY", "TECHNOLOGY"), ("PERSON", "TECHNOLOGY")),
    ),
    RelationDefinition("COLLABORATES_WITH", "SYMMETRIC", (("COMPANY", "COMPANY"),)),
    RelationDefinition(
        "ANNOUNCES",
        "DIRECTED",
        (
            ("COMPANY", "TECHNOLOGY"),
            ("COMPANY", "EVENT"),
            ("PERSON", "TECHNOLOGY"),
            ("PERSON", "EVENT"),
        ),
    ),
    RelationDefinition(
        "INVESTS_IN",
        "DIRECTED",
        (("COMPANY", "COMPANY"), ("COMPANY", "TECHNOLOGY")),
    ),
    RelationDefinition("ADOPTS", "DIRECTED", (("COMPANY", "TECHNOLOGY"),)),
    RelationDefinition("TESTS", "DIRECTED", (("COMPANY", "TECHNOLOGY"),)),
    RelationDefinition("SUPPLIES", "DIRECTED", (("COMPANY", "TECHNOLOGY"),)),
    RelationDefinition("SUPPLIES_TO", "DIRECTED", (("COMPANY", "COMPANY"),)),
    RelationDefinition("INCLUDES", "DIRECTED", (("TECHNOLOGY", "TECHNOLOGY"),)),
    RelationDefinition(
        "PARTICIPATES_IN",
        "DIRECTED",
        (("COMPANY", "EVENT"), ("PERSON", "EVENT")),
    ),
    RelationDefinition(
        "MENTIONS",
        "DIRECTED",
        (
            ("COMPANY", "COMPANY"),
            ("COMPANY", "PERSON"),
            ("COMPANY", "TECHNOLOGY"),
            ("COMPANY", "EVENT"),
            ("PERSON", "COMPANY"),
            ("PERSON", "PERSON"),
            ("PERSON", "TECHNOLOGY"),
            ("PERSON", "EVENT"),
        ),
    ),
    RelationDefinition("RELATED_TO", "SYMMETRIC", (("EVENT", "TECHNOLOGY"),)),
    RelationDefinition(
        "HAS_TOPIC",
        "DIRECTED",
        (
            ("COMPANY", "TOPIC"),
            ("PERSON", "TOPIC"),
            ("TECHNOLOGY", "TOPIC"),
            ("EVENT", "TOPIC"),
        ),
    ),
)


@dataclass(frozen=True)
class AttributeDefinition:
    code: str
    target_node_type: str
    value_kind: str
    allowed_units: tuple[str, ...] = ()


ATTRIBUTE_DEFINITIONS: tuple[AttributeDefinition, ...] = (
    AttributeDefinition("ROLE_TITLE", "PERSON", "STRING"),
    AttributeDefinition("TECHNOLOGY_VERSION", "TECHNOLOGY", "STRING"),
    AttributeDefinition("COMMERCIALIZATION_STATUS", "TECHNOLOGY", "STRING"),
    AttributeDefinition("COMMERCIALIZATION_SCHEDULE", "TECHNOLOGY", "STRING"),
    AttributeDefinition("RECURRENCE_SCHEDULE", "EVENT", "STRING"),
    AttributeDefinition("CORE_COUNT", "TECHNOLOGY", "NUMBER", ("COUNT",)),
    AttributeDefinition(
        "MAX_MEMORY_BANDWIDTH",
        "TECHNOLOGY",
        "NUMBER",
        ("GB_PER_S", "TB_PER_S"),
    ),
)


@dataclass(frozen=True)
class ActivationResult:
    node_type_ids: dict[str, int]
    relation_revision_ids: dict[str, int]
    attribute_revision_ids: dict[str, int]
    topic_node_ids: dict[str, int]


def _require_transaction(session: Session) -> None:
    if not session.in_transaction():
        raise ValueError(
            "ontology reference activation requires an explicit transaction"
        )


def _ensure_node_types(session: Session) -> dict[str, int]:
    result: dict[str, int] = {}
    for code, display_name in NODE_TYPE_DEFINITIONS:
        row = (
            session.execute(
                sa.select(
                    node_type.c.node_type_id,
                    node_type.c.display_name,
                    node_type.c.creation_rule,
                    node_type.c.is_active,
                ).where(node_type.c.node_type_code == code)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            node_type_id = int(
                session.execute(
                    node_type.insert()
                    .values(
                        node_type_code=code,
                        display_name=display_name,
                        creation_rule=_NODE_CREATION_RULE,
                        is_active=True,
                    )
                    .returning(node_type.c.node_type_id)
                ).scalar_one()
            )
        else:
            if (
                str(row["display_name"]) != display_name
                or str(row["creation_rule"]) != _NODE_CREATION_RULE
            ):
                raise RuntimeError(
                    f"node_type {code} conflicts with the frozen prerequisite contract"
                )
            if not bool(row["is_active"]):
                raise RuntimeError(
                    f"node_type {code} is inactive; #201 activation "
                    "will not reactivate it"
                )
            node_type_id = int(row["node_type_id"])
        result[code] = node_type_id
    return result


def _canonical_endpoint_pair(
    source_id: int, target_id: int, directionality: str
) -> tuple[int, int]:
    if directionality == "SYMMETRIC":
        return min(source_id, target_id), max(source_id, target_id)
    return source_id, target_id


def _revision_endpoint_pairs(
    session: Session, revision_id: int, directionality: str
) -> set[tuple[int, int]]:
    rows = session.execute(
        sa.select(
            relation_endpoint_rule.c.source_node_type_id,
            relation_endpoint_rule.c.target_node_type_id,
        ).where(relation_endpoint_rule.c.relation_type_revision_id == revision_id)
    )
    return {
        _canonical_endpoint_pair(int(source_id), int(target_id), directionality)
        for source_id, target_id in rows
    }


def _expected_endpoint_pairs(
    definition: RelationDefinition, node_type_ids: dict[str, int]
) -> set[tuple[int, int]]:
    return {
        _canonical_endpoint_pair(
            node_type_ids[source_code],
            node_type_ids[target_code],
            definition.directionality,
        )
        for source_code, target_code in definition.endpoint_pairs
    }


def _relation_revision_matches(
    session: Session,
    row: sa.RowMapping,
    definition: RelationDefinition,
    expected_pairs: set[tuple[int, int]],
) -> bool:
    revision_id = int(row["relation_type_revision_id"])
    return (
        str(row["directionality"]) == definition.directionality
        and row["inverse_relation_type_revision_id"] is None
        and _revision_endpoint_pairs(session, revision_id, definition.directionality)
        == expected_pairs
    )


def _ensure_relation(
    session: Session,
    definition: RelationDefinition,
    node_type_ids: dict[str, int],
) -> int:
    relation_type_id = session.scalar(
        sa.select(relation_type.c.relation_type_id).where(
            relation_type.c.relation_code == definition.code
        )
    )
    if relation_type_id is None:
        relation_type_id = session.execute(
            relation_type.insert()
            .values(relation_code=definition.code)
            .returning(relation_type.c.relation_type_id)
        ).scalar_one()
    relation_type_id = int(relation_type_id)

    revisions = (
        session.execute(
            sa.select(
                relation_type_revision.c.relation_type_revision_id,
                relation_type_revision.c.version_no,
                relation_type_revision.c.directionality,
                relation_type_revision.c.inverse_relation_type_revision_id,
                relation_type_revision.c.is_active,
            )
            .where(relation_type_revision.c.relation_type_id == relation_type_id)
            .order_by(relation_type_revision.c.version_no)
        )
        .mappings()
        .all()
    )
    expected_pairs = _expected_endpoint_pairs(definition, node_type_ids)
    active = [row for row in revisions if bool(row["is_active"])]
    if active:
        if len(active) != 1 or not _relation_revision_matches(
            session, active[0], definition, expected_pairs
        ):
            raise RuntimeError(
                f"active relation revision for {definition.code} conflicts with #126"
            )
        return int(active[0]["relation_type_revision_id"])

    exact = [
        row
        for row in revisions
        if _relation_revision_matches(session, row, definition, expected_pairs)
    ]
    if exact:
        selected = exact[-1]
        revision_id = int(selected["relation_type_revision_id"])
        session.execute(
            relation_type_revision.update()
            .where(relation_type_revision.c.relation_type_revision_id == revision_id)
            .values(is_active=True)
        )
        return revision_id

    version_no = max((int(row["version_no"]) for row in revisions), default=0) + 1
    revision_id = int(
        session.execute(
            relation_type_revision.insert()
            .values(
                relation_type_id=relation_type_id,
                version_no=version_no,
                display_name=definition.code,
                directionality=definition.directionality,
                inverse_relation_type_revision_id=None,
                is_active=True,
            )
            .returning(relation_type_revision.c.relation_type_revision_id)
        ).scalar_one()
    )
    session.execute(
        relation_endpoint_rule.insert(),
        [
            {
                "relation_type_revision_id": revision_id,
                "source_node_type_id": source_id,
                "target_node_type_id": target_id,
            }
            for source_id, target_id in sorted(expected_pairs)
        ],
    )
    return revision_id


def _attribute_units(session: Session, revision_id: int) -> set[str]:
    return {
        str(value)
        for value in session.scalars(
            sa.select(attribute_revision_allowed_unit.c.unit_code).where(
                attribute_revision_allowed_unit.c.attribute_revision_id == revision_id
            )
        )
    }


def _attribute_revision_matches(
    session: Session,
    row: sa.RowMapping,
    definition: AttributeDefinition,
    target_node_type_id: int,
) -> bool:
    return (
        int(row["target_node_type_id"]) == target_node_type_id
        and str(row["allowed_value_kind"]) == definition.value_kind
        and _attribute_units(session, int(row["attribute_revision_id"]))
        == set(definition.allowed_units)
    )


def _ensure_attribute(
    session: Session,
    definition: AttributeDefinition,
    node_type_ids: dict[str, int],
) -> int:
    attribute_id = session.scalar(
        sa.select(attribute.c.attribute_id).where(
            attribute.c.attribute_code == definition.code
        )
    )
    if attribute_id is None:
        attribute_id = session.execute(
            attribute.insert()
            .values(attribute_code=definition.code)
            .returning(attribute.c.attribute_id)
        ).scalar_one()
    attribute_id = int(attribute_id)
    target_id = node_type_ids[definition.target_node_type]

    revisions = (
        session.execute(
            sa.select(
                attribute_revision.c.attribute_revision_id,
                attribute_revision.c.version_no,
                attribute_revision.c.target_node_type_id,
                attribute_revision.c.allowed_value_kind,
                attribute_revision.c.is_active,
            )
            .where(attribute_revision.c.attribute_id == attribute_id)
            .order_by(attribute_revision.c.version_no)
        )
        .mappings()
        .all()
    )
    active = [row for row in revisions if bool(row["is_active"])]
    if active:
        if len(active) != 1 or not _attribute_revision_matches(
            session, active[0], definition, target_id
        ):
            raise RuntimeError(
                f"active attribute revision for {definition.code} conflicts with #126"
            )
        return int(active[0]["attribute_revision_id"])

    exact = [
        row
        for row in revisions
        if _attribute_revision_matches(session, row, definition, target_id)
    ]
    if exact:
        selected = exact[-1]
        revision_id = int(selected["attribute_revision_id"])
        session.execute(
            attribute_revision.update()
            .where(attribute_revision.c.attribute_revision_id == revision_id)
            .values(is_active=True)
        )
        return revision_id

    version_no = max((int(row["version_no"]) for row in revisions), default=0) + 1
    revision_id = int(
        session.execute(
            attribute_revision.insert()
            .values(
                attribute_id=attribute_id,
                version_no=version_no,
                display_name=definition.code,
                target_node_type_id=target_id,
                allowed_value_kind=definition.value_kind,
                is_active=True,
            )
            .returning(attribute_revision.c.attribute_revision_id)
        ).scalar_one()
    )
    if definition.allowed_units:
        session.execute(
            attribute_revision_allowed_unit.insert(),
            [
                {
                    "attribute_revision_id": revision_id,
                    "allowed_value_kind": "NUMBER",
                    "unit_code": unit,
                }
                for unit in definition.allowed_units
            ],
        )
    return revision_id


def activate_approved_ontology_reference_data(session: Session) -> ActivationResult:
    """Activate exactly the #126/#203 reference contract in one transaction."""

    _require_transaction(session)
    node_type_ids = _ensure_node_types(session)
    relation_revision_ids = {
        definition.code: _ensure_relation(session, definition, node_type_ids)
        for definition in RELATION_DEFINITIONS
    }
    attribute_revision_ids = {
        definition.code: _ensure_attribute(session, definition, node_type_ids)
        for definition in ATTRIBUTE_DEFINITIONS
    }
    topic_node_ids = {
        code: ensure_topic_reference(
            session,
            topic_code=code,
            canonical_display_name=display_name,
            is_active=True,
        ).node_id
        for code, display_name in APPROVED_TOPIC_DEFINITIONS
    }
    return ActivationResult(
        node_type_ids=node_type_ids,
        relation_revision_ids=relation_revision_ids,
        attribute_revision_ids=attribute_revision_ids,
        topic_node_ids=topic_node_ids,
    )


def main() -> None:
    with Session(get_engine()) as session:
        with session.begin():
            result = activate_approved_ontology_reference_data(session)
    print(
        "승인 ontology reference data 활성화 완료: "
        f"relation={len(result.relation_revision_ids)}, "
        f"attribute={len(result.attribute_revision_ids)}, "
        f"topic={len(result.topic_node_ids)}"
    )


if __name__ == "__main__":
    main()
