"""Issue #216 migration rehearsal: never infer historical association provenance."""

import os
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

REHEARSAL_FLAG = "ONTOLOGY_MAP_PROMOTION_PROVENANCE_MIGRATION_REHEARSAL"
SERVER_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = SERVER_DIR / "alembic.ini"

pytestmark = pytest.mark.skipif(
    os.getenv(REHEARSAL_FLAG) != "1",
    reason="#216 migration rehearsal 전용 격리 PostgreSQL에서만 실행한다.",
)


def _database_url() -> str:
    database_url = os.environ["ONTOLOGY_MAP_DATABASE_URL"]
    database_name = sa.engine.make_url(database_url).database or ""
    if not database_name.endswith("_promotion216_test"):
        raise RuntimeError("#216 migration rehearsal은 전용 격리 DB에서만 실행한다")
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
            sa.text("SELECT to_regclass(:qualified)"),
            {"qualified": f"public.{table_name}"},
        ).scalar_one()
        is not None
    )


def _historical_alias_evidence(connection: Connection) -> tuple[int, int]:
    policy_id = int(
        connection.execute(
            sa.text("""
                INSERT INTO lint_policy_version (
                    version_no, validator_version, is_active
                ) VALUES (216005, 'issue-216-migration', false)
                RETURNING lint_policy_version_id
            """)
        ).scalar_one()
    )
    batch_id = int(
        connection.execute(
            sa.text("""
                INSERT INTO promotion_batch (
                    lint_policy_version_id, promotion_status,
                    publication_status, committed_at, ready_at
                ) VALUES (
                    :policy_id, 'COMMITTED', 'READY',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                RETURNING promotion_batch_id
            """),
            {"policy_id": policy_id},
        ).scalar_one()
    )
    type_id = int(
        connection.execute(
            sa.text("""
                INSERT INTO node_type (
                    node_type_code, display_name, creation_rule, is_active
                ) VALUES ('COMPANY', '회사', 'issue 216 migration', true)
                RETURNING node_type_id
            """)
        ).scalar_one()
    )
    node_id = int(
        connection.execute(
            sa.text("""
                INSERT INTO knowledge_item (
                    item_kind, current_state, promotion_batch_id
                ) VALUES ('NODE', 'EVIDENCE_VERIFIED', :batch_id)
                RETURNING knowledge_item_id
            """),
            {"batch_id": batch_id},
        ).scalar_one()
    )
    connection.execute(
        sa.text("INSERT INTO node (node_id, node_type_id) VALUES (:node_id, :type_id)"),
        {"node_id": node_id, "type_id": type_id},
    )
    alias_id = int(
        connection.execute(
            sa.text("""
                INSERT INTO node_alias (
                    node_id, alias_text, language, is_preferred
                ) VALUES (:node_id, '역사 alias', 'ko', true)
                RETURNING node_alias_id
            """),
            {"node_id": node_id},
        ).scalar_one()
    )
    body = f"역사 alias 근거 {uuid4()}"
    group_id = int(
        connection.execute(
            sa.text(
                "INSERT INTO evidence_group DEFAULT VALUES RETURNING evidence_group_id"
            )
        ).scalar_one()
    )
    document_id = int(
        connection.execute(
            sa.text("""
                INSERT INTO source_document (
                    evidence_group_id, source_key, version_no, canonical_url,
                    publisher_name, title, original_language,
                    normalized_body, body_hash, published_precision,
                    modified_precision, last_checked_at, last_check_status
                ) VALUES (
                    :group_id, :source_key, 1, :url,
                    'issue216', 'issue216 migration', 'ko',
                    :body, :body_hash, 'UNKNOWN', 'UNKNOWN',
                    CURRENT_TIMESTAMP, 'SUCCESS'
                )
                RETURNING source_document_id
            """),
            {
                "group_id": group_id,
                "source_key": f"issue216-migration:{uuid4()}",
                "url": f"https://example.invalid/{uuid4()}",
                "body": body,
                "body_hash": sha256(body.encode()).digest(),
            },
        ).scalar_one()
    )
    observation_id = int(
        connection.execute(
            sa.text("""
                INSERT INTO observation (
                    source_document_id, start_char, end_char,
                    quote_text, quote_hash, observed_at
                ) VALUES (
                    :document_id, 0, :end_char,
                    :body, :quote_hash, CURRENT_TIMESTAMP
                )
                RETURNING observation_id
            """),
            {
                "document_id": document_id,
                "end_char": len(body),
                "body": body,
                "quote_hash": sha256(body.encode()).digest(),
            },
        ).scalar_one()
    )
    connection.execute(
        sa.text("""
            INSERT INTO node_alias_evidence (node_alias_id, observation_id)
            VALUES (:alias_id, :observation_id)
        """),
        {"alias_id": alias_id, "observation_id": observation_id},
    )
    return alias_id, observation_id


def test_clean_upgrade_reaches_current_head() -> None:
    _upgrade("head")
    engine = _engine()
    try:
        with engine.connect() as connection:
            assert _version(connection) == "0010"
            assert _table_exists(connection, "promotion_canonical_change")
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM promotion_canonical_change")
                )
                == 0
            )
    finally:
        engine.dispose()


def test_0004_history_is_not_attributed_and_survives_round_trip() -> None:
    _upgrade("0004")
    engine = _engine()
    try:
        with engine.begin() as connection:
            alias_id, observation_id = _historical_alias_evidence(connection)

        _upgrade("0005")
        with engine.connect() as connection:
            assert _version(connection) == "0005"
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM promotion_canonical_change")
                )
                == 0
            )
            assert (
                connection.scalar(
                    sa.text("""
                        SELECT count(*) FROM node_alias_evidence
                        WHERE node_alias_id = :alias_id
                          AND observation_id = :observation_id
                    """),
                    {"alias_id": alias_id, "observation_id": observation_id},
                )
                == 1
            )

        _downgrade("0004")
        with engine.connect() as connection:
            assert _version(connection) == "0004"
            assert not _table_exists(connection, "promotion_canonical_change")
            assert (
                connection.scalar(
                    sa.text("""
                        SELECT count(*) FROM node_alias_evidence
                        WHERE node_alias_id = :alias_id
                          AND observation_id = :observation_id
                    """),
                    {"alias_id": alias_id, "observation_id": observation_id},
                )
                == 1
            )
    finally:
        engine.dispose()
