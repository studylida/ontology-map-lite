import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

REHEARSAL_FLAG = "ONTOLOGY_MAP_MIGRATION_REHEARSAL"
SERVER_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = SERVER_DIR / "alembic.ini"

pytestmark = pytest.mark.skipif(
    os.getenv(REHEARSAL_FLAG) != "1",
    reason="#200 migration rehearsal 전용 격리 PostgreSQL에서만 실행한다.",
)


def _database_url() -> str:
    database_url = os.environ["ONTOLOGY_MAP_DATABASE_URL"]
    database_name = sa.engine.make_url(database_url).database or ""
    if "migration_rehearsal" not in database_name:
        raise RuntimeError(
            "migration rehearsal은 이름에 migration_rehearsal이 포함된 격리 DB에서만 "
            "실행할 수 있습니다."
        )
    return database_url


def _engine() -> Engine:
    return sa.create_engine(_database_url(), poolclass=NullPool)


def _alembic_config() -> Config:
    return Config(str(ALEMBIC_CONFIG))


def _upgrade(revision: str) -> None:
    command.upgrade(_alembic_config(), revision)


def _downgrade(revision: str) -> None:
    command.downgrade(_alembic_config(), revision)


def _reset_public_schema() -> None:
    engine = _engine()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_rehearsal_database() -> Iterator[None]:
    _reset_public_schema()
    yield
    _reset_public_schema()


def _version(connection: Connection) -> str:
    return str(
        connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    )


def _table_exists(connection: Connection, table_name: str) -> bool:
    return (
        connection.execute(
            sa.text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": f"public.{table_name}"},
        ).scalar_one()
        is not None
    )


def _column_exists(
    connection: Connection,
    *,
    table_name: str,
    column_name: str,
) -> bool:
    return bool(
        connection.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                      AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar_one()
    )


def _constraint_exists(connection: Connection, constraint_name: str) -> bool:
    return bool(
        connection.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = :constraint_name
                      AND connamespace = 'public'::regnamespace
                )
                """
            ),
            {"constraint_name": constraint_name},
        ).scalar_one()
    )


def _reflect_tables(
    connection: Connection,
    *names: str,
) -> dict[str, sa.Table]:
    metadata = sa.MetaData()
    return {name: sa.Table(name, metadata, autoload_with=connection) for name in names}


def _insert_claim_scaffold(connection: Connection) -> tuple[int, int, int]:
    tables = _reflect_tables(
        connection,
        "node_type",
        "lint_policy_version",
        "promotion_batch",
        "knowledge_item",
        "node",
        "claim",
    )
    node_type = tables["node_type"]
    lint_policy_version = tables["lint_policy_version"]
    promotion_batch = tables["promotion_batch"]
    knowledge_item = tables["knowledge_item"]
    node = tables["node"]
    claim = tables["claim"]

    node_type_id = int(
        connection.execute(
            node_type.insert()
            .values(
                node_type_code="ISSUE_200_MIGRATION_TECHNOLOGY",
                display_name="Issue 200 migration 기술",
                creation_rule="Issue #200 migration rehearsal 전용 유형이다.",
                is_active=True,
            )
            .returning(node_type.c.node_type_id)
        ).scalar_one()
    )
    lint_policy_version_id = int(
        connection.execute(
            lint_policy_version.insert()
            .values(
                version_no=200003,
                validator_version="issue-200-migration-rehearsal",
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
        node.insert().values(
            node_id=target_node_id,
            node_type_id=node_type_id,
        )
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
            statement_text="Issue #200 migration rehearsal Claim",
            language="ko",
            modality="FACT",
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    return node_type_id, target_node_id, claim_id


def _insert_number_value(
    connection: Connection,
    *,
    unit_code: str,
    number_value: Decimal,
    unit_rule: str | None = None,
    allowed_units: tuple[str, ...] = (),
) -> tuple[int, int]:
    node_type_id, target_node_id, claim_id = _insert_claim_scaffold(connection)
    tables = _reflect_tables(
        connection,
        "attribute",
        "attribute_revision",
        "claim_attribute_value",
    )
    attribute = tables["attribute"]
    attribute_revision = tables["attribute_revision"]
    claim_attribute_value = tables["claim_attribute_value"]

    attribute_id = int(
        connection.execute(
            attribute.insert()
            .values(attribute_code="ISSUE_200_MIGRATION_NUMBER")
            .returning(attribute.c.attribute_id)
        ).scalar_one()
    )
    revision_values: dict[str, object] = {
        "attribute_id": attribute_id,
        "version_no": 1,
        "display_name": "Issue 200 migration NUMBER",
        "target_node_type_id": node_type_id,
        "allowed_value_kind": "NUMBER",
        "is_active": True,
    }
    if unit_rule is not None:
        revision_values["unit_rule"] = unit_rule

    revision_id = int(
        connection.execute(
            attribute_revision.insert()
            .values(**revision_values)
            .returning(attribute_revision.c.attribute_revision_id)
        ).scalar_one()
    )
    if allowed_units:
        allowed_unit = _reflect_tables(
            connection,
            "attribute_revision_allowed_unit",
        )["attribute_revision_allowed_unit"]
        connection.execute(
            allowed_unit.insert(),
            [
                {
                    "attribute_revision_id": revision_id,
                    "allowed_value_kind": "NUMBER",
                    "unit_code": allowed_unit_code,
                }
                for allowed_unit_code in allowed_units
            ],
        )

    value_id = int(
        connection.execute(
            claim_attribute_value.insert()
            .values(
                claim_id=claim_id,
                target_node_id=target_node_id,
                attribute_revision_id=revision_id,
                value_kind="NUMBER",
                number_value=number_value,
                unit_code=unit_code,
                date_from_precision="UNKNOWN",
                date_to_precision="UNKNOWN",
            )
            .returning(claim_attribute_value.c.claim_attribute_value_id)
        ).scalar_one()
    )
    return revision_id, value_id


def _stored_number_value(
    connection: Connection,
    value_id: int,
) -> tuple[Decimal, str]:
    row = connection.execute(
        sa.text(
            """
            SELECT number_value, unit_code
            FROM claim_attribute_value
            WHERE claim_attribute_value_id = :value_id
            """
        ),
        {"value_id": value_id},
    ).one()
    return Decimal(row.number_value), str(row.unit_code)


def _allowed_units(connection: Connection, revision_id: int) -> list[str]:
    return list(
        connection.execute(
            sa.text(
                """
                SELECT unit_code
                FROM attribute_revision_allowed_unit
                WHERE attribute_revision_id = :revision_id
                ORDER BY unit_code
                """
            ),
            {"revision_id": revision_id},
        ).scalars()
    )


def test_single_unit_legacy_data_round_trips_through_0003() -> None:
    _upgrade("0002")
    engine = _engine()
    try:
        with engine.begin() as connection:
            revision_id, value_id = _insert_number_value(
                connection,
                unit_rule="COUNT",
                unit_code="COUNT",
                number_value=Decimal("64.25"),
            )

        _upgrade("0003")
        with engine.connect() as connection:
            assert _version(connection) == "0003"
            assert _table_exists(connection, "attribute_revision_allowed_unit")
            assert not _column_exists(
                connection,
                table_name="attribute_revision",
                column_name="unit_rule",
            )
            assert _constraint_exists(
                connection,
                "fk_claim_attribute_value__allowed_unit",
            )
            assert _allowed_units(connection, revision_id) == ["COUNT"]
            assert _stored_number_value(connection, value_id) == (
                Decimal("64.25"),
                "COUNT",
            )
            referenced_unit = connection.execute(
                sa.text(
                    """
                    SELECT allowed_unit.unit_code
                    FROM claim_attribute_value AS cav
                    JOIN attribute_revision_allowed_unit AS allowed_unit
                      ON allowed_unit.attribute_revision_id = cav.attribute_revision_id
                     AND allowed_unit.unit_code = cav.unit_code
                    WHERE cav.claim_attribute_value_id = :value_id
                    """
                ),
                {"value_id": value_id},
            ).scalar_one()
            assert referenced_unit == "COUNT"

        _downgrade("0002")
        with engine.connect() as connection:
            assert _version(connection) == "0002"
            assert not _table_exists(connection, "attribute_revision_allowed_unit")
            assert _column_exists(
                connection,
                table_name="attribute_revision",
                column_name="unit_rule",
            )
            unit_rule = connection.execute(
                sa.text(
                    """
                    SELECT unit_rule
                    FROM attribute_revision
                    WHERE attribute_revision_id = :revision_id
                    """
                ),
                {"revision_id": revision_id},
            ).scalar_one()
            assert unit_rule == "COUNT"
            assert _stored_number_value(connection, value_id) == (
                Decimal("64.25"),
                "COUNT",
            )

        _upgrade("0003")
        with engine.connect() as connection:
            assert _version(connection) == "0003"
            assert _allowed_units(connection, revision_id) == ["COUNT"]
            assert _stored_number_value(connection, value_id) == (
                Decimal("64.25"),
                "COUNT",
            )
    finally:
        engine.dispose()


def test_legacy_unit_mismatch_upgrade_fails_without_partial_state() -> None:
    _upgrade("0002")
    engine = _engine()
    try:
        with engine.begin() as connection:
            revision_id, value_id = _insert_number_value(
                connection,
                unit_rule="COUNT",
                unit_code="RATIO",
                number_value=Decimal("2.5"),
            )

        with pytest.raises(RuntimeError, match="기존 NUMBER Claim"):
            _upgrade("0003")

        with engine.connect() as connection:
            assert _version(connection) == "0002"
            assert not _table_exists(connection, "attribute_revision_allowed_unit")
            assert _column_exists(
                connection,
                table_name="attribute_revision",
                column_name="unit_rule",
            )
            assert not _constraint_exists(
                connection,
                "fk_claim_attribute_value__allowed_unit",
            )
            unit_rule = connection.execute(
                sa.text(
                    """
                    SELECT unit_rule
                    FROM attribute_revision
                    WHERE attribute_revision_id = :revision_id
                    """
                ),
                {"revision_id": revision_id},
            ).scalar_one()
            assert unit_rule == "COUNT"
            assert _stored_number_value(connection, value_id) == (
                Decimal("2.5"),
                "RATIO",
            )
    finally:
        engine.dispose()


def test_multi_unit_downgrade_fails_without_data_loss() -> None:
    _upgrade("0003")
    engine = _engine()
    try:
        with engine.begin() as connection:
            revision_id, value_id = _insert_number_value(
                connection,
                unit_code="GB_PER_S",
                number_value=Decimal("1024.5"),
                allowed_units=("GB_PER_S", "TB_PER_S"),
            )

        with pytest.raises(RuntimeError, match="복수 허용 단위"):
            _downgrade("0002")

        with engine.connect() as connection:
            assert _version(connection) == "0003"
            assert _table_exists(connection, "attribute_revision_allowed_unit")
            assert not _column_exists(
                connection,
                table_name="attribute_revision",
                column_name="unit_rule",
            )
            assert _constraint_exists(
                connection,
                "fk_claim_attribute_value__allowed_unit",
            )
            assert _allowed_units(connection, revision_id) == [
                "GB_PER_S",
                "TB_PER_S",
            ]
            assert _stored_number_value(connection, value_id) == (
                Decimal("1024.5"),
                "GB_PER_S",
            )
    finally:
        engine.dispose()
