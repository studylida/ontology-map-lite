"""Real PostgreSQL top-level rollback regressions for the #128 write boundary."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
import test_entity_resolution_postgres as base
from sqlalchemy.orm import Session

from ontology_map import entity_resolution as service

pytestmark = pytest.mark.skipif(
    not base.DATABASE_URL,
    reason="isolated migrated PostgreSQL URL was not supplied",
)


@pytest.fixture
def database_engine():
    url = sa.engine.make_url(base.DATABASE_URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_er128_test")
    engine = sa.create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure", ["orphan", "writer"])
def test_top_level_failure_rolls_back_every_new_row_for_fresh_connection(
    database_engine, failure
):
    name = f"TopRollback{uuid4().hex}"
    expected = ValueError if failure == "orphan" else RuntimeError
    created_node_id = None
    document_id = None
    promotion_id = None

    with database_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection) as session:
                base.seed(session)
                prior = base.node(session, "기존회사")
                target = base.source_mention(session, name)
                document_id = target.source_ranges[0].source_document_id
                result = service.resolve_mention(session, target, base.propose("NEW"))
                promotion_id = base.batch(session)
                with pytest.raises(expected):
                    with service.resolved_nodes_for_promotion(
                        session, promotion_id, (result,), frozenset({"m1"})
                    ) as bindings:
                        created_node_id = bindings["m1"].node_id
                        if failure == "writer":
                            base.relation_claim(
                                session,
                                promotion_id,
                                bindings["m1"],
                                prior,
                                f"{name}는 기존회사와 협력한다.",
                            )
                            raise RuntimeError("synthetic top-level writer failure")
            assert transaction.is_active
        finally:
            if transaction.is_active:
                transaction.rollback()

    assert created_node_id is not None
    assert document_id is not None
    assert promotion_id is not None
    with database_engine.connect() as observer:
        assert (
            observer.execute(
                sa.text(
                    "SELECT count(*) FROM source_document "
                    "WHERE source_document_id = :document_id"
                ),
                {"document_id": document_id},
            ).scalar_one()
            == 0
        )
        assert (
            observer.execute(
                sa.text(
                    "SELECT count(*) FROM promotion_batch "
                    "WHERE promotion_batch_id = :promotion_id"
                ),
                {"promotion_id": promotion_id},
            ).scalar_one()
            == 0
        )
        assert (
            observer.execute(
                sa.text("SELECT count(*) FROM node WHERE node_id = :node_id"),
                {"node_id": created_node_id},
            ).scalar_one()
            == 0
        )
        assert (
            observer.execute(
                sa.text(
                    "SELECT count(*) FROM node_alias WHERE alias_text = :alias_text"
                ),
                {"alias_text": name},
            ).scalar_one()
            == 0
        )
