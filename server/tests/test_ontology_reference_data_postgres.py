import os

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.fixture import _load_hbf_fixture
from ontology_map.db.ontology_reference_data import (
    ATTRIBUTE_DEFINITIONS,
    NODE_TYPE_DEFINITIONS,
    RELATION_DEFINITIONS,
    activate_approved_ontology_reference_data,
)
from ontology_map.db.schema import (
    attribute,
    attribute_revision,
    attribute_revision_allowed_unit,
    claim,
    knowledge_item,
    node,
    node_alias,
    node_search_document,
    node_type,
    observation,
    promotion_batch,
    relation,
    relation_endpoint_rule,
    relation_type,
    relation_type_revision,
    source_document,
    topic_reference,
)
from ontology_map.db.session import get_engine
from ontology_map.db.topic_reference_schema import (
    APPROVED_TOPIC_DEFINITIONS,
    PRODUCT_REFERENCE,
)

pytestmark = pytest.mark.skipif(
    os.getenv("ONTOLOGY_MAP_ONTOLOGY_REFERENCE_TEST") != "1",
    reason="#201 전용 실제 PostgreSQL 검증에서 실행한다.",
)


def _count(session: Session, table: sa.Table) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(table)) or 0)


def _force_constraints(session: Session) -> None:
    session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))


def _node_type_codes(session: Session) -> dict[int, str]:
    return {
        int(node_type_id): str(code)
        for node_type_id, code in session.execute(
            sa.select(node_type.c.node_type_id, node_type.c.node_type_code)
        )
    }


def _relation_contract(session: Session) -> dict[str, tuple[str, set[tuple[str, str]]]]:
    type_codes = _node_type_codes(session)
    rows = session.execute(
        sa.select(
            relation_type.c.relation_code,
            relation_type_revision.c.relation_type_revision_id,
            relation_type_revision.c.directionality,
        )
        .join(
            relation_type_revision,
            relation_type_revision.c.relation_type_id
            == relation_type.c.relation_type_id,
        )
        .where(relation_type_revision.c.is_active)
    )
    result: dict[str, tuple[str, set[tuple[str, str]]]] = {}
    for code, revision_id, directionality in rows:
        endpoint_rows = session.execute(
            sa.select(
                relation_endpoint_rule.c.source_node_type_id,
                relation_endpoint_rule.c.target_node_type_id,
            ).where(relation_endpoint_rule.c.relation_type_revision_id == revision_id)
        )
        pairs = {
            (type_codes[int(source_id)], type_codes[int(target_id)])
            for source_id, target_id in endpoint_rows
        }
        result[str(code)] = str(directionality), pairs
    return result


def _normalized_pairs(
    directionality: str, pairs: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    if directionality != "SYMMETRIC":
        return pairs
    return {tuple(sorted(pair)) for pair in pairs}


def _attribute_contract(
    session: Session,
) -> dict[str, tuple[str, str, set[str]]]:
    type_codes = _node_type_codes(session)
    rows = session.execute(
        sa.select(
            attribute.c.attribute_code,
            attribute_revision.c.attribute_revision_id,
            attribute_revision.c.target_node_type_id,
            attribute_revision.c.allowed_value_kind,
        )
        .join(
            attribute_revision,
            attribute_revision.c.attribute_id == attribute.c.attribute_id,
        )
        .where(attribute_revision.c.is_active)
    )
    result: dict[str, tuple[str, str, set[str]]] = {}
    for code, revision_id, target_id, value_kind in rows:
        units = {
            str(unit)
            for unit in session.scalars(
                sa.select(attribute_revision_allowed_unit.c.unit_code).where(
                    attribute_revision_allowed_unit.c.attribute_revision_id
                    == revision_id
                )
            )
        }
        result[str(code)] = (
            type_codes[int(target_id)],
            str(value_kind),
            units,
        )
    return result


_NODE_CREATION_RULE = "공개 원문 근거와 대표 alias가 필요하다."


def _activate(connection: sa.Connection):
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        with session.begin():
            result = activate_approved_ontology_reference_data(session)
            _force_constraints(session)
        return result


def _insert_node_types(
    connection: sa.Connection,
    *,
    only: set[str] | None = None,
    inactive: set[str] | None = None,
) -> dict[str, int]:
    inactive = inactive or set()
    result: dict[str, int] = {}
    for code, display_name in NODE_TYPE_DEFINITIONS:
        if only is not None and code not in only:
            continue
        result[code] = int(
            connection.execute(
                node_type.insert()
                .values(
                    node_type_code=code,
                    display_name=display_name,
                    creation_rule=_NODE_CREATION_RULE,
                    is_active=code not in inactive,
                )
                .returning(node_type.c.node_type_id)
            ).scalar_one()
        )
    return result


def _reference_snapshot(session: Session) -> dict[str, int]:
    return {
        "node_type": _count(session, node_type),
        "relation_type": _count(session, relation_type),
        "relation_revision": _count(session, relation_type_revision),
        "relation_endpoint": _count(session, relation_endpoint_rule),
        "attribute": _count(session, attribute),
        "attribute_revision": _count(session, attribute_revision),
        "attribute_unit": _count(session, attribute_revision_allowed_unit),
        "topic_reference": _count(session, topic_reference),
        "knowledge_item": _count(session, knowledge_item),
        "node": _count(session, node),
        "relation": _count(session, relation),
        "claim": _count(session, claim),
        "source_document": _count(session, source_document),
        "observation": _count(session, observation),
        "promotion_batch": _count(session, promotion_batch),
        "node_search_document": _count(session, node_search_document),
    }


def test_clean_activation_is_exact_and_idempotent() -> None:
    engine = get_engine()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            first = _activate(connection)
            second = _activate(connection)
            assert first == second
            assert len(first.relation_revision_ids) == 14
            assert len(first.attribute_revision_ids) == 7
            assert len(first.topic_node_ids) == 9

            with Session(bind=connection) as session:
                relation_contract = _relation_contract(session)
                assert set(relation_contract) == {
                    definition.code for definition in RELATION_DEFINITIONS
                }
                for definition in RELATION_DEFINITIONS:
                    actual_direction, actual_pairs = relation_contract[definition.code]
                    assert actual_direction == definition.directionality
                    assert _normalized_pairs(actual_direction, actual_pairs) == (
                        _normalized_pairs(
                            definition.directionality,
                            set(definition.endpoint_pairs),
                        )
                    )

                assert relation_contract["RELATED_TO"] == (
                    "SYMMETRIC",
                    {("TECHNOLOGY", "EVENT")},
                )
                assert relation_contract["HAS_TOPIC"] == (
                    "DIRECTED",
                    {
                        ("COMPANY", "TOPIC"),
                        ("PERSON", "TOPIC"),
                        ("TECHNOLOGY", "TOPIC"),
                        ("EVENT", "TOPIC"),
                    },
                )

                attribute_contract = _attribute_contract(session)
                assert set(attribute_contract) == {
                    definition.code for definition in ATTRIBUTE_DEFINITIONS
                }
                assert attribute_contract["CORE_COUNT"] == (
                    "TECHNOLOGY",
                    "NUMBER",
                    {"COUNT"},
                )
                assert attribute_contract["MAX_MEMORY_BANDWIDTH"] == (
                    "TECHNOLOGY",
                    "NUMBER",
                    {"GB_PER_S", "TB_PER_S"},
                )
                assert attribute_contract["RECURRENCE_SCHEDULE"] == (
                    "EVENT",
                    "STRING",
                    set(),
                )

                topic_rows = session.execute(
                    sa.select(
                        topic_reference.c.node_id,
                        topic_reference.c.topic_code,
                        topic_reference.c.canonical_display_name,
                        topic_reference.c.is_active,
                        knowledge_item.c.lifecycle_kind,
                        knowledge_item.c.current_state,
                        knowledge_item.c.promotion_batch_id,
                        node_type.c.node_type_code,
                    )
                    .join(node, node.c.node_id == topic_reference.c.node_id)
                    .join(node_type, node_type.c.node_type_id == node.c.node_type_id)
                    .join(
                        knowledge_item,
                        knowledge_item.c.knowledge_item_id == topic_reference.c.node_id,
                    )
                    .order_by(topic_reference.c.topic_code)
                ).all()
                assert {
                    (str(code), str(name))
                    for _node_id, code, name, *_rest in topic_rows
                } == set(APPROVED_TOPIC_DEFINITIONS)
                assert all(bool(row.is_active) for row in topic_rows)
                assert all(
                    row.lifecycle_kind == PRODUCT_REFERENCE for row in topic_rows
                )
                assert all(row.current_state is None for row in topic_rows)
                assert all(row.promotion_batch_id is None for row in topic_rows)
                assert all(row.node_type_code == "TOPIC" for row in topic_rows)
                assert _count(session, source_document) == 0
                assert _count(session, observation) == 0
                assert _count(session, promotion_batch) == 0
                assert _count(session, claim) == 0
                assert _count(session, relation) == 0
                assert _count(session, node_alias) == 0
                assert _count(session, node_search_document) == 0
        finally:
            outer.rollback()


@pytest.mark.parametrize("first", ["fixture", "activation"])
def test_fixture_and_activation_coexist_in_both_orders(first: str) -> None:
    engine = get_engine()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            if first == "fixture":
                created, fixture_node_ids = _load_hbf_fixture(connection)
                assert created
                activation = _activate(connection)
            else:
                activation = _activate(connection)
                created, fixture_node_ids = _load_hbf_fixture(connection)
                assert created

            with Session(bind=connection) as session:
                first_snapshot = _reference_snapshot(session)
                dev_revision_id = int(
                    session.scalar(
                        sa.select(relation_type_revision.c.relation_type_revision_id)
                        .join(
                            relation_type,
                            relation_type.c.relation_type_id
                            == relation_type_revision.c.relation_type_id,
                        )
                        .where(
                            relation_type.c.relation_code == "PUBLICLY_ASSOCIATED_WITH",
                            relation_type_revision.c.is_active,
                        )
                    )
                )

            activation_again = _activate(connection)
            created_again, fixture_node_ids_again = _load_hbf_fixture(connection)
            assert activation_again == activation
            assert not created_again
            assert fixture_node_ids_again == fixture_node_ids
            assert not set(fixture_node_ids.values()) & set(
                activation.topic_node_ids.values()
            )

            with Session(bind=connection) as session:
                assert _reference_snapshot(session) == first_snapshot
                assert _count(session, topic_reference) == 9
                assert (
                    session.scalar(
                        sa.select(relation_type_revision.c.relation_type_revision_id)
                        .join(
                            relation_type,
                            relation_type.c.relation_type_id
                            == relation_type_revision.c.relation_type_id,
                        )
                        .where(
                            relation_type.c.relation_code == "PUBLICLY_ASSOCIATED_WITH",
                            relation_type_revision.c.is_active,
                        )
                    )
                    == dev_revision_id
                )
        finally:
            outer.rollback()


def test_inactive_node_type_is_preserved_and_activation_fails_closed() -> None:
    engine = get_engine()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            ids = _insert_node_types(
                connection,
                only={"TOPIC"},
                inactive={"TOPIC"},
            )
            with pytest.raises(RuntimeError, match="node_type TOPIC is inactive"):
                _activate(connection)

            row = connection.execute(
                sa.select(node_type.c.node_type_id, node_type.c.is_active).where(
                    node_type.c.node_type_code == "TOPIC"
                )
            ).one()
            assert int(row.node_type_id) == ids["TOPIC"]
            assert not bool(row.is_active)
            assert (
                int(
                    connection.scalar(sa.select(sa.func.count()).select_from(node_type))
                    or 0
                )
                == 1
            )
            assert (
                int(
                    connection.scalar(
                        sa.select(sa.func.count()).select_from(relation_type)
                    )
                    or 0
                )
                == 0
            )
            assert (
                int(
                    connection.scalar(sa.select(sa.func.count()).select_from(attribute))
                    or 0
                )
                == 0
            )
            assert (
                int(
                    connection.scalar(
                        sa.select(sa.func.count()).select_from(topic_reference)
                    )
                    or 0
                )
                == 0
            )
        finally:
            outer.rollback()


def test_conflicting_active_relation_revision_fails_closed_and_rolls_back() -> None:
    engine = get_engine()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            _insert_node_types(connection)
            relation_type_id = int(
                connection.execute(
                    relation_type.insert()
                    .values(relation_code="DEVELOPS")
                    .returning(relation_type.c.relation_type_id)
                ).scalar_one()
            )
            conflict_revision_id = int(
                connection.execute(
                    relation_type_revision.insert()
                    .values(
                        relation_type_id=relation_type_id,
                        version_no=1,
                        display_name="conflict",
                        directionality="SYMMETRIC",
                        is_active=True,
                    )
                    .returning(relation_type_revision.c.relation_type_revision_id)
                ).scalar_one()
            )

            with pytest.raises(
                RuntimeError, match="active relation revision for DEVELOPS"
            ):
                _activate(connection)

            codes = set(connection.scalars(sa.select(relation_type.c.relation_code)))
            assert codes == {"DEVELOPS"}
            assert (
                connection.scalar(
                    sa.select(relation_type_revision.c.relation_type_revision_id).where(
                        relation_type_revision.c.is_active
                    )
                )
                == conflict_revision_id
            )
            assert (
                int(
                    connection.scalar(sa.select(sa.func.count()).select_from(attribute))
                    or 0
                )
                == 0
            )
            assert (
                int(
                    connection.scalar(
                        sa.select(sa.func.count()).select_from(topic_reference)
                    )
                    or 0
                )
                == 0
            )
        finally:
            outer.rollback()


def test_conflicting_active_attribute_revision_fails_closed_and_rolls_back() -> None:
    engine = get_engine()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            type_ids = _insert_node_types(connection)
            attribute_id = int(
                connection.execute(
                    attribute.insert()
                    .values(attribute_code="TECHNOLOGY_VERSION")
                    .returning(attribute.c.attribute_id)
                ).scalar_one()
            )
            conflict_revision_id = int(
                connection.execute(
                    attribute_revision.insert()
                    .values(
                        attribute_id=attribute_id,
                        version_no=1,
                        display_name="conflict",
                        target_node_type_id=type_ids["PERSON"],
                        allowed_value_kind="STRING",
                        is_active=True,
                    )
                    .returning(attribute_revision.c.attribute_revision_id)
                ).scalar_one()
            )

            with pytest.raises(
                RuntimeError,
                match="active attribute revision for TECHNOLOGY_VERSION",
            ):
                _activate(connection)

            assert set(connection.scalars(sa.select(attribute.c.attribute_code))) == {
                "TECHNOLOGY_VERSION"
            }
            assert (
                connection.scalar(
                    sa.select(attribute_revision.c.attribute_revision_id).where(
                        attribute_revision.c.is_active
                    )
                )
                == conflict_revision_id
            )
            assert (
                int(
                    connection.scalar(
                        sa.select(sa.func.count()).select_from(relation_type)
                    )
                    or 0
                )
                == 0
            )
            assert (
                int(
                    connection.scalar(
                        sa.select(sa.func.count()).select_from(topic_reference)
                    )
                    or 0
                )
                == 0
            )
        finally:
            outer.rollback()


def test_exact_inactive_revisions_are_reused_by_id() -> None:
    engine = get_engine()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            type_ids = _insert_node_types(connection)

            relation_type_id = int(
                connection.execute(
                    relation_type.insert()
                    .values(relation_code="AFFILIATED_WITH")
                    .returning(relation_type.c.relation_type_id)
                ).scalar_one()
            )
            relation_revision_id = int(
                connection.execute(
                    relation_type_revision.insert()
                    .values(
                        relation_type_id=relation_type_id,
                        version_no=7,
                        display_name="existing exact relation",
                        directionality="DIRECTED",
                        is_active=False,
                    )
                    .returning(relation_type_revision.c.relation_type_revision_id)
                ).scalar_one()
            )
            connection.execute(
                relation_endpoint_rule.insert().values(
                    relation_type_revision_id=relation_revision_id,
                    source_node_type_id=type_ids["PERSON"],
                    target_node_type_id=type_ids["COMPANY"],
                )
            )

            attribute_id = int(
                connection.execute(
                    attribute.insert()
                    .values(attribute_code="CORE_COUNT")
                    .returning(attribute.c.attribute_id)
                ).scalar_one()
            )
            attribute_revision_id = int(
                connection.execute(
                    attribute_revision.insert()
                    .values(
                        attribute_id=attribute_id,
                        version_no=3,
                        display_name="existing exact attribute",
                        target_node_type_id=type_ids["TECHNOLOGY"],
                        allowed_value_kind="NUMBER",
                        is_active=False,
                    )
                    .returning(attribute_revision.c.attribute_revision_id)
                ).scalar_one()
            )
            connection.execute(
                attribute_revision_allowed_unit.insert().values(
                    attribute_revision_id=attribute_revision_id,
                    allowed_value_kind="NUMBER",
                    unit_code="COUNT",
                )
            )

            result = _activate(connection)
            assert (
                result.relation_revision_ids["AFFILIATED_WITH"] == relation_revision_id
            )
            assert result.attribute_revision_ids["CORE_COUNT"] == attribute_revision_id
            assert bool(
                connection.scalar(
                    sa.select(relation_type_revision.c.is_active).where(
                        relation_type_revision.c.relation_type_revision_id
                        == relation_revision_id
                    )
                )
            )
            assert bool(
                connection.scalar(
                    sa.select(attribute_revision.c.is_active).where(
                        attribute_revision.c.attribute_revision_id
                        == attribute_revision_id
                    )
                )
            )
        finally:
            outer.rollback()
