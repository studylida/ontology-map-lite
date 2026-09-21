import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.lint_scope import requires_evidence_trace_lint
from ontology_map.db.schema import (
    claim,
    claim_observation,
    claim_relation,
    knowledge_item,
    node,
    node_type,
    promotion_batch,
    publication_affected_node,
    relation,
    relation_type,
    relation_type_revision,
    search_document_basis,
)
from ontology_map.db.session import get_engine
from ontology_map.db.topic_references import (
    ensure_topic_reference,
    set_topic_reference_active,
)
from ontology_map.exploration import TimeWindow
from ontology_map.topic_exploration import get_topic_exploration

pytestmark = pytest.mark.skipif(
    os.getenv("ONTOLOGY_MAP_TOPIC_REFERENCE_TEST") != "1",
    reason="#203 전용 실제 PostgreSQL 검증에서 실행한다.",
)

NOW = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)


@contextmanager
def rollback_session() -> Iterator[Session]:
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection) as session:
                session.execute(sa.text("SELECT 1"))
                yield session
        finally:
            transaction.rollback()


def _force_deferred_constraints(session: Session) -> None:
    session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))


def _batch_id(session: Session, node_id: int) -> int:
    value = session.scalar(
        sa.select(knowledge_item.c.promotion_batch_id).where(
            knowledge_item.c.knowledge_item_id == node_id
        )
    )
    assert value is not None
    return int(value)


def _type_id(session: Session, code: str) -> int:
    value = session.scalar(
        sa.select(node_type.c.node_type_id).where(node_type.c.node_type_code == code)
    )
    assert value is not None
    return int(value)


def _evidence_node(session: Session, *, batch_id: int, node_type_id: int) -> int:
    node_id = int(
        session.execute(
            knowledge_item.insert()
            .values(
                item_kind="NODE",
                lifecycle_kind="EVIDENCE_BACKED",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    session.execute(node.insert().values(node_id=node_id, node_type_id=node_type_id))
    return node_id


def _has_topic_revision(session: Session) -> int:
    relation_type_id = int(
        session.execute(
            relation_type.insert()
            .values(relation_code="HAS_TOPIC")
            .returning(relation_type.c.relation_type_id)
        ).scalar_one()
    )
    return int(
        session.execute(
            relation_type_revision.insert()
            .values(
                relation_type_id=relation_type_id,
                version_no=1,
                display_name="주제 분류",
                directionality="DIRECTED",
                is_active=True,
            )
            .returning(relation_type_revision.c.relation_type_revision_id)
        ).scalar_one()
    )


def _relation(
    session: Session,
    *,
    batch_id: int,
    revision_id: int,
    source_node_id: int,
    target_node_id: int,
    key: str,
) -> int:
    relation_id = int(
        session.execute(
            knowledge_item.insert()
            .values(
                item_kind="RELATION",
                lifecycle_kind="EVIDENCE_BACKED",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    session.execute(
        relation.insert().values(
            relation_id=relation_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type_revision_id=revision_id,
            relation_identity_key=sha256(key.encode()).digest(),
        )
    )
    return relation_id


def _claim_without_observation(
    session: Session, *, batch_id: int, relation_id: int
) -> int:
    claim_id = int(
        session.execute(
            knowledge_item.insert()
            .values(
                item_kind="CLAIM",
                lifecycle_kind="EVIDENCE_BACKED",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    session.execute(
        claim.insert().values(
            claim_id=claim_id,
            statement_text="근거 Observation이 없는 synthetic HAS_TOPIC Claim",
            language="ko",
            modality="FACT",
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    session.execute(
        claim_relation.insert().values(
            claim_id=claim_id,
            relation_id=relation_id,
            stance="SUPPORT",
        )
    )
    return claim_id


def _latest_search_document(session: Session, node_id: int) -> int:
    value = session.scalar(
        sa.select(publication_affected_node.c.node_search_document_id)
        .join(
            promotion_batch,
            promotion_batch.c.promotion_batch_id
            == publication_affected_node.c.promotion_batch_id,
        )
        .where(
            publication_affected_node.c.node_id == node_id,
            promotion_batch.c.promotion_status == "COMMITTED",
            promotion_batch.c.publication_status == "READY",
        )
        .order_by(
            promotion_batch.c.ready_at.desc(),
            promotion_batch.c.promotion_batch_id.desc(),
        )
        .limit(1)
    )
    assert value is not None
    return int(value)


def test_lint_scope_is_lifecycle_based_not_topic_type() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        batch_id = _batch_id(session, node_ids["sk_hynix"])
        legacy_topic_id = _evidence_node(
            session,
            batch_id=batch_id,
            node_type_id=_type_id(session, "TOPIC"),
        )
        reference = ensure_topic_reference(
            session,
            topic_code="SEMICONDUCTOR",
            canonical_display_name="반도체",
            is_active=True,
        )
        _force_deferred_constraints(session)

        assert requires_evidence_trace_lint(session, node_ids["sk_hynix"]) is True
        assert requires_evidence_trace_lint(session, legacy_topic_id) is True
        assert requires_evidence_trace_lint(session, reference.node_id) is False

        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            session.execute(
                knowledge_item.update()
                .where(knowledge_item.c.knowledge_item_id == legacy_topic_id)
                .values(
                    lifecycle_kind="PRODUCT_REFERENCE",
                    current_state=None,
                    promotion_batch_id=None,
                )
            )
            session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))

        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            session.execute(
                knowledge_item.update()
                .where(knowledge_item.c.knowledge_item_id == node_ids["sk_hynix"])
                .values(
                    lifecycle_kind="PRODUCT_REFERENCE",
                    current_state=None,
                    promotion_batch_id=None,
                )
            )
            session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))

        assert requires_evidence_trace_lint(session, legacy_topic_id) is True
        assert requires_evidence_trace_lint(session, node_ids["sk_hynix"]) is True


def test_has_topic_requires_active_reference_and_keeps_existing_rows() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        source_node_id = node_ids["sk_hynix"]
        batch_id = _batch_id(session, source_node_id)
        reference = ensure_topic_reference(
            session,
            topic_code="SEMICONDUCTOR",
            canonical_display_name="반도체",
            is_active=True,
        )
        _force_deferred_constraints(session)
        revision_id = _has_topic_revision(session)

        relation_id = _relation(
            session,
            batch_id=batch_id,
            revision_id=revision_id,
            source_node_id=source_node_id,
            target_node_id=reference.node_id,
            key="issue-203-active-reference",
        )
        claim_id = _claim_without_observation(
            session,
            batch_id=batch_id,
            relation_id=relation_id,
        )
        search_document_id = _latest_search_document(session, source_node_id)
        session.execute(
            search_document_basis.insert(),
            [
                {
                    "node_search_document_id": search_document_id,
                    "knowledge_item_id": relation_id,
                },
                {
                    "node_search_document_id": search_document_id,
                    "knowledge_item_id": claim_id,
                },
            ],
        )
        session.flush()

        assert requires_evidence_trace_lint(session, relation_id) is True
        assert requires_evidence_trace_lint(session, claim_id) is True
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(claim_observation)
                .where(claim_observation.c.claim_id == claim_id)
            )
            == 0
        )
        result = get_topic_exploration(
            session,
            reference.node_id,
            TimeWindow.RECENT_90_DAYS,
            now=NOW,
        )
        assert result.total_public_membership_count == 0
        assert all(item.relation_id != relation_id for item in result.graph.relations)

        set_topic_reference_active(session, reference.node_id, is_active=False)
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(relation)
                .where(relation.c.relation_id == relation_id)
            )
            == 1
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(claim)
                .where(claim.c.claim_id == claim_id)
            )
            == 1
        )

        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            _relation(
                session,
                batch_id=batch_id,
                revision_id=revision_id,
                source_node_id=node_ids["sandisk"],
                target_node_id=reference.node_id,
                key="issue-203-inactive-reference",
            )

        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(relation)
                .where(relation.c.relation_id == relation_id)
            )
            == 1
        )
