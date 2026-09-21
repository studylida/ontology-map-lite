"""#46 publication failure/retry lifecycle exercised through #215 coordinator."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import initial_publication as publication
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.session import get_engine

DATABASE_URL = os.environ.get("ONTOLOGY_MAP_INITIAL_PUBLICATION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="isolated migrated PostgreSQL URL was not supplied",
)


def _engine() -> sa.Engine:
    engine = get_engine()
    url = engine.url
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_publication215_test")
    return engine


@pytest.fixture(autouse=True)
def _isolate_database() -> None:
    if not DATABASE_URL:
        yield
        return
    names = ", ".join(f'"{table.name}"' for table in s.metadata.sorted_tables)
    engine = _engine()
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
            )
        yield
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(
                    sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
                )
        finally:
            engine.dispose()


def _new_committed_node_batch(session: Session) -> tuple[int, int]:
    policy_id = session.scalar(
        sa.select(s.lint_policy_version.c.lint_policy_version_id)
        .where(s.lint_policy_version.c.is_active)
        .limit(1)
    )
    node_type_id = session.scalar(
        sa.select(s.node_type.c.node_type_id)
        .where(s.node_type.c.node_type_code == "COMPANY")
        .limit(1)
    )
    assert policy_id is not None
    assert node_type_id is not None

    batch_id = session.scalar(
        s.promotion_batch.insert()
        .values(lint_policy_version_id=policy_id)
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    assert batch_id is not None
    node_id = session.scalar(
        s.knowledge_item.insert()
        .values(
            item_kind="NODE",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(s.knowledge_item.c.knowledge_item_id)
    )
    assert node_id is not None
    session.execute(s.node.insert().values(node_id=node_id, node_type_id=node_type_id))
    session.execute(
        s.node_alias.insert().values(
            node_id=node_id,
            alias_text=f"publication-failure-{uuid4().hex}",
            language="ko",
            is_preferred=True,
        )
    )
    provenance.mark_promotion_committed(session, int(batch_id))
    return int(batch_id), int(node_id)


def test_failed_retry_preserves_membership_pointer_and_previous_ready() -> None:
    load_hbf_fixture()
    engine = _engine()
    with Session(engine) as session:
        previous_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )

    with Session(engine) as session, session.begin():
        batch_id, node_id = _new_committed_node_batch(session)
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_id)
        document_id = publication.ensure_search_document(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )

    with Session(engine) as session, session.begin():
        assert publication.mark_initial_publication_failed(
            session,
            batch_id,
            "mandatory model task reached terminal failure",
        )
        assert not publication.mark_initial_publication_failed(
            session,
            batch_id,
            "mandatory model task reached terminal failure",
        )

    with Session(engine) as session:
        failed = session.execute(
            sa.select(
                s.promotion_batch.c.publication_status,
                s.promotion_batch.c.publication_failure_reason,
            ).where(s.promotion_batch.c.promotion_batch_id == batch_id)
        ).one()
        selected = session.scalar(
            sa.select(s.publication_affected_node.c.node_search_document_id).where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == node_id,
            )
        )
        ready_during_failure = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
    assert failed.publication_status == "FAILED"
    assert failed.publication_failure_reason == (
        "mandatory model task reached terminal failure"
    )
    assert selected == document_id
    assert previous_ready <= ready_during_failure

    with Session(engine) as session, session.begin():
        assert publication.retry_failed_initial_publication(session, batch_id)
        assert not publication.retry_failed_initial_publication(session, batch_id)

    with Session(engine) as session:
        retried = session.execute(
            sa.select(
                s.promotion_batch.c.publication_status,
                s.promotion_batch.c.publication_failure_reason,
            ).where(s.promotion_batch.c.promotion_batch_id == batch_id)
        ).one()
        membership = tuple(
            int(value)
            for value in session.scalars(
                sa.select(s.publication_affected_node.c.node_id)
                .where(s.publication_affected_node.c.promotion_batch_id == batch_id)
                .order_by(s.publication_affected_node.c.node_id)
            )
        )
        selected_after_retry = session.scalar(
            sa.select(s.publication_affected_node.c.node_search_document_id).where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == node_id,
            )
        )
    assert retried.publication_status == "PREPARING"
    assert retried.publication_failure_reason is None
    assert membership == (node_id,)
    assert selected_after_retry == document_id
