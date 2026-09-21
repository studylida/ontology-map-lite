from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from ontology_map.db.schema import (
    attribute,
    attribute_revision,
    attribute_revision_allowed_unit,
    claim,
    claim_attribute_value,
    knowledge_item,
    lint_policy_version,
    node,
    node_type,
    promotion_batch,
)
from ontology_map.db.session import get_engine


def _number_value(
    *,
    claim_id: int,
    target_node_id: int,
    attribute_revision_id: int,
    number_value: Decimal,
    unit_code: str,
) -> dict[str, object]:
    return {
        "claim_id": claim_id,
        "target_node_id": target_node_id,
        "attribute_revision_id": attribute_revision_id,
        "value_kind": "NUMBER",
        "number_value": number_value,
        "unit_code": unit_code,
        "date_from_precision": "UNKNOWN",
        "date_to_precision": "UNKNOWN",
    }


def _insert_claim_target(connection: sa.Connection) -> tuple[int, int, int]:
    node_type_id = int(
        connection.execute(
            node_type.insert()
            .values(
                node_type_code="ISSUE_200_TECHNOLOGY",
                display_name="Issue 200 기술",
                creation_rule="Issue #200 PostgreSQL 회귀 테스트 전용 유형이다.",
                is_active=True,
            )
            .returning(node_type.c.node_type_id)
        ).scalar_one()
    )
    lint_policy_version_id = int(
        connection.execute(
            lint_policy_version.insert()
            .values(
                version_no=200001,
                validator_version="issue-200-test",
                is_active=False,
            )
            .returning(lint_policy_version.c.lint_policy_version_id)
        ).scalar_one()
    )
    promotion_batch_id = int(
        connection.execute(
            promotion_batch.insert()
            .values(lint_policy_version_id=lint_policy_version_id)
            .returning(promotion_batch.c.promotion_batch_id)
        ).scalar_one()
    )
    target_node_id = int(
        connection.execute(
            knowledge_item.insert()
            .values(
                item_kind="NODE",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=promotion_batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    connection.execute(
        node.insert().values(node_id=target_node_id, node_type_id=node_type_id)
    )
    claim_id = int(
        connection.execute(
            knowledge_item.insert()
            .values(
                item_kind="CLAIM",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=promotion_batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    connection.execute(
        claim.insert().values(
            claim_id=claim_id,
            statement_text="Issue #200 NUMBER 단위 회귀 테스트 Claim",
            language="ko",
            modality="FACT",
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    return node_type_id, target_node_id, claim_id


def _insert_number_revision(
    connection: sa.Connection,
    *,
    code: str,
    display_name: str,
    node_type_id: int,
    units: tuple[str, ...],
) -> tuple[int, int]:
    attribute_id = int(
        connection.execute(
            attribute.insert()
            .values(attribute_code=code)
            .returning(attribute.c.attribute_id)
        ).scalar_one()
    )
    revision_id = int(
        connection.execute(
            attribute_revision.insert()
            .values(
                attribute_id=attribute_id,
                version_no=1,
                display_name=display_name,
                target_node_type_id=node_type_id,
                allowed_value_kind="NUMBER",
                is_active=True,
            )
            .returning(attribute_revision.c.attribute_revision_id)
        ).scalar_one()
    )
    connection.execute(
        attribute_revision_allowed_unit.insert(),
        [
            {
                "attribute_revision_id": revision_id,
                "allowed_value_kind": "NUMBER",
                "unit_code": unit,
            }
            for unit in units
        ],
    )
    return attribute_id, revision_id


def test_number_attribute_supports_multiple_allowed_units() -> None:
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            node_type_id, target_node_id, claim_id = _insert_claim_target(connection)
            attribute_id, revision_id = _insert_number_revision(
                connection,
                code="MAX_MEMORY_BANDWIDTH",
                display_name="최대 메모리 대역폭",
                node_type_id=node_type_id,
                units=("GB_PER_S", "TB_PER_S"),
            )

            inserted_ids = list(
                connection.execute(
                    claim_attribute_value.insert().returning(
                        claim_attribute_value.c.claim_attribute_value_id
                    ),
                    [
                        _number_value(
                            claim_id=claim_id,
                            target_node_id=target_node_id,
                            attribute_revision_id=revision_id,
                            number_value=Decimal("1024.50"),
                            unit_code="GB_PER_S",
                        ),
                        _number_value(
                            claim_id=claim_id,
                            target_node_id=target_node_id,
                            attribute_revision_id=revision_id,
                            number_value=Decimal("1.25"),
                            unit_code="TB_PER_S",
                        ),
                    ],
                ).scalars()
            )
            stored = connection.execute(
                sa.select(
                    claim_attribute_value.c.number_value,
                    claim_attribute_value.c.unit_code,
                )
                .where(
                    claim_attribute_value.c.claim_attribute_value_id.in_(inserted_ids)
                )
                .order_by(claim_attribute_value.c.claim_attribute_value_id)
            ).all()
            assert stored == [
                (Decimal("1024.50"), "GB_PER_S"),
                (Decimal("1.25"), "TB_PER_S"),
            ]

            allowed_units = set(
                connection.execute(
                    sa.select(attribute_revision_allowed_unit.c.unit_code).where(
                        attribute_revision_allowed_unit.c.attribute_revision_id
                        == revision_id
                    )
                ).scalars()
            )
            assert allowed_units == {"GB_PER_S", "TB_PER_S"}

            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    claim_attribute_value.insert().values(
                        **_number_value(
                            claim_id=claim_id,
                            target_node_id=target_node_id,
                            attribute_revision_id=revision_id,
                            number_value=Decimal("1000"),
                            unit_code="MB_PER_S",
                        )
                    )
                )

            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    attribute_revision.insert().values(
                        attribute_id=attribute_id,
                        version_no=2,
                        display_name="최대 메모리 대역폭 v2",
                        target_node_type_id=node_type_id,
                        allowed_value_kind="NUMBER",
                        is_active=True,
                    )
                )
        finally:
            transaction.rollback()


def test_existing_single_unit_number_attribute_remains_valid() -> None:
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            node_type_id, target_node_id, claim_id = _insert_claim_target(connection)
            _attribute_id, revision_id = _insert_number_revision(
                connection,
                code="ISSUE_200_SINGLE_UNIT",
                display_name="Issue 200 단일 단위",
                node_type_id=node_type_id,
                units=("COUNT",),
            )

            value_id = int(
                connection.execute(
                    claim_attribute_value.insert()
                    .values(
                        **_number_value(
                            claim_id=claim_id,
                            target_node_id=target_node_id,
                            attribute_revision_id=revision_id,
                            number_value=Decimal("64"),
                            unit_code="COUNT",
                        )
                    )
                    .returning(claim_attribute_value.c.claim_attribute_value_id)
                ).scalar_one()
            )
            stored = connection.execute(
                sa.select(
                    claim_attribute_value.c.number_value,
                    claim_attribute_value.c.unit_code,
                ).where(claim_attribute_value.c.claim_attribute_value_id == value_id)
            ).one()
            assert stored == (Decimal("64"), "COUNT")

            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    claim_attribute_value.insert().values(
                        **_number_value(
                            claim_id=claim_id,
                            target_node_id=target_node_id,
                            attribute_revision_id=revision_id,
                            number_value=Decimal("64"),
                            unit_code="RATIO",
                        )
                    )
                )
        finally:
            transaction.rollback()
