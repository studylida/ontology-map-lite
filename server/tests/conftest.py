"""Cross-test isolation for PostgreSQL-backed regression modules."""

import os

import pytest
import sqlalchemy as sa

from ontology_map.db import schema

_B3_TEST_MODULE = "test_extraction_promotion_postgres.py"


@pytest.fixture(autouse=True)
def isolate_b3_postgres_state(request: pytest.FixtureRequest):
    """Remove B-3 synthetic rows before the next test module can observe them."""
    yield

    if request.node.path.name != _B3_TEST_MODULE:
        return

    raw_url = os.environ.get("ONTOLOGY_MAP_KE_TEST_DATABASE_URL")
    if not raw_url:
        return

    url = sa.engine.make_url(raw_url)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_ke127_test")

    engine = sa.create_engine(url)
    names = ", ".join(f'"{table.name}"' for table in schema.metadata.sorted_tables)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
            )
    finally:
        engine.dispose()
