"""Focused PostgreSQL regressions for issue #215 Phase A only."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import initial_publication as publication
from ontology_map.db import initial_publication_start as phase_a
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
    with _engine().begin() as connection:
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    yield


def _batch(session: Session) -> int:
    policy_id = session.scalar(
        sa.select(s.lint_policy_version.c.lint_policy_version_id)
        .where(s.lint_policy_version.c.is_active)
        .limit(1)
    )
    assert policy_id is not None
    batch_id = session.scalar(
        s.promotion_batch.insert()
        .values(lint_policy_version_id=policy_id)
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    assert batch_id is not None
    return int(batch_id)


def _new_node(session: Session, batch_id: int) -> int:
    node_type_id = session.scalar(
        sa.select(s.node_type.c.node_type_id)
        .where(s.node_type.c.node_type_code == "COMPANY")
        .limit(1)
    )
    assert node_type_id is not None
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
            alias_text=f"phase-a-{uuid4().hex}",
            language="ko",
            is_preferred=True,
        )
    )
    return int(node_id)


def _artifact_counts(session: Session) -> tuple[int, int, int, int, int]:
    return (
        int(session.scalar(sa.select(sa.func.count()).select_from(s.model_task)) or 0),
        int(
            session.scalar(
                sa.select(sa.func.count()).select_from(s.node_search_document)
            )
            or 0
        ),
        int(
            session.scalar(sa.select(sa.func.count()).select_from(s.node_context)) or 0
        ),
        int(
            session.scalar(sa.select(sa.func.count()).select_from(s.node_question_set))
            or 0
        ),
        int(
            session.scalar(
                sa.select(sa.func.count()).select_from(s.node_insight_window)
            )
            or 0
        ),
    )


def test_preparing_reentry_uses_frozen_membership_without_reprojection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_hbf_fixture()
    engine = _engine()
    with Session(engine) as session, session.begin():
        batch_id = _batch(session)
        node_id = _new_node(session, batch_id)
        provenance.mark_promotion_committed(session, batch_id)

    with Session(engine) as session, session.begin():
        first = publication.start_initial_publication(session, batch_id)
    assert first.started is True
    assert first.affected_node_ids == (node_id,)

    def forbidden_reprojection(_session: Session, _batch_id: int) -> tuple[int, ...]:
        raise AssertionError("PREPARING re-entry must not re-read durable provenance")

    monkeypatch.setattr(phase_a, "affected_node_ids", forbidden_reprojection)
    with Session(engine) as session, session.begin():
        second = publication.start_initial_publication(session, batch_id)

    assert second.started is False
    assert second.publication_status == "PREPARING"
    assert second.affected_node_ids == (node_id,)
    with Session(engine) as session:
        assert phase_a.membership(session, batch_id) == (node_id,)
        count = session.scalar(
            sa.select(sa.func.count())
            .select_from(s.publication_affected_node)
            .where(s.publication_affected_node.c.promotion_batch_id == batch_id)
        )
    assert count == 1


def test_phase_a_preserves_previous_ready_and_does_not_create_recovery_state() -> None:
    load_hbf_fixture()
    engine = _engine()
    with Session(engine) as session:
        previous_ready = tuple(
            session.execute(
                sa.select(
                    s.promotion_batch.c.promotion_batch_id,
                    s.promotion_batch.c.publication_status,
                    s.promotion_batch.c.ready_at,
                )
                .where(s.promotion_batch.c.publication_status == "READY")
                .order_by(s.promotion_batch.c.promotion_batch_id)
            ).all()
        )
        assert previous_ready
        previous_ready_ids = [int(row.promotion_batch_id) for row in previous_ready]
        previous_pointers = tuple(
            session.execute(
                sa.select(
                    s.publication_affected_node.c.promotion_batch_id,
                    s.publication_affected_node.c.node_id,
                    s.publication_affected_node.c.node_search_document_id,
                    s.publication_affected_node.c.node_context_id,
                    s.publication_affected_node.c.node_insight_model_task_id,
                )
                .where(
                    s.publication_affected_node.c.promotion_batch_id.in_(
                        previous_ready_ids
                    )
                )
                .order_by(
                    s.publication_affected_node.c.promotion_batch_id,
                    s.publication_affected_node.c.node_id,
                )
            ).all()
        )

    with Session(engine) as session, session.begin():
        batch_id = _batch(session)
        node_id = _new_node(session, batch_id)
        provenance.mark_promotion_committed(session, batch_id)

    with Session(engine) as session:
        batches_before_start = int(
            session.scalar(sa.select(sa.func.count()).select_from(s.promotion_batch))
            or 0
        )
        artifacts_before_start = _artifact_counts(session)

    with Session(engine) as session, session.begin():
        started = publication.start_initial_publication(session, batch_id)
        assert started.started is True
        assert started.affected_node_ids == (node_id,)

    with Session(engine) as session:
        current = session.execute(
            sa.select(
                s.promotion_batch.c.publication_status,
                s.promotion_batch.c.ready_at,
                s.promotion_batch.c.publication_failure_reason,
            ).where(s.promotion_batch.c.promotion_batch_id == batch_id)
        ).one()
        assert current.publication_status == "PREPARING"
        assert current.ready_at is None
        assert current.publication_failure_reason is None
        assert phase_a.membership(session, batch_id) == (node_id,)

        batches_after_start = int(
            session.scalar(sa.select(sa.func.count()).select_from(s.promotion_batch))
            or 0
        )
        assert batches_after_start == batches_before_start
        assert _artifact_counts(session) == artifacts_before_start

        ready_after = tuple(
            session.execute(
                sa.select(
                    s.promotion_batch.c.promotion_batch_id,
                    s.promotion_batch.c.publication_status,
                    s.promotion_batch.c.ready_at,
                )
                .where(s.promotion_batch.c.promotion_batch_id.in_(previous_ready_ids))
                .order_by(s.promotion_batch.c.promotion_batch_id)
            ).all()
        )
        pointers_after = tuple(
            session.execute(
                sa.select(
                    s.publication_affected_node.c.promotion_batch_id,
                    s.publication_affected_node.c.node_id,
                    s.publication_affected_node.c.node_search_document_id,
                    s.publication_affected_node.c.node_context_id,
                    s.publication_affected_node.c.node_insight_model_task_id,
                )
                .where(
                    s.publication_affected_node.c.promotion_batch_id.in_(
                        previous_ready_ids
                    )
                )
                .order_by(
                    s.publication_affected_node.c.promotion_batch_id,
                    s.publication_affected_node.c.node_id,
                )
            ).all()
        )

    assert ready_after == previous_ready
    assert pointers_after == previous_pointers
