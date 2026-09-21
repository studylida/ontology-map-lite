"""Reference Topic x initial-publication integration regressions for Track A."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import followup_generation as followup_product
from ontology_map import insight_generation as insight_product
from ontology_map import node_context_generation as context_product
from ontology_map import topic_api
from ontology_map.db import initial_publication as publication
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.initial_publication_search import build_search_document_snapshot
from ontology_map.db.ontology_reference_data import (
    activate_approved_ontology_reference_data,
)
from ontology_map.db.session import get_engine
from ontology_map.db.topic_references import set_topic_reference_active
from ontology_map.exploration import TimeWindow
from ontology_map.followup_generation_contracts import (
    FollowupQuestionsProposal,
    FollowupWindowProposal,
)
from ontology_map.initial_publication_coordinator import run_initial_publication
from ontology_map.insight_generation_contracts import (
    InsightBundleProposal,
    InsightWindowProposal,
)
from ontology_map.node_context_generation_contracts import NodeContextProposal

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


def _truncate() -> None:
    names = ", ".join(f'"{table.name}"' for table in s.metadata.sorted_tables)
    with _engine().begin() as connection:
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def _isolate_database() -> None:
    if not DATABASE_URL:
        yield
        return
    _truncate()
    yield
    _truncate()


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


def _activate_contract(
    session: Session,
    task_kind: str,
    schema_json: dict[str, object],
) -> None:
    session.execute(
        s.output_schema_definition.update()
        .where(
            s.output_schema_definition.c.task_kind == task_kind,
            s.output_schema_definition.c.is_active,
        )
        .values(is_active=False)
    )
    version = int(
        session.scalar(
            sa.select(
                sa.func.coalesce(
                    sa.func.max(s.output_schema_definition.c.version_no),
                    0,
                )
            ).where(s.output_schema_definition.c.task_kind == task_kind)
        )
        or 0
    )
    session.execute(
        s.output_schema_definition.insert().values(
            task_kind=task_kind,
            version_no=version + 1,
            schema_json=schema_json,
            is_active=True,
        )
    )


def _activate_generation_contracts(session: Session) -> None:
    _activate_contract(session, "NODE_CONTEXT", context_product.output_schema())
    _activate_contract(
        session,
        "FOLLOWUP_QUESTIONS",
        followup_product.output_schema(),
    )
    _activate_contract(session, "NODE_INSIGHT", insight_product.output_schema())


def _observation(session: Session, *, key: str, days_ago: int) -> int:
    marker = uuid4().hex
    body = f"{key} evidence {marker}"
    group_id = session.scalar(
        s.evidence_group.insert().returning(s.evidence_group.c.evidence_group_id)
    )
    assert group_id is not None
    published_at = datetime.now(UTC) - timedelta(days=days_ago)
    document_id = session.scalar(
        s.source_document.insert()
        .values(
            evidence_group_id=group_id,
            source_key=f"topic-publication:{key}:{marker}",
            version_no=1,
            canonical_url=f"https://example.com/topic-publication/{key}/{marker}",
            publisher_name="Track A Topic publication regression",
            title=f"{key} membership evidence",
            original_language="ko",
            normalized_body=body,
            body_hash=sha256(body.encode()).digest(),
            published_at=published_at,
            published_precision="DAY",
            modified_precision="UNKNOWN",
            last_checked_at=datetime.now(UTC),
            last_check_status="SUCCESS",
        )
        .returning(s.source_document.c.source_document_id)
    )
    assert document_id is not None
    observation_id = session.scalar(
        s.observation.insert()
        .values(
            source_document_id=document_id,
            start_char=0,
            end_char=len(body),
            quote_text=body,
            quote_hash=sha256(body.encode()).digest(),
            paragraph_number=1,
            observed_at=datetime.now(UTC),
        )
        .returning(s.observation.c.observation_id)
    )
    assert observation_id is not None
    return int(observation_id)


def _membership_knowledge(
    session: Session,
    *,
    batch_id: int,
    revision_id: int,
    member_node_id: int,
    topic_node_id: int,
    key: str,
    days_ago: int,
) -> tuple[int, int]:
    relation_id = session.scalar(
        s.knowledge_item.insert()
        .values(
            item_kind="RELATION",
            lifecycle_kind="EVIDENCE_BACKED",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(s.knowledge_item.c.knowledge_item_id)
    )
    assert relation_id is not None
    relation_id = int(relation_id)
    session.execute(
        s.relation.insert().values(
            relation_id=relation_id,
            source_node_id=member_node_id,
            target_node_id=topic_node_id,
            relation_type_revision_id=revision_id,
            relation_identity_key=sha256(
                f"HAS_TOPIC:{member_node_id}:{topic_node_id}:{key}".encode()
            ).digest(),
        )
    )

    claim_id = session.scalar(
        s.knowledge_item.insert()
        .values(
            item_kind="CLAIM",
            lifecycle_kind="EVIDENCE_BACKED",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(s.knowledge_item.c.knowledge_item_id)
    )
    assert claim_id is not None
    claim_id = int(claim_id)
    session.execute(
        s.claim.insert().values(
            claim_id=claim_id,
            statement_text=f"{key}는 반도체 Topic에 직접 연결된다.",
            language="ko",
            modality="FACT",
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    assert provenance.add_claim_relation(
        session,
        batch_id,
        claim_id,
        relation_id,
        "SUPPORT",
    )
    assert provenance.add_claim_observation(
        session,
        batch_id,
        claim_id,
        _observation(session, key=key, days_ago=days_ago),
    )
    return relation_id, claim_id


def _new_member_node(session: Session, batch_id: int) -> int:
    node_type_id = session.scalar(
        sa.select(s.node_type.c.node_type_id).where(
            s.node_type.c.node_type_code == "COMPANY"
        )
    )
    assert node_type_id is not None
    node_id = session.scalar(
        s.knowledge_item.insert()
        .values(
            item_kind="NODE",
            lifecycle_kind="EVIDENCE_BACKED",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(s.knowledge_item.c.knowledge_item_id)
    )
    assert node_id is not None
    session.execute(s.node.insert().values(node_id=node_id, node_type_id=node_type_id))
    alias_id = session.scalar(
        s.node_alias.insert()
        .values(
            node_id=node_id, alias_text="새 발행 대상", language="ko", is_preferred=True
        )
        .returning(s.node_alias.c.node_alias_id)
    )
    assert alias_id is not None
    assert provenance.add_node_alias_evidence(
        session,
        batch_id,
        int(alias_id),
        _observation(session, key="새 발행 대상", days_ago=10),
    )
    return int(node_id)


def _providers(captured: dict[str, list[object]]):
    def context(prepared):
        captured["context"].append(prepared)

        def send():
            return NodeContextProposal(
                context_text="검증된 직접 관계만 사용하는 테스트 NODE_CONTEXT입니다."
            )

        return send

    def followup(prepared):
        captured["followup"].append(prepared)

        def send():
            return FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            )

        return send

    def insight(prepared):
        captured["insight"].append(prepared)

        def send():
            return InsightBundleProposal(
                recent_90_days=InsightWindowProposal(report=None),
                recent_1_year=InsightWindowProposal(report=None),
            )

        return send

    return context, followup, insight


def _count(session: Session, table: sa.Table, *conditions: object) -> int:
    statement = sa.select(sa.func.count()).select_from(table)
    if conditions:
        statement = statement.where(*conditions)
    return int(session.scalar(statement) or 0)


def _topic_connections(claims: object, topic_node_id: int) -> list[object]:
    result: list[object] = []
    for claim in claims:  # type: ignore[union-attr]
        for connection in claim.connections:
            related = connection.related_node
            if related is not None and related.node_id == topic_node_id:
                result.append(connection)
    return result


def test_reference_topic_publication_has_no_topic_artifacts() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        activation = activate_approved_ontology_reference_data(session)
    _created, nodes = load_hbf_fixture()

    topic_node_id = activation.topic_node_ids["SEMICONDUCTOR"]
    has_topic_revision_id = activation.relation_revision_ids["HAS_TOPIC"]
    member_ids = (nodes["sk_hynix"], nodes["sandisk"])

    with Session(engine) as session:
        prior_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )

    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _batch(session)
        first_relation, first_claim = _membership_knowledge(
            session,
            batch_id=batch_id,
            revision_id=has_topic_revision_id,
            member_node_id=member_ids[0],
            topic_node_id=topic_node_id,
            key="SK하이닉스",
            days_ago=30,
        )
        second_relation, second_claim = _membership_knowledge(
            session,
            batch_id=batch_id,
            revision_id=has_topic_revision_id,
            member_node_id=member_ids[1],
            topic_node_id=topic_node_id,
            key="SanDisk",
            days_ago=180,
        )
        provenance.mark_promotion_committed(session, batch_id)

    captured: dict[str, list[object]] = {
        "context": [],
        "followup": [],
        "insight": [],
    }
    context_provider, followup_provider, insight_provider = _providers(captured)
    first = run_initial_publication(
        engine,
        batch_id,
        "topic-publication-track-a",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    )

    assert first.ready
    assert first.affected_node_ids == tuple(sorted(member_ids))
    assert topic_node_id not in first.affected_node_ids
    assert len(captured["context"]) == 2
    assert len(captured["followup"]) == 2
    assert len(captured["insight"]) == 2

    relation_claims = {
        member_ids[0]: (first_relation, first_claim),
        member_ids[1]: (second_relation, second_claim),
    }
    with Session(engine) as session:
        reference_state = session.execute(
            sa.select(
                s.knowledge_item.c.lifecycle_kind,
                s.knowledge_item.c.current_state,
                s.knowledge_item.c.promotion_batch_id,
            ).where(s.knowledge_item.c.knowledge_item_id == topic_node_id)
        ).one()
        assert reference_state == ("PRODUCT_REFERENCE", None, None)
        assert (
            _count(
                session,
                s.publication_affected_node,
                s.publication_affected_node.c.node_id == topic_node_id,
            )
            == 0
        )
        assert (
            _count(
                session,
                s.node_search_document,
                s.node_search_document.c.node_id == topic_node_id,
            )
            == 0
        )
        assert (
            _count(
                session,
                s.node_context,
                s.node_context.c.node_id == topic_node_id,
            )
            == 0
        )
        assert (
            _count(
                session,
                s.node_insight,
                s.node_insight.c.node_id == topic_node_id,
            )
            == 0
        )
        assert (
            _count(
                session,
                s.node_insight_window,
                s.node_insight_window.c.node_id == topic_node_id,
            )
            == 0
        )
        assert (
            _count(
                session,
                s.node_alias,
                s.node_alias.c.node_id == topic_node_id,
            )
            == 0
        )

        for member_node_id, (relation_id, claim_id) in relation_claims.items():
            document_id = session.scalar(
                sa.select(s.publication_affected_node.c.node_search_document_id).where(
                    s.publication_affected_node.c.promotion_batch_id == batch_id,
                    s.publication_affected_node.c.node_id == member_node_id,
                )
            )
            assert document_id is not None
            basis = set(
                int(value)
                for value in session.scalars(
                    sa.select(s.search_document_basis.c.knowledge_item_id).where(
                        s.search_document_basis.c.node_search_document_id == document_id
                    )
                )
            )
            assert relation_id in basis
            assert claim_id in basis
            assert topic_node_id not in basis
            knowledge_text = session.scalar(
                sa.select(s.node_search_document.c.knowledge_text).where(
                    s.node_search_document.c.node_search_document_id == document_id
                )
            )
            assert knowledge_text is not None
            assert "반도체" in str(knowledge_text)

        api_recent = topic_api.read_topic_exploration(
            str(topic_node_id),
            TimeWindow.RECENT_90_DAYS,
            session,
        )
        api_year = topic_api.read_topic_exploration(
            str(topic_node_id),
            TimeWindow.RECENT_1_YEAR,
            session,
        )
        assert api_recent.total_public_membership_count == 2
        assert api_recent.recent_member_count == 1
        assert api_year.total_public_membership_count == 2
        assert api_year.recent_member_count == 2
        assert {
            int(node.node_id)
            for node in api_recent.graph.nodes
            if node.node_type.code != "TOPIC"
        } == {member_ids[0]}
        assert {
            int(node.node_id)
            for node in api_year.graph.nodes
            if node.node_type.code != "TOPIC"
        } == set(member_ids)

    for prepared in captured["followup"]:
        assert _topic_connections(
            prepared.agent_input.recent_90_days.claims, topic_node_id
        )
        assert _topic_connections(
            prepared.agent_input.recent_1_year.claims, topic_node_id
        )
        if prepared.agent_input.node_id == member_ids[1]:
            recent_claim = next(
                claim
                for claim in prepared.agent_input.recent_90_days.claims
                if claim.claim_id == second_claim
            )
            year_claim = next(
                claim
                for claim in prepared.agent_input.recent_1_year.claims
                if claim.claim_id == second_claim
            )
            assert recent_claim.period_role == "BACKGROUND"
            assert year_claim.period_role == "IN_WINDOW"
    for prepared in captured["insight"]:
        assert _topic_connections(
            prepared.agent_input.recent_90_days.claims,
            topic_node_id,
        )
        assert _topic_connections(
            prepared.agent_input.recent_1_year.claims,
            topic_node_id,
        )

    send_counts = {key: len(value) for key, value in captured.items()}
    with Session(engine) as session:
        artifact_counts = (
            _count(session, s.node_search_document),
            _count(session, s.node_context),
            _count(session, s.node_question_set),
            _count(session, s.node_insight_window),
        )
        frozen_membership = tuple(
            int(value)
            for value in session.scalars(
                sa.select(s.publication_affected_node.c.node_id)
                .where(s.publication_affected_node.c.promotion_batch_id == batch_id)
                .order_by(s.publication_affected_node.c.node_id)
            )
        )

    second = run_initial_publication(
        engine,
        batch_id,
        "topic-publication-track-a-reentry",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    )
    assert second.ready
    assert second.affected_node_ids == frozen_membership
    assert {key: len(value) for key, value in captured.items()} == send_counts

    with Session(engine) as session:
        assert artifact_counts == (
            _count(session, s.node_search_document),
            _count(session, s.node_context),
            _count(session, s.node_question_set),
            _count(session, s.node_insight_window),
        )
        current_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
        assert prior_ready <= current_ready

    with Session(engine) as session, session.begin():
        set_topic_reference_active(session, topic_node_id, is_active=False)
    with Session(engine) as session:
        inactive = topic_api.read_topic_exploration(
            str(topic_node_id),
            TimeWindow.RECENT_90_DAYS,
            session,
        )
        assert inactive.topic.is_active is False
        assert inactive.total_public_membership_count == 2
        assert (
            first_relation
            in build_search_document_snapshot(
                session, promotion_batch_id=batch_id, node_id=member_ids[0]
            ).basis_ids
        )


def test_new_member_first_publication_keeps_topic_as_identity_only() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        activation = activate_approved_ontology_reference_data(session)
    load_hbf_fixture()
    topic_node_id = activation.topic_node_ids["SEMICONDUCTOR"]

    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _batch(session)
        member_node_id = _new_member_node(session, batch_id)
        relation_id, claim_id = _membership_knowledge(
            session,
            batch_id=batch_id,
            revision_id=activation.relation_revision_ids["HAS_TOPIC"],
            member_node_id=member_node_id,
            topic_node_id=topic_node_id,
            key="새 발행 대상",
            days_ago=20,
        )
        provenance.mark_promotion_committed(session, batch_id)

    captured: dict[str, list[object]] = {"context": [], "followup": [], "insight": []}
    context_provider, followup_provider, insight_provider = _providers(captured)
    result = run_initial_publication(
        engine,
        batch_id,
        "topic-publication-track-a-new",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    )
    assert result.ready
    assert result.affected_node_ids == (member_node_id,)
    assert len(captured["context"]) == 1
    assert len(captured["followup"]) == 1
    assert len(captured["insight"]) == 0
    assert all(
        _topic_connections(prepared.agent_input.recent_90_days.claims, topic_node_id)
        and _topic_connections(prepared.agent_input.recent_1_year.claims, topic_node_id)
        for prepared in captured["followup"]
    )
    assert all(
        _topic_connections(prepared.agent_input.recent_90_days.claims, topic_node_id)
        and _topic_connections(prepared.agent_input.recent_1_year.claims, topic_node_id)
        for prepared in captured["insight"]
    )
    with Session(engine) as session:
        basis = set(
            build_search_document_snapshot(
                session, promotion_batch_id=batch_id, node_id=member_node_id
            ).basis_ids
        )
        assert {relation_id, claim_id} <= basis
        assert topic_node_id not in basis
        assert (
            _count(
                session,
                s.publication_affected_node,
                s.publication_affected_node.c.node_id == topic_node_id,
            )
            == 0
        )


def test_has_topic_without_usable_support_claim_is_not_publication_basis() -> None:
    engine = _engine()
    with Session(engine) as session, session.begin():
        activation = activate_approved_ontology_reference_data(session)
    load_hbf_fixture()
    with Session(engine) as session, session.begin():
        batch_id = _batch(session)
        member_node_id = _new_member_node(session, batch_id)
        relation_id, claim_id = _membership_knowledge(
            session,
            batch_id=batch_id,
            revision_id=activation.relation_revision_ids["HAS_TOPIC"],
            member_node_id=member_node_id,
            topic_node_id=activation.topic_node_ids["SEMICONDUCTOR"],
            key="근거 없음",
            days_ago=10,
        )
        provenance.mark_promotion_committed(session, batch_id)
    with Session(engine) as session, session.begin():
        started = publication.start_initial_publication(session, batch_id)
        assert started.affected_node_ids == (member_node_id,)
        publication.ensure_search_document(
            session, promotion_batch_id=batch_id, node_id=member_node_id
        )
    with Session(engine) as session, session.begin():
        session.execute(
            s.claim_relation.update()
            .where(
                s.claim_relation.c.claim_id == claim_id,
                s.claim_relation.c.relation_id == relation_id,
            )
            .values(stance="DISPUTE")
        )
    with Session(engine) as session:
        basis = set(
            build_search_document_snapshot(
                session, promotion_batch_id=batch_id, node_id=member_node_id
            ).basis_ids
        )
        assert relation_id not in basis
        assert claim_id not in basis
        with pytest.raises(publication.NodeContextTaskError, match="stale"):
            publication.prepare_node_context(
                session, promotion_batch_id=batch_id, node_id=member_node_id
            )
