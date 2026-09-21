import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REHEARSAL_FLAG = "ONTOLOGY_MAP_SOURCE_CLAIM_MIGRATION_REHEARSAL"
SERVER_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    os.getenv(REHEARSAL_FLAG) != "1",
    reason="#247 migration rehearsal 전용 격리 PostgreSQL에서만 실행한다.",
)


def _database_url() -> str:
    database_url = os.environ["ONTOLOGY_MAP_DATABASE_URL"]
    database_name = sa.engine.make_url(database_url).database or ""
    if "ke127_test" not in database_name:
        raise RuntimeError("#247 migration rehearsal은 전용 격리 DB에서만 실행한다")
    return database_url


def _config() -> Config:
    return Config(str(SERVER_DIR / "alembic.ini"))


def _reset() -> None:
    engine = sa.create_engine(_database_url())
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_database() -> Iterator[None]:
    _reset()
    yield
    _reset()


def test_downgrade_refuses_to_delete_source_claim() -> None:
    command.upgrade(_config(), "0007")
    engine = sa.create_engine(_database_url())
    try:
        with engine.begin() as connection:
            contract_id = connection.scalar(
                sa.text(
                    """
                    INSERT INTO output_schema_definition (
                        task_kind, version_no, schema_json, is_active
                    ) VALUES (
                        'KNOWLEDGE_EXTRACTION', 247001, '{}'::jsonb, false
                    ) RETURNING output_schema_definition_id
                    """
                )
            )
            task_id = connection.scalar(
                sa.text(
                    """
                    INSERT INTO model_task (
                        task_kind, input_hash, output_schema_definition_id,
                        model_version, prompt_version, cache_key
                    ) VALUES (
                        'KNOWLEDGE_EXTRACTION', decode(repeat('00', 32), 'hex'),
                        :contract_id, 'migration-rehearsal', '247001',
                        decode(repeat('01', 32), 'hex')
                    ) RETURNING model_task_id
                    """
                ),
                {"contract_id": contract_id},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO source_claim (
                        model_task_id, candidate_id, statement_text, language,
                        modality, projection_status, proposal_json,
                        projection_outcomes_json
                    ) VALUES (
                        :task_id, 'c1', '근거가 확인된 문장', 'ko', 'FACT',
                        'NONE', '{}'::jsonb, '{}'::jsonb
                    )
                    """
                ),
                {"task_id": task_id},
            )

        with pytest.raises(RuntimeError, match="downgrade refused"):
            command.downgrade(_config(), "0006")

        with engine.connect() as connection:
            assert (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == "0007"
            )
            assert connection.scalar(sa.text("SELECT count(*) FROM source_claim")) == 1
    finally:
        engine.dispose()
