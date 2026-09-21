import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

REHEARSAL_FLAG = "ONTOLOGY_MAP_TOPIC_REFERENCE_MIGRATION_REHEARSAL"
SERVER_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = SERVER_DIR / "alembic.ini"

pytestmark = pytest.mark.skipif(
    os.getenv(REHEARSAL_FLAG) != "1",
    reason="#203 migration rehearsal 전용 격리 PostgreSQL에서만 실행한다.",
)


def _database_url() -> str:
    database_url = os.environ["ONTOLOGY_MAP_DATABASE_URL"]
    database_name = sa.engine.make_url(database_url).database or ""
    if "topic_reference_migration_rehearsal" not in database_name:
        raise RuntimeError("#203 migration rehearsal은 전용 격리 DB에서만 실행한다")
    return database_url


def _engine() -> Engine:
    return sa.create_engine(_database_url(), poolclass=NullPool)


def _config() -> Config:
    return Config(str(ALEMBIC_CONFIG))


def _upgrade(revision: str) -> None:
    command.upgrade(_config(), revision)


def _downgrade(revision: str) -> None:
    command.downgrade(_config(), revision)


def _reset_public_schema() -> None:
    engine = _engine()
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_database() -> Iterator[None]:
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


def _insert_policy_and_batch(connection: Connection) -> int:
    policy_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO lint_policy_version (
                    version_no, validator_version, is_active
                ) VALUES (203004, 'issue-203-migration', false)
                RETURNING lint_policy_version_id
                """
            )
        ).scalar_one()
    )
    return int(
        connection.execute(
            sa.text(
                """
                INSERT INTO promotion_batch (lint_policy_version_id)
                VALUES (:policy_id)
                RETURNING promotion_batch_id
                """
            ),
            {"policy_id": policy_id},
        ).scalar_one()
    )


def _insert_evidence_knowledge(
    connection: Connection,
) -> tuple[int, int, int, int]:
    batch_id = _insert_policy_and_batch(connection)
    company_type_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO node_type (
                    node_type_code, display_name, creation_rule, is_active
                ) VALUES ('COMPANY', '회사', 'migration rehearsal', true)
                RETURNING node_type_id
                """
            )
        ).scalar_one()
    )
    node_ids: list[int] = []
    for _index in range(2):
        node_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO knowledge_item (
                        item_kind, current_state, promotion_batch_id
                    ) VALUES ('NODE', 'EVIDENCE_VERIFIED', :batch_id)
                    RETURNING knowledge_item_id
                    """
                ),
                {"batch_id": batch_id},
            ).scalar_one()
        )
        connection.execute(
            sa.text(
                "INSERT INTO node (node_id, node_type_id) VALUES (:node_id, :type_id)"
            ),
            {"node_id": node_id, "type_id": company_type_id},
        )
        node_ids.append(node_id)

    relation_type_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO relation_type (relation_code)
                VALUES ('ISSUE_203_LEGACY_RELATION')
                RETURNING relation_type_id
                """
            )
        ).scalar_one()
    )
    revision_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO relation_type_revision (
                    relation_type_id, version_no, display_name,
                    directionality, is_active
                ) VALUES (
                    :relation_type_id, 1, 'migration relation', 'DIRECTED', true
                )
                RETURNING relation_type_revision_id
                """
            ),
            {"relation_type_id": relation_type_id},
        ).scalar_one()
    )
    relation_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO knowledge_item (
                    item_kind, current_state, promotion_batch_id
                ) VALUES ('RELATION', 'HUMAN_VERIFIED', :batch_id)
                RETURNING knowledge_item_id
                """
            ),
            {"batch_id": batch_id},
        ).scalar_one()
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO relation (
                relation_id, source_node_id, target_node_id,
                relation_type_revision_id, relation_identity_key
            ) VALUES (
                :relation_id, :source_node_id, :target_node_id,
                :revision_id, :identity_key
            )
            """
        ),
        {
            "relation_id": relation_id,
            "source_node_id": node_ids[0],
            "target_node_id": node_ids[1],
            "revision_id": revision_id,
            "identity_key": b"R" * 32,
        },
    )
    claim_id = int(
        connection.execute(
            sa.text(
                """
                INSERT INTO knowledge_item (
                    item_kind, current_state, promotion_batch_id
                ) VALUES ('CLAIM', 'EVIDENCE_VERIFIED', :batch_id)
                RETURNING knowledge_item_id
                """
            ),
            {"batch_id": batch_id},
        ).scalar_one()
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO claim (
                claim_id, statement_text, language, modality,
                asserted_from_precision, asserted_to_precision
            ) VALUES (
                :claim_id, 'migration claim', 'ko', 'FACT',
                'UNKNOWN', 'UNKNOWN'
            )
            """
        ),
        {"claim_id": claim_id},
    )
    return batch_id, node_ids[0], relation_id, claim_id


def test_clean_upgrade_reaches_current_head() -> None:
    _upgrade("head")
    engine = _engine()
    try:
        with engine.connect() as connection:
            assert _version(connection) == "0010"
            assert _table_exists(connection, "topic_reference")
            assert _table_exists(connection, "promotion_canonical_change")
            assert _column_exists(
                connection,
                table_name="knowledge_item",
                column_name="lifecycle_kind",
            )
    finally:
        engine.dispose()


def test_existing_evidence_data_round_trips_without_reclassification() -> None:
    _upgrade("0003")
    engine = _engine()
    try:
        with engine.begin() as connection:
            batch_id, node_id, relation_id, claim_id = _insert_evidence_knowledge(
                connection
            )

        _upgrade("0004")
        with engine.connect() as connection:
            assert _version(connection) == "0004"
            rows = (
                connection.execute(
                    sa.text(
                        """
                    SELECT knowledge_item_id, item_kind, lifecycle_kind,
                           current_state, promotion_batch_id
                    FROM knowledge_item
                    WHERE knowledge_item_id = ANY(CAST(:ids AS bigint[]))
                    ORDER BY knowledge_item_id
                    """
                    ),
                    {"ids": [node_id, relation_id, claim_id]},
                )
                .mappings()
                .all()
            )
            assert [str(row["lifecycle_kind"]) for row in rows] == [
                "EVIDENCE_BACKED",
                "EVIDENCE_BACKED",
                "EVIDENCE_BACKED",
            ]
            assert [int(row["promotion_batch_id"]) for row in rows] == [
                batch_id,
                batch_id,
                batch_id,
            ]
            assert [str(row["current_state"]) for row in rows] == [
                "EVIDENCE_VERIFIED",
                "HUMAN_VERIFIED",
                "EVIDENCE_VERIFIED",
            ]
            assert (
                connection.scalar(sa.text("SELECT count(*) FROM topic_reference")) == 0
            )

        _downgrade("0003")
        with engine.connect() as connection:
            assert _version(connection) == "0003"
            assert not _table_exists(connection, "topic_reference")
            assert not _column_exists(
                connection,
                table_name="knowledge_item",
                column_name="lifecycle_kind",
            )
            values = connection.execute(
                sa.text(
                    """
                    SELECT knowledge_item_id, current_state, promotion_batch_id
                    FROM knowledge_item
                    WHERE knowledge_item_id = ANY(CAST(:ids AS bigint[]))
                    ORDER BY knowledge_item_id
                    """
                ),
                {"ids": [node_id, relation_id, claim_id]},
            ).all()
            assert all(int(row.promotion_batch_id) == batch_id for row in values)

        _upgrade("0004")
        with engine.connect() as connection:
            assert _version(connection) == "0004"
            assert (
                connection.scalar(
                    sa.text(
                        """
                    SELECT count(*) FROM knowledge_item
                    WHERE lifecycle_kind = 'EVIDENCE_BACKED'
                    """
                    )
                )
                == 4
            )
    finally:
        engine.dispose()


def test_existing_evidence_topic_is_preserved_without_reclassification() -> None:
    _upgrade("0003")
    engine = _engine()
    try:
        with engine.begin() as connection:
            batch_id = _insert_policy_and_batch(connection)
            topic_type_id = int(
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO node_type (
                            node_type_code, display_name, creation_rule, is_active
                        ) VALUES ('TOPIC', '주제', 'legacy topic', true)
                        RETURNING node_type_id
                        """
                    )
                ).scalar_one()
            )
            topic_node_id = int(
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO knowledge_item (
                            item_kind, current_state, promotion_batch_id
                        ) VALUES ('NODE', 'EVIDENCE_VERIFIED', :batch_id)
                        RETURNING knowledge_item_id
                        """
                    ),
                    {"batch_id": batch_id},
                ).scalar_one()
            )
            connection.execute(
                sa.text(
                    "INSERT INTO node (node_id, node_type_id) "
                    "VALUES (:node_id, :type_id)"
                ),
                {"node_id": topic_node_id, "type_id": topic_type_id},
            )

        _upgrade("0004")

        with engine.connect() as connection:
            assert _version(connection) == "0004"
            row = connection.execute(
                sa.text(
                    """
                    SELECT lifecycle_kind, current_state, promotion_batch_id
                    FROM knowledge_item
                    WHERE knowledge_item_id = :id
                    """
                ),
                {"id": topic_node_id},
            ).one()
            assert row.lifecycle_kind == "EVIDENCE_BACKED"
            assert row.current_state == "EVIDENCE_VERIFIED"
            assert int(row.promotion_batch_id) == batch_id
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM topic_reference WHERE node_id = :id"),
                    {"id": topic_node_id},
                )
                == 0
            )

        _downgrade("0003")
        with engine.connect() as connection:
            assert _version(connection) == "0003"
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT current_state FROM knowledge_item "
                        "WHERE knowledge_item_id = :id"
                    ),
                    {"id": topic_node_id},
                )
                == "EVIDENCE_VERIFIED"
            )
    finally:
        engine.dispose()


def test_reference_topic_downgrade_fails_without_data_loss() -> None:
    _upgrade("0004")
    engine = _engine()
    try:
        with engine.begin() as connection:
            topic_type_id = int(
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO node_type (
                            node_type_code, display_name, creation_rule, is_active
                        ) VALUES ('TOPIC', '주제', 'reference Topic only', true)
                        RETURNING node_type_id
                        """
                    )
                ).scalar_one()
            )
            topic_node_id = int(
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO knowledge_item (
                            item_kind, lifecycle_kind,
                            current_state, promotion_batch_id
                        ) VALUES ('NODE', 'PRODUCT_REFERENCE', NULL, NULL)
                        RETURNING knowledge_item_id
                        """
                    )
                ).scalar_one()
            )
            connection.execute(
                sa.text(
                    "INSERT INTO node (node_id, node_type_id) "
                    "VALUES (:node_id, :type_id)"
                ),
                {"node_id": topic_node_id, "type_id": topic_type_id},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO topic_reference (
                        node_id, topic_code, canonical_display_name, is_active
                    ) VALUES (
                        :node_id, 'SEMICONDUCTOR', '반도체', true
                    )
                    """
                ),
                {"node_id": topic_node_id},
            )

        with pytest.raises(RuntimeError, match="PRODUCT_REFERENCE Topic"):
            _downgrade("0003")

        with engine.connect() as connection:
            assert _version(connection) == "0004"
            assert _table_exists(connection, "topic_reference")
            row = connection.execute(
                sa.text(
                    """
                    SELECT ki.lifecycle_kind, ki.current_state,
                           ki.promotion_batch_id, tr.topic_code,
                           tr.canonical_display_name, tr.is_active
                    FROM knowledge_item AS ki
                    JOIN topic_reference AS tr
                      ON tr.node_id = ki.knowledge_item_id
                    WHERE ki.knowledge_item_id = :node_id
                    """
                ),
                {"node_id": topic_node_id},
            ).one()
            assert row.lifecycle_kind == "PRODUCT_REFERENCE"
            assert row.current_state is None
            assert row.promotion_batch_id is None
            assert row.topic_code == "SEMICONDUCTOR"
            assert row.canonical_display_name == "반도체"
            assert row.is_active is True
    finally:
        engine.dispose()
