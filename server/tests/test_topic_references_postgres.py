import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.schema import (
    claim,
    claim_observation,
    claim_relation,
    evidence_group,
    knowledge_item,
    lint_policy_version,
    node,
    node_alias,
    node_search_document,
    node_type,
    observation,
    promotion_batch,
    publication_affected_node,
    relation,
    relation_type,
    relation_type_revision,
    search_document_basis,
    source_document,
    topic_reference,
)
from ontology_map.db.session import get_engine
from ontology_map.db.topic_references import (
    ensure_topic_reference,
    set_topic_reference_active,
)
from ontology_map.entity_resolution import resolve_mention, resolved_nodes_for_promotion
from ontology_map.entity_resolution_contracts import EntityMention, SourceRange
from ontology_map.exploration import MAX_DIRECT_NODES, TimeWindow, get_exploration
from ontology_map.search import search_nodes
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


def _batch(session: Session, version: int = 203004) -> int:
    policy_id = int(
        session.execute(
            lint_policy_version.insert()
            .values(
                version_no=version,
                validator_version=f"issue-203-{version}",
                is_active=False,
            )
            .returning(lint_policy_version.c.lint_policy_version_id)
        ).scalar_one()
    )
    return int(
        session.execute(
            promotion_batch.insert()
            .values(lint_policy_version_id=policy_id)
            .returning(promotion_batch.c.promotion_batch_id)
        ).scalar_one()
    )


def _node_type(session: Session, code: str) -> int:
    existing = session.scalar(
        sa.select(node_type.c.node_type_id).where(node_type.c.node_type_code == code)
    )
    if existing is not None:
        return int(existing)
    return int(
        session.execute(
            node_type.insert()
            .values(
                node_type_code=code,
                display_name={"TOPIC": "주제", "COMPANY": "회사"}.get(code, code),
                creation_rule="Issue #203 synthetic test type",
                is_active=True,
            )
            .returning(node_type.c.node_type_id)
        ).scalar_one()
    )


def _evidence_node(
    session: Session,
    *,
    batch_id: int,
    node_type_id: int,
) -> int:
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


def _relation_revision(session: Session, code: str) -> int:
    relation_type_id = int(
        session.execute(
            relation_type.insert()
            .values(relation_code=code)
            .returning(relation_type.c.relation_type_id)
        ).scalar_one()
    )
    return int(
        session.execute(
            relation_type_revision.insert()
            .values(
                relation_type_id=relation_type_id,
                version_no=1,
                display_name=code,
                directionality="DIRECTED",
                is_active=True,
            )
            .returning(relation_type_revision.c.relation_type_revision_id)
        ).scalar_one()
    )


def _evidence_relation(
    session: Session,
    *,
    batch_id: int,
    revision_id: int,
    source_node_id: int,
    target_node_id: int,
    key: str,
    current_state: str = "EVIDENCE_VERIFIED",
) -> int:
    relation_id = int(
        session.execute(
            knowledge_item.insert()
            .values(
                item_kind="RELATION",
                lifecycle_kind="EVIDENCE_BACKED",
                current_state=current_state,
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


def _evidence_claim(
    session: Session,
    *,
    batch_id: int,
    relation_id: int,
    observation_id: int,
    text: str,
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
            statement_text=text,
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
    session.execute(
        claim_observation.insert().values(
            claim_id=claim_id,
            observation_id=observation_id,
        )
    )
    return claim_id


def _observation(
    session: Session,
    *,
    source_key: str,
    published_at: datetime,
) -> int:
    group_id = int(
        session.execute(
            evidence_group.insert().returning(evidence_group.c.evidence_group_id)
        ).scalar_one()
    )
    body = f"{source_key} Topic membership evidence"
    source_id = int(
        session.execute(
            source_document.insert()
            .values(
                evidence_group_id=group_id,
                source_key=source_key,
                version_no=1,
                canonical_url=f"https://example.com/{source_key}",
                publisher_name="Issue #203 synthetic test",
                title=source_key,
                original_language="ko",
                normalized_body=body,
                body_hash=sha256(body.encode()).digest(),
                published_at=published_at,
                published_precision="DAY",
                modified_precision="UNKNOWN",
                last_checked_at=NOW,
                last_check_status="SUCCESS",
            )
            .returning(source_document.c.source_document_id)
        ).scalar_one()
    )
    return int(
        session.execute(
            observation.insert()
            .values(
                source_document_id=source_id,
                start_char=0,
                end_char=len(body),
                quote_text=body,
                quote_hash=sha256(body.encode()).digest(),
                paragraph_number=1,
                observed_at=NOW,
            )
            .returning(observation.c.observation_id)
        ).scalar_one()
    )


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


def _publish_membership_basis(
    session: Session,
    *,
    member_node_id: int,
    relation_id: int,
    claim_id: int,
) -> None:
    search_document_id = _latest_search_document(session, member_node_id)
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


def test_lifecycle_constraints_keep_evidence_and_reference_separate() -> None:
    with rollback_session() as session:
        batch_id = _batch(session)
        company_type_id = _node_type(session, "COMPANY")
        topic_type_id = _node_type(session, "TOPIC")
        company_id = _evidence_node(
            session, batch_id=batch_id, node_type_id=company_type_id
        )
        second_company_id = _evidence_node(
            session, batch_id=batch_id, node_type_id=company_type_id
        )
        revision_id = _relation_revision(session, "ISSUE_203_RELATION")
        relation_id = _evidence_relation(
            session,
            batch_id=batch_id,
            revision_id=revision_id,
            source_node_id=company_id,
            target_node_id=second_company_id,
            key="normal-relation",
        )
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
                statement_text="기존 Claim lifecycle regression",
                language="ko",
                modality="FACT",
                asserted_from_precision="UNKNOWN",
                asserted_to_precision="UNKNOWN",
            )
        )
        _force_deferred_constraints(session)

        assert (
            session.scalar(
                sa.select(knowledge_item.c.lifecycle_kind)
                .where(
                    knowledge_item.c.knowledge_item_id.in_(
                        [company_id, relation_id, claim_id]
                    )
                )
                .limit(1)
            )
            == "EVIDENCE_BACKED"
        )

        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            session.execute(
                knowledge_item.insert().values(
                    item_kind="NODE",
                    lifecycle_kind="EVIDENCE_BACKED",
                    current_state=None,
                    promotion_batch_id=batch_id,
                )
            )
        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            session.execute(
                knowledge_item.insert().values(
                    item_kind="CLAIM",
                    lifecycle_kind="EVIDENCE_BACKED",
                    current_state="EVIDENCE_VERIFIED",
                    promotion_batch_id=None,
                )
            )

        reference = ensure_topic_reference(
            session,
            topic_code="SEMICONDUCTOR",
            canonical_display_name="반도체",
            is_active=True,
        )
        _force_deferred_constraints(session)
        reference_state = session.execute(
            sa.select(
                knowledge_item.c.lifecycle_kind,
                knowledge_item.c.current_state,
                knowledge_item.c.promotion_batch_id,
            ).where(knowledge_item.c.knowledge_item_id == reference.node_id)
        ).one()
        assert reference_state == ("PRODUCT_REFERENCE", None, None)
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(node_alias)
                .where(node_alias.c.node_id == reference.node_id)
            )
            == 0
        )

        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            product_company_id = int(
                session.execute(
                    knowledge_item.insert()
                    .values(
                        item_kind="NODE",
                        lifecycle_kind="PRODUCT_REFERENCE",
                        current_state=None,
                        promotion_batch_id=None,
                    )
                    .returning(knowledge_item.c.knowledge_item_id)
                ).scalar_one()
            )
            session.execute(
                node.insert().values(
                    node_id=product_company_id, node_type_id=company_type_id
                )
            )
            session.execute(
                topic_reference.insert().values(
                    node_id=product_company_id,
                    topic_code="MEMORY_SEMICONDUCTOR",
                    canonical_display_name="메모리 반도체",
                    is_active=True,
                )
            )
            session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))

        legacy_topic_id = _evidence_node(
            session, batch_id=batch_id, node_type_id=topic_type_id
        )
        _force_deferred_constraints(session)
        assert (
            session.scalar(
                sa.select(knowledge_item.c.lifecycle_kind).where(
                    knowledge_item.c.knowledge_item_id == legacy_topic_id
                )
            )
            == "EVIDENCE_BACKED"
        )

        second_reference = ensure_topic_reference(
            session,
            topic_code="MEMORY_SEMICONDUCTOR",
            canonical_display_name="메모리 반도체",
            is_active=True,
        )
        _force_deferred_constraints(session)
        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            session.execute(
                topic_reference.update()
                .where(topic_reference.c.node_id == second_reference.node_id)
                .values(
                    topic_code="SEMICONDUCTOR",
                    canonical_display_name="반도체",
                )
            )
        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            session.execute(
                topic_reference.update()
                .where(topic_reference.c.node_id == reference.node_id)
                .values(canonical_display_name="   ")
            )

        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            session.execute(
                node_search_document.insert().values(
                    node_id=reference.node_id,
                    identity_text="반도체",
                    knowledge_text="reference Topic에는 만들 수 없다",
                    input_hash=b"S" * 32,
                    generator_version="issue-203-test",
                )
            )
        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            session.execute(
                publication_affected_node.insert().values(
                    promotion_batch_id=batch_id,
                    node_id=reference.node_id,
                )
            )


def test_entity_resolution_reuses_active_reference_without_topic_alias() -> None:
    load_hbf_fixture()
    with rollback_session() as session:
        reference = ensure_topic_reference(
            session,
            topic_code="ARTIFICIAL_INTELLIGENCE",
            canonical_display_name="인공지능",
            is_active=True,
        )
        _force_deferred_constraints(session)
        observation_row = session.execute(
            sa.select(
                observation.c.source_document_id,
                observation.c.start_char,
                observation.c.end_char,
            )
            .order_by(observation.c.observation_id)
            .limit(1)
        ).one()
        mention = EntityMention(
            mention_id="topic-ai",
            text="AI",
            node_type="TOPIC",
            approved_topic_name="인공지능",
            source_ranges=(
                SourceRange(
                    source_document_id=int(observation_row.source_document_id),
                    start_char=int(observation_row.start_char),
                    end_char=int(observation_row.end_char),
                ),
            ),
        )

        def must_not_call_model(_messages: list[tuple[str, str]]) -> object:
            raise AssertionError("approved Topic reuse must be deterministic")

        resolution = resolve_mention(session, mention, must_not_call_model)
        assert resolution.decision == "SAME"
        assert resolution.node_id == reference.node_id
        assert resolution.candidates.nodes[0].candidate.preferred_alias == "인공지능"
        assert resolution.candidates.nodes[0].candidate.aliases == ()

        lint_policy_id = session.scalar(
            sa.select(lint_policy_version.c.lint_policy_version_id).limit(1)
        )
        assert lint_policy_id is not None
        pending_batch_id = int(
            session.execute(
                promotion_batch.insert()
                .values(lint_policy_version_id=lint_policy_id)
                .returning(promotion_batch.c.promotion_batch_id)
            ).scalar_one()
        )
        with resolved_nodes_for_promotion(
            session,
            pending_batch_id,
            [resolution],
            frozenset({"topic-ai"}),
        ) as bindings:
            assert bindings["topic-ai"].node_id == reference.node_id
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(node_alias)
                .where(node_alias.c.node_id == reference.node_id)
            )
            == 0
        )

        set_topic_reference_active(session, reference.node_id, is_active=False)
        unresolved = resolve_mention(session, mention, must_not_call_model)
        assert unresolved.decision == "UNRESOLVED"
        assert unresolved.node_id is None


def test_topic_exploration_and_has_topic_lifecycle_contract() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        reference = ensure_topic_reference(
            session,
            topic_code="SEMICONDUCTOR",
            canonical_display_name="반도체",
            is_active=True,
        )
        _force_deferred_constraints(session)
        revision_id = _relation_revision(session, "HAS_TOPIC")
        batch_id = int(
            session.scalar(
                sa.select(knowledge_item.c.promotion_batch_id).where(
                    knowledge_item.c.knowledge_item_id == node_ids["sk_hynix"]
                )
            )
        )

        recent_observation = _observation(
            session,
            source_key="issue-203-recent-membership",
            published_at=NOW - timedelta(days=30),
        )
        old_observation = _observation(
            session,
            source_key="issue-203-old-membership",
            published_at=NOW - timedelta(days=180),
        )
        recent_relation = _evidence_relation(
            session,
            batch_id=batch_id,
            revision_id=revision_id,
            source_node_id=node_ids["sk_hynix"],
            target_node_id=reference.node_id,
            key="sk-has-semiconductor",
        )
        recent_claim = _evidence_claim(
            session,
            batch_id=batch_id,
            relation_id=recent_relation,
            observation_id=recent_observation,
            text="SK하이닉스는 반도체 Topic에 직접 연결된다.",
        )
        _publish_membership_basis(
            session,
            member_node_id=node_ids["sk_hynix"],
            relation_id=recent_relation,
            claim_id=recent_claim,
        )

        old_relation = _evidence_relation(
            session,
            batch_id=batch_id,
            revision_id=revision_id,
            source_node_id=node_ids["sandisk"],
            target_node_id=reference.node_id,
            key="sandisk-has-semiconductor",
        )
        old_claim = _evidence_claim(
            session,
            batch_id=batch_id,
            relation_id=old_relation,
            observation_id=old_observation,
            text="SanDisk는 반도체 Topic에 직접 연결된다.",
        )
        _publish_membership_basis(
            session,
            member_node_id=node_ids["sandisk"],
            relation_id=old_relation,
            claim_id=old_claim,
        )

        invalid_relation = _evidence_relation(
            session,
            batch_id=batch_id,
            revision_id=revision_id,
            source_node_id=node_ids["hbf"],
            target_node_id=reference.node_id,
            key="unpublished-membership",
        )
        assert invalid_relation > 0

        recent = get_topic_exploration(
            session,
            reference.node_id,
            TimeWindow.RECENT_90_DAYS,
            now=NOW,
        )
        year = get_topic_exploration(
            session,
            reference.node_id,
            TimeWindow.RECENT_1_YEAR,
            now=NOW,
        )

        assert recent.total_public_membership_count == 2
        assert recent.recent_member_count == 1
        assert recent.recent_activity_evidence_group_count == 1
        assert year.total_public_membership_count == 2
        assert year.recent_member_count == 2
        assert year.recent_activity_evidence_group_count == 2
        assert {item.node_type.code for item in recent.graph.nodes} == {
            "TOPIC",
            "COMPANY",
        }
        assert len(recent.graph.nodes) == 2
        assert all(item.tier in {"CENTER", "DIRECT"} for item in recent.graph.nodes)
        assert {item.relation_id for item in recent.graph.relations} == {
            recent_relation,
        }
        assert len(year.graph.nodes) == 3
        assert {item.relation_id for item in year.graph.relations} == {
            recent_relation,
            old_relation,
        }

        general = get_exploration(
            session,
            node_ids["sk_hynix"],
            TimeWindow.RECENT_90_DAYS,
            now=NOW,
        )
        topic_node = next(
            item for item in general.graph.nodes if item.node_id == reference.node_id
        )
        assert topic_node.tier == "DIRECT"
        assert topic_node.name == "반도체"
        assert MAX_DIRECT_NODES == 24

        assert all(
            item.node_id != reference.node_id
            for item in search_nodes(session, "반도체", limit=20)
        )

        set_topic_reference_active(session, reference.node_id, is_active=False)
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(relation)
                .where(relation.c.relation_id.in_([recent_relation, old_relation]))
            )
            == 2
        )
        inactive = get_topic_exploration(
            session,
            reference.node_id,
            TimeWindow.RECENT_90_DAYS,
            now=NOW,
        )
        assert inactive.topic.is_active is False
        assert inactive.total_public_membership_count == 2

        with pytest.raises(sa.exc.DBAPIError), session.begin_nested():
            _evidence_relation(
                session,
                batch_id=batch_id,
                revision_id=revision_id,
                source_node_id=node_ids["ucie"],
                target_node_id=reference.node_id,
                key="inactive-target-must-fail",
            )
