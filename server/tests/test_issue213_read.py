from datetime import UTC, datetime
from hashlib import sha256

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import panel
from ontology_map.db import panel as panel_queries
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.ontology_reference_data import (
    activate_approved_ontology_reference_data,
)
from ontology_map.db.panel_fixture import load_panel_fixture
from ontology_map.db.schema import (
    claim,
    claim_observation,
    claim_relation,
    conflict_member,
    conflict_set,
    evidence_group,
    knowledge_item,
    observation,
    promotion_batch,
    publication_affected_node,
    relation,
    search_document_basis,
    source_document,
    topic_reference,
)
from ontology_map.db.session import get_engine
from ontology_map.exploration import TimeWindow
from ontology_map.relations import list_node_relations, list_relation_evidence

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _public_document_and_batch(session: Session, node_id: int) -> tuple[int, int]:
    row = session.execute(
        sa.select(
            publication_affected_node.c.node_search_document_id,
            promotion_batch.c.promotion_batch_id,
        )
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
    ).one()
    assert row.node_search_document_id is not None
    return int(row.node_search_document_id), int(row.promotion_batch_id)


def _observation(session: Session, key: str, text: str) -> int:
    group_id = int(
        session.execute(
            evidence_group.insert().returning(evidence_group.c.evidence_group_id)
        ).scalar_one()
    )
    document_id = int(
        session.execute(
            source_document.insert()
            .values(
                evidence_group_id=group_id,
                source_key=f"issue213:{key}",
                version_no=1,
                canonical_url=f"https://example.com/issue213/{key}",
                publisher_name="Issue 213 테스트",
                title=f"Issue 213 {key}",
                original_language="ko",
                normalized_body=text,
                body_hash=sha256(text.encode()).digest(),
                published_at=NOW,
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
                source_document_id=document_id,
                start_char=0,
                end_char=len(text),
                quote_text=text,
                quote_hash=sha256(text.encode()).digest(),
                paragraph_number=1,
                observed_at=NOW,
            )
            .returning(observation.c.observation_id)
        ).scalar_one()
    )


def _relation_claim(
    session: Session,
    *,
    relation_id: int,
    batch_id: int,
    document_id: int | None,
    stance: str,
    modality: str,
    key: str,
    observation_id: int | None = None,
) -> int:
    claim_id = int(
        session.execute(
            knowledge_item.insert()
            .values(
                item_kind="CLAIM",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    text = f"Issue 213 {key} Claim"
    session.execute(
        claim.insert().values(
            claim_id=claim_id,
            statement_text=text,
            language="ko",
            modality=modality,
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    session.execute(
        claim_relation.insert().values(
            claim_id=claim_id,
            relation_id=relation_id,
            stance=stance,
        )
    )
    if observation_id is None:
        observation_id = _observation(session, key, text)
    session.execute(
        claim_observation.insert().values(
            claim_id=claim_id,
            observation_id=observation_id,
        )
    )
    if document_id is not None:
        session.execute(
            search_document_basis.insert().values(
                node_search_document_id=document_id,
                knowledge_item_id=claim_id,
            )
        )
    return claim_id


def _setup_has_topic_fixture(
    session: Session, node_ids: dict[str, int]
) -> dict[str, int]:
    activation = activate_approved_ontology_reference_data(session)
    source_id = node_ids["sk_hynix"]
    topic_id = activation.topic_node_ids["SEMICONDUCTOR"]
    document_id, batch_id = _public_document_and_batch(session, source_id)
    relation_id = int(
        session.execute(
            knowledge_item.insert()
            .values(
                item_kind="RELATION",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    session.execute(
        relation.insert().values(
            relation_id=relation_id,
            source_node_id=source_id,
            target_node_id=topic_id,
            relation_type_revision_id=activation.relation_revision_ids["HAS_TOPIC"],
            relation_identity_key=sha256(
                f"issue213:{source_id}:{topic_id}".encode()
            ).digest(),
        )
    )
    session.execute(
        search_document_basis.insert().values(
            node_search_document_id=document_id,
            knowledge_item_id=relation_id,
        )
    )
    shared_observation_id = _observation(
        session, "shared", "Issue 213 shared relation observation"
    )
    support_claim_id = _relation_claim(
        session,
        relation_id=relation_id,
        batch_id=batch_id,
        document_id=document_id,
        stance="SUPPORT",
        modality="PREDICTION_OR_ESTIMATE",
        key="support",
        observation_id=shared_observation_id,
    )
    public_dispute_claim_id = _relation_claim(
        session,
        relation_id=relation_id,
        batch_id=batch_id,
        document_id=document_id,
        stance="DISPUTE",
        modality="OPINION_OR_EVALUATION",
        key="public-dispute",
        observation_id=shared_observation_id,
    )
    private_dispute_claim_id = _relation_claim(
        session,
        relation_id=relation_id,
        batch_id=batch_id,
        document_id=None,
        stance="DISPUTE",
        modality="PREDICTION_OR_ESTIMATE",
        key="private-dispute",
    )
    session.flush()
    return {
        "source_id": source_id,
        "topic_id": topic_id,
        "document_id": document_id,
        "batch_id": batch_id,
        "relation_id": relation_id,
        "support_claim_id": support_claim_id,
        "public_dispute_claim_id": public_dispute_claim_id,
        "private_dispute_claim_id": private_dispute_claim_id,
    }


def test_has_topic_generic_reads_use_product_reference_endpoint_without_ready() -> None:
    _created, node_ids = load_hbf_fixture()
    engine = get_engine()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection) as session:
                session.execute(sa.select(sa.literal(1)))
                fixture = _setup_has_topic_fixture(session, node_ids)
                source_id = fixture["source_id"]
                topic_id = fixture["topic_id"]
                relation_id = fixture["relation_id"]

                topic_item = session.execute(
                    sa.select(
                        knowledge_item.c.item_kind,
                        knowledge_item.c.lifecycle_kind,
                        knowledge_item.c.current_state,
                        knowledge_item.c.promotion_batch_id,
                    ).where(knowledge_item.c.knowledge_item_id == topic_id)
                ).one()
                assert topic_item.item_kind == "NODE"
                assert topic_item.lifecycle_kind == "PRODUCT_REFERENCE"
                assert topic_item.current_state is None
                assert topic_item.promotion_batch_id is None
                assert (
                    session.execute(
                        sa.select(topic_reference.c.is_active).where(
                            topic_reference.c.node_id == topic_id
                        )
                    ).scalar_one()
                    is True
                )
                ready_publication_count = int(
                    session.execute(
                        sa.select(sa.func.count())
                        .select_from(publication_affected_node)
                        .join(
                            promotion_batch,
                            promotion_batch.c.promotion_batch_id
                            == publication_affected_node.c.promotion_batch_id,
                        )
                        .where(
                            publication_affected_node.c.node_id == topic_id,
                            promotion_batch.c.promotion_status == "COMMITTED",
                            promotion_batch.c.publication_status == "READY",
                        )
                    ).scalar_one()
                )
                assert ready_publication_count == 0

                active_relations_page = list_node_relations(
                    session, source_id, cursor=None, limit=50
                )
                active_membership = next(
                    item
                    for item in active_relations_page.items
                    if item.relation_id == relation_id
                )
                assert active_membership.other_node.node_id == topic_id
                assert active_membership.other_node.name == "반도체"
                assert active_membership.other_node.node_type.code == "TOPIC"

                active_evidence_page = list_relation_evidence(
                    session, relation_id, cursor=None, limit=20
                )
                assert len(active_evidence_page.items) == 2
                assert {item.stance for item in active_evidence_page.items} == {
                    "SUPPORT",
                    "DISPUTE",
                }
                assert {item.modality for item in active_evidence_page.items} == {
                    "PREDICTION_OR_ESTIMATE",
                    "OPINION_OR_EVALUATION",
                }
                active_item_keys = {
                    item.item_key for item in active_evidence_page.items
                }
                assert len(active_item_keys) == 2
                assert all(len(item_key) == 64 for item_key in active_item_keys)

                session.execute(
                    topic_reference.update()
                    .where(topic_reference.c.node_id == topic_id)
                    .values(is_active=False)
                )
                session.flush()
                assert (
                    session.execute(
                        sa.select(topic_reference.c.is_active).where(
                            topic_reference.c.node_id == topic_id
                        )
                    ).scalar_one()
                    is False
                )

                relations_page = list_node_relations(
                    session, source_id, cursor=None, limit=50
                )
                membership = next(
                    item
                    for item in relations_page.items
                    if item.relation_id == relation_id
                )
                assert membership.other_node.node_id == topic_id
                assert membership.other_node.name == "반도체"
                assert membership.other_node.node_type.code == "TOPIC"

                evidence_page = list_relation_evidence(
                    session, relation_id, cursor=None, limit=20
                )
                assert len(evidence_page.items) == 2
                assert {
                    item.item_key for item in evidence_page.items
                } == active_item_keys
                assert {item.stance for item in evidence_page.items} == {
                    "SUPPORT",
                    "DISPUTE",
                }
                assert {item.modality for item in evidence_page.items} == {
                    "PREDICTION_OR_ESTIMATE",
                    "OPINION_OR_EVALUATION",
                }
                assert {
                    item.item_key
                    for item in list_relation_evidence(
                        session, relation_id, cursor=None, limit=20
                    ).items
                } == active_item_keys
        finally:
            outer.rollback()


def test_claim_connections_project_relation_and_hide_non_public_conflict() -> None:
    _created, node_ids = load_hbf_fixture()
    engine = get_engine()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection) as session:
                session.execute(sa.select(sa.literal(1)))
                fixture = _setup_has_topic_fixture(session, node_ids)
                source_id = fixture["source_id"]
                topic_id = fixture["topic_id"]
                relation_id = fixture["relation_id"]
                support_claim_id = fixture["support_claim_id"]
                private_dispute_claim_id = fixture["private_dispute_claim_id"]
                conflict_id = int(
                    session.execute(
                        conflict_set.insert()
                        .values(
                            relation_id=relation_id,
                            modality="PREDICTION_OR_ESTIMATE",
                            current_state="AGENT_PROPOSED",
                            created_at=NOW,
                        )
                        .returning(conflict_set.c.conflict_set_id)
                    ).scalar_one()
                )
                session.execute(
                    conflict_member.insert(),
                    [
                        {
                            "conflict_set_id": conflict_id,
                            "claim_id": support_claim_id,
                            "position_key": "support",
                        },
                        {
                            "conflict_set_id": conflict_id,
                            "claim_id": private_dispute_claim_id,
                            "position_key": "dispute",
                        },
                    ],
                )
                session.flush()

                claims_page = panel.list_claims(
                    session,
                    source_id,
                    TimeWindow.RECENT_90_DAYS,
                    cursor=None,
                    limit=50,
                )
                projected = next(
                    item
                    for item in claims_page["items"]
                    if int(item["claim_id"]) == support_claim_id
                )
                relation_connection = next(
                    item
                    for item in projected["connections"]
                    if item["kind"] == "RELATION"
                    and item["target_id"] == str(relation_id)
                )
                relation_projection = relation_connection["relation"]
                assert relation_projection is not None
                assert relation_projection["relation_id"] == str(relation_id)
                assert relation_projection["display_name"] == "HAS_TOPIC"
                assert relation_projection["directionality"] == "DIRECTED"
                assert relation_projection["source_node"]["node_id"] == str(source_id)
                assert (
                    relation_projection["source_node"]["node_type"]["code"] == "COMPANY"
                )
                assert relation_projection["target_node"] == {
                    "node_id": str(topic_id),
                    "name": "반도체",
                    "node_type": {"code": "TOPIC", "display_name": "주제"},
                }
                assert (
                    relation_projection["other_node"]
                    == relation_projection["target_node"]
                )
                assert relation_projection["stance"] == "SUPPORT"
                assert not any(
                    item["kind"] == "CONFLICT" for item in projected["connections"]
                )

                raw_connections = panel_queries.claim_connections(
                    session,
                    {
                        "node_id": source_id,
                        "basis_ids": [support_claim_id, relation_id, source_id],
                        "start_at": NOW,
                        "as_of_at": NOW,
                        "claim_id": support_claim_id,
                    },
                )
                assert not any(row["kind"] == "CONFLICT" for row in raw_connections)
        finally:
            outer.rollback()


def test_nested_question_and_report_claims_reuse_canonical_connections() -> None:
    _created, node_ids = load_panel_fixture()
    with Session(get_engine()) as session:
        questions = panel.list_questions(
            session,
            node_ids["gaon"],
            TimeWindow.RECENT_90_DAYS,
            cursor=None,
        )
        assert questions["items"]
        answer = panel.read_question(session, int(questions["items"][0]["question_id"]))
        assert answer["claims"]
        relation_claim = next(
            item
            for item in answer["claims"]
            if any(
                connection["kind"] == "RELATION" for connection in item["connections"]
            )
        )
        relation_connection = next(
            connection
            for connection in relation_claim["connections"]
            if connection["kind"] == "RELATION"
        )
        question_relation = relation_connection["relation"]
        assert question_relation is not None
        assert question_relation["relation_id"]
        assert question_relation["other_node"]["node_id"]
        assert question_relation["stance"] in {"SUPPORT", "DISPUTE"}
        assert question_relation["directionality"] in {"DIRECTED", "SYMMETRIC"}

        report_page = panel.read_report(
            session,
            node_ids["gaon"],
            TimeWindow.RECENT_90_DAYS,
            detail=True,
        )
        report = report_page["items"][0]
        nested = [
            claim_item
            for section in report["sections"]
            for claim_item in section["claims"]
        ]
        assert nested
        report_relation_claim = next(
            item
            for item in nested
            if any(
                connection["kind"] == "RELATION" for connection in item["connections"]
            )
        )
        report_relation = next(
            connection["relation"]
            for connection in report_relation_claim["connections"]
            if connection["kind"] == "RELATION"
        )
        assert report_relation is not None
        assert report_relation["relation_id"]
        assert report_relation["other_node"]["node_id"]
        assert report_relation["stance"] in {"SUPPORT", "DISPUTE"}
        assert report_relation["directionality"] in {"DIRECTED", "SYMMETRIC"}
