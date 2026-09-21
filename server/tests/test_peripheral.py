import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import quote, urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from starlette.types import Message, Scope

import ontology_map.api as api_module
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.schema import (
    claim,
    claim_observation,
    claim_relation,
    knowledge_item,
    node,
    node_alias,
    node_search_document,
    observation,
    promotion_batch,
    publication_affected_node,
    relation,
    relation_type_revision,
    search_document_basis,
)
from ontology_map.db.session import get_engine
from ontology_map.exploration import (
    Graph,
    GraphNode,
    GraphRelation,
    NodeType,
    PeripheralPage,
    PublicationNotReadyError,
    TimeWindow,
    list_peripheral_nodes,
)
from ontology_map.main import app
from ontology_map.pagination import InvalidCursorError, encode_cursor

NOW = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


@contextmanager
def rollback_session() -> Iterator[Session]:
    _, base_ids = load_hbf_fixture()
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection) as session:
                # 개발 검토 자료와 독립된 공개 범위를 transaction 안에서만 구성한다.
                session.execute(
                    knowledge_item.update()
                    .where(
                        knowledge_item.c.item_kind == "NODE",
                        knowledge_item.c.knowledge_item_id.not_in(
                            list(base_ids.values())
                        ),
                    )
                    .values(current_state="ON_HOLD")
                )
                yield session
        finally:
            transaction.rollback()


async def asgi_get(target: str) -> tuple[int, dict[str, Any]]:
    parsed = urlsplit(target)
    messages: list[Message] = []
    request_sent = False

    async def receive() -> Message:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode(),
        "query_string": parsed.query.encode(),
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    await app(scope, receive, send)
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(body)


def request(target: str) -> tuple[int, dict[str, Any]]:
    return asyncio.run(asgi_get(target))


def insert_public_node(session: Session, name: str) -> tuple[int, int]:
    batch_id = session.scalar(
        sa.select(promotion_batch.c.promotion_batch_id)
        .where(promotion_batch.c.publication_status == "READY")
        .order_by(
            promotion_batch.c.ready_at.desc(),
            promotion_batch.c.promotion_batch_id.desc(),
        )
        .limit(1)
    )
    node_type_id = session.scalar(sa.select(node.c.node_type_id).limit(1))
    assert batch_id is not None
    assert node_type_id is not None
    node_id = session.execute(
        knowledge_item.insert()
        .values(
            item_kind="NODE",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(knowledge_item.c.knowledge_item_id)
    ).scalar_one()
    session.execute(node.insert().values(node_id=node_id, node_type_id=node_type_id))
    session.execute(
        node_alias.insert().values(
            node_id=node_id,
            alias_text=name,
            language="ko",
            is_preferred=True,
        )
    )
    search_document_id = session.execute(
        node_search_document.insert()
        .values(
            node_id=node_id,
            identity_text=name,
            knowledge_text=f"{name}의 공개 지식",
            input_hash=sha256(f"peripheral-search:{node_id}".encode()).digest(),
            generator_version="integration-test-v1",
        )
        .returning(node_search_document.c.node_search_document_id)
    ).scalar_one()
    session.execute(
        search_document_basis.insert().values(
            node_search_document_id=search_document_id,
            knowledge_item_id=node_id,
        )
    )
    session.execute(
        publication_affected_node.insert().values(
            promotion_batch_id=batch_id,
            node_id=node_id,
            node_search_document_id=search_document_id,
        )
    )
    return int(node_id), int(search_document_id)


def insert_public_relation(
    session: Session,
    owner_search_document_id: int,
    source_node_id: int,
    target_node_id: int,
) -> int:
    batch_id = session.scalar(
        sa.select(promotion_batch.c.promotion_batch_id)
        .where(promotion_batch.c.publication_status == "READY")
        .order_by(
            promotion_batch.c.ready_at.desc(),
            promotion_batch.c.promotion_batch_id.desc(),
        )
        .limit(1)
    )
    relation_type_revision_id = session.scalar(
        sa.select(relation.c.relation_type_revision_id).limit(1)
    )
    observation_id = session.scalar(sa.select(observation.c.observation_id).limit(1))
    assert batch_id is not None
    assert relation_type_revision_id is not None
    assert observation_id is not None

    source_node_id, target_node_id = sorted((source_node_id, target_node_id))
    relation_id = session.execute(
        knowledge_item.insert()
        .values(
            item_kind="RELATION",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(knowledge_item.c.knowledge_item_id)
    ).scalar_one()
    session.execute(
        relation.insert().values(
            relation_id=relation_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type_revision_id=relation_type_revision_id,
            relation_identity_key=sha256(
                f"peripheral-relation:{relation_id}".encode()
            ).digest(),
        )
    )
    claim_id = session.execute(
        knowledge_item.insert()
        .values(
            item_kind="CLAIM",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(knowledge_item.c.knowledge_item_id)
    ).scalar_one()
    session.execute(
        claim.insert().values(
            claim_id=claim_id,
            statement_text="주변부 Relation 통합 테스트 Claim",
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
    session.execute(
        search_document_basis.insert(),
        [
            {
                "node_search_document_id": owner_search_document_id,
                "knowledge_item_id": relation_id,
            },
            {
                "node_search_document_id": owner_search_document_id,
                "knowledge_item_id": claim_id,
            },
        ],
    )
    return int(relation_id)


@pytest.mark.parametrize("directionality", ["DIRECTED", "SYMMETRIC"])
def test_repository_pages_nodes_and_returns_actual_page_relations(
    directionality: str,
) -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        session.execute(
            relation_type_revision.update().values(directionality=directionality)
        )
        first_id, first_document_id = insert_public_node(session, "주변부 1")
        second_id, second_document_id = insert_public_node(session, "주변부 2")
        third_id, third_document_id = insert_public_node(session, "주변부 3")
        first_relation_id = insert_public_relation(
            session,
            first_document_id,
            node_ids["sk_hynix"],
            first_id,
        )
        second_relation_id = insert_public_relation(
            session,
            second_document_id,
            node_ids["hbf"],
            second_id,
        )
        page_relation_id = insert_public_relation(
            session,
            first_document_id,
            first_id,
            second_id,
        )

        cross_page_relation_id = insert_public_relation(
            session, third_document_id, first_id, third_id
        )
        first = list_peripheral_nodes(
            session,
            node_ids["sk_hynix"],
            TimeWindow.RECENT_90_DAYS,
            cursor=None,
            limit=2,
            now=NOW,
        )
        repeated = list_peripheral_nodes(
            session,
            node_ids["sk_hynix"],
            TimeWindow.RECENT_90_DAYS,
            cursor=None,
            limit=2,
            now=NOW,
        )
        assert first.next_cursor is not None
        second = list_peripheral_nodes(
            session,
            node_ids["sk_hynix"],
            TimeWindow.RECENT_90_DAYS,
            cursor=first.next_cursor,
            limit=2,
            now=NOW,
        )
        empty = list_peripheral_nodes(
            session,
            node_ids["sk_hynix"],
            TimeWindow.RECENT_90_DAYS,
            cursor=encode_cursor(
                "exploration_peripheral",
                {
                    "center_node_id": node_ids["sk_hynix"],
                    "time_window": "RECENT_90_DAYS",
                },
                [third_id],
            ),
            limit=2,
            now=NOW,
        )

    assert all(item.directionality == directionality for item in first.graph.relations)
    assert first == repeated
    assert [item.node_id for item in first.graph.nodes] == [first_id, second_id]
    assert all(item.tier == "AMBIENT" for item in first.graph.nodes)
    assert [item.relation_id for item in first.graph.relations] == [
        first_relation_id,
        second_relation_id,
        page_relation_id,
    ]
    assert [item.node_id for item in second.graph.nodes] == [third_id]
    assert [item.relation_id for item in second.graph.relations] == [
        cross_page_relation_id
    ]
    assert second.next_cursor is None
    assert empty == PeripheralPage(
        graph=Graph(nodes=[], relations=[]), next_cursor=None
    )


def test_repository_rechecks_public_state_between_pages() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        first_id, first_document_id = insert_public_node(session, "상태 주변부 1")
        second_id, second_document_id = insert_public_node(session, "상태 주변부 2")
        third_id, third_document_id = insert_public_node(session, "상태 주변부 3")
        for node_id, document_id in (
            (first_id, first_document_id),
            (second_id, second_document_id),
            (third_id, third_document_id),
        ):
            insert_public_relation(session, document_id, node_ids["sk_hynix"], node_id)
        first = list_peripheral_nodes(
            session,
            node_ids["sk_hynix"],
            TimeWindow.RECENT_1_YEAR,
            cursor=None,
            limit=1,
            now=NOW,
        )
        session.execute(
            knowledge_item.update()
            .where(knowledge_item.c.knowledge_item_id == second_id)
            .values(current_state="ON_HOLD")
        )
        assert first.next_cursor is not None
        second = list_peripheral_nodes(
            session,
            node_ids["sk_hynix"],
            TimeWindow.RECENT_1_YEAR,
            cursor=first.next_cursor,
            limit=2,
            now=NOW,
        )

    assert [item.node_id for item in first.graph.nodes] == [first_id]
    assert [item.node_id for item in second.graph.nodes] == [third_id]


def test_repository_rejects_cursor_for_another_scope() -> None:
    _created, node_ids = load_hbf_fixture()
    cursor = encode_cursor(
        "exploration_peripheral",
        {
            "center_node_id": node_ids["sk_hynix"],
            "time_window": "RECENT_90_DAYS",
        },
        [node_ids["hbf"]],
    )
    with Session(get_engine()) as session:
        with pytest.raises(InvalidCursorError):
            list_peripheral_nodes(
                session,
                node_ids["hbf"],
                TimeWindow.RECENT_90_DAYS,
                cursor=cursor,
                limit=20,
                now=NOW,
            )
        with pytest.raises(InvalidCursorError):
            list_peripheral_nodes(
                session,
                node_ids["sk_hynix"],
                TimeWindow.RECENT_1_YEAR,
                cursor=cursor,
                limit=20,
                now=NOW,
            )


def test_http_contract_returns_only_approved_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeripheralPage(
        graph=Graph(
            nodes=[
                GraphNode(
                    node_id=9_007_199_254_740_993,
                    name="주변부 Node",
                    node_type=NodeType(code="TOPIC", display_name="주제"),
                    tier="AMBIENT",
                    activity_evidence_group_count=0,
                )
            ],
            relations=[
                GraphRelation(
                    relation_id=9_007_199_254_740_995,
                    source_node_id=1,
                    target_node_id=9_007_199_254_740_993,
                    relation_type_display_name="공개 관계",
                    directionality="DIRECTED",
                    supporting_evidence_group_count=2,
                    has_conflict=False,
                )
            ],
        ),
        next_cursor="next-page",
    )
    monkeypatch.setattr(
        api_module, "list_peripheral_nodes", lambda *_args, **_kwargs: page
    )

    status, body = request(
        "/api/v1/exploration/1/peripheral?time_window=RECENT_90_DAYS&limit=1"
    )

    assert status == 200
    assert set(body) == {"graph", "next_cursor"}
    assert set(body["graph"]) == {"nodes", "relations"}
    assert set(body["graph"]["nodes"][0]) == {
        "node_id",
        "name",
        "node_type",
        "tier",
        "activity_evidence_group_count",
    }
    assert set(body["graph"]["relations"][0]) == {
        "relation_id",
        "source_node_id",
        "target_node_id",
        "relation_type_display_name",
        "directionality",
        "supporting_evidence_group_count",
        "has_conflict",
    }
    assert body["graph"]["nodes"][0]["node_id"] == "9007199254740993"
    assert body["graph"]["relations"][0]["relation_id"] == "9007199254740995"
    assert body["graph"]["relations"][0]["directionality"] == "DIRECTED"
    assert body["next_cursor"] == "next-page"


@pytest.mark.parametrize(
    "target",
    [
        "/api/v1/exploration/1/peripheral?time_window=RECENT_90_DAYS&limit=0",
        "/api/v1/exploration/1/peripheral?time_window=RECENT_90_DAYS&limit=51",
        "/api/v1/exploration/1/peripheral?time_window=RECENT_30_DAYS",
        "/api/v1/exploration/not-an-id/peripheral?time_window=RECENT_90_DAYS",
        "/api/v1/exploration/1/peripheral?time_window=RECENT_90_DAYS&cursor=not-base64!",
    ],
)
def test_http_contract_rejects_invalid_request(target: str) -> None:
    load_hbf_fixture()
    status, body = request(target)
    assert status == 422
    assert body == {"error": {"code": "INVALID_REQUEST", "retryable": False}}


def test_http_contract_rejects_cursor_for_another_scope() -> None:
    _created, node_ids = load_hbf_fixture()
    cursor = encode_cursor(
        "exploration_peripheral",
        {
            "center_node_id": node_ids["sk_hynix"],
            "time_window": "RECENT_90_DAYS",
        },
        [node_ids["hbf"]],
    )
    responses = [
        request(
            f"/api/v1/exploration/{node_ids['hbf']}/peripheral"
            f"?time_window=RECENT_90_DAYS&cursor={quote(cursor)}"
        ),
        request(
            f"/api/v1/exploration/{node_ids['sk_hynix']}/peripheral"
            f"?time_window=RECENT_1_YEAR&cursor={quote(cursor)}"
        ),
    ]

    assert all(status == 422 for status, _body in responses)
    assert all(
        body == {"error": {"code": "INVALID_REQUEST", "retryable": False}}
        for _status, body in responses
    )


def test_http_contract_returns_not_found_and_publication_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_hbf_fixture()
    missing_status, missing_body = request(
        "/api/v1/exploration/999999/peripheral?time_window=RECENT_90_DAYS"
    )

    def not_ready(*_args: object, **_kwargs: object) -> None:
        raise PublicationNotReadyError

    monkeypatch.setattr(api_module, "list_peripheral_nodes", not_ready)
    unavailable_status, unavailable_body = request(
        "/api/v1/exploration/1/peripheral?time_window=RECENT_90_DAYS"
    )

    assert missing_status == 404
    assert missing_body == {"error": {"code": "NODE_NOT_FOUND", "retryable": False}}
    assert unavailable_status == 503
    assert unavailable_body == {
        "error": {"code": "PUBLICATION_NOT_READY", "retryable": True}
    }
