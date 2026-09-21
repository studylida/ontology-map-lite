import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

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
    conflict_member,
    conflict_set,
    evidence_group,
    knowledge_item,
    lint_finding,
    lint_policy_rule,
    lint_run,
    observation,
    promotion_batch,
    publication_affected_node,
    relation,
    search_document_basis,
    source_document,
)
from ontology_map.db.session import get_engine
from ontology_map.exploration import (
    PublicationNotReadyError,
    TimeWindow,
    get_exploration,
    list_peripheral_nodes,
)
from ontology_map.main import app
from ontology_map.pagination import InvalidCursorError, encode_cursor
from ontology_map.relations import (
    RelationEvidenceNotFoundError,
    list_node_relations,
    list_relation_evidence,
)

NOW = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


@contextmanager
def rollback_session() -> Iterator[Session]:
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection) as session:
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


def hbf_relation_ids(
    session: Session, node_ids: dict[str, int]
) -> tuple[int, int, int]:
    relation_id = session.scalar(
        sa.select(relation.c.relation_id).where(
            relation.c.source_node_id.in_((node_ids["sk_hynix"], node_ids["hbf"])),
            relation.c.target_node_id.in_((node_ids["sk_hynix"], node_ids["hbf"])),
        )
    )
    assert relation_id is not None
    claim_id = session.scalar(
        sa.select(claim_relation.c.claim_id).where(
            claim_relation.c.relation_id == relation_id,
            claim_relation.c.stance == "SUPPORT",
        )
    )
    assert claim_id is not None
    group_id = session.scalar(
        sa.select(source_document.c.evidence_group_id)
        .select_from(claim_observation.join(observation).join(source_document))
        .where(claim_observation.c.claim_id == claim_id)
    )
    assert group_id is not None
    return int(relation_id), int(claim_id), int(group_id)


def current_search_document_id(session: Session, node_id: int) -> int:
    value = session.scalar(
        sa.select(publication_affected_node.c.node_search_document_id)
        .join(promotion_batch)
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


def insert_observation(
    session: Session,
    *,
    key: str,
    body: str,
    published_at: datetime | None,
    evidence_group_id: int | None = None,
    paragraph_number: int | None = 1,
) -> tuple[int, int]:
    group_id = evidence_group_id
    if group_id is None:
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
                source_key=f"relation-test:{key}",
                version_no=1,
                canonical_url=f"https://example.com/relation-test/{key}",
                publisher_name="관계 API 테스트",
                title=f"관계 API 테스트 {key}",
                original_language="ko",
                normalized_body=body,
                body_hash=sha256(body.encode()).digest(),
                published_at=published_at,
                published_precision="DAY" if published_at is not None else "UNKNOWN",
                modified_precision="UNKNOWN",
                last_checked_at=NOW,
                last_check_status="SUCCESS",
            )
            .returning(source_document.c.source_document_id)
        ).scalar_one()
    )
    observation_id = int(
        session.execute(
            observation.insert()
            .values(
                source_document_id=document_id,
                start_char=0,
                end_char=len(body),
                quote_text=body,
                quote_hash=sha256(body.encode()).digest(),
                paragraph_number=paragraph_number,
                observed_at=NOW,
            )
            .returning(observation.c.observation_id)
        ).scalar_one()
    )
    return observation_id, group_id


def insert_relation_claim(
    session: Session,
    *,
    relation_id: int,
    search_document_id: int,
    stance: str,
    observation_id: int,
    key: str,
) -> int:
    batch_id = session.scalar(
        sa.select(knowledge_item.c.promotion_batch_id).where(
            knowledge_item.c.knowledge_item_id == relation_id
        )
    )
    assert batch_id is not None
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
    session.execute(
        claim.insert().values(
            claim_id=claim_id,
            statement_text=f"관계 API 테스트 Claim {key}",
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
            stance=stance,
        )
    )
    session.execute(
        claim_observation.insert().values(
            claim_id=claim_id,
            observation_id=observation_id,
        )
    )
    session.execute(
        search_document_basis.insert().values(
            node_search_document_id=search_document_id,
            knowledge_item_id=claim_id,
        )
    )
    return claim_id


def test_repository_lists_relations_and_traces_from_current_ready_data() -> None:
    _created, node_ids = load_hbf_fixture()
    with Session(get_engine()) as session:
        relation_id, _claim_id, _group_id = hbf_relation_ids(session, node_ids)
        relations_page = list_node_relations(
            session, node_ids["sk_hynix"], cursor=None, limit=20
        )
        evidence_page = list_relation_evidence(
            session, relation_id, cursor=None, limit=10
        )

    relation_item = next(
        item for item in relations_page.items if item.relation_id == relation_id
    )
    assert relation_item.other_node.node_id == node_ids["hbf"]
    assert relation_item.relation_type_display_name == "공개 관계"
    assert relation_item.supporting_evidence_group_count == 1
    assert relation_item.has_conflict is False
    assert evidence_page.trace_count == 1
    assert evidence_page.next_cursor is None
    assert evidence_page.items[0].stance == "SUPPORT"
    assert evidence_page.items[0].source.canonical_url.startswith("https://")
    assert evidence_page.items[0].locator.paragraph_number == 1
    assert evidence_page.items[0].locator.end_char == len(
        evidence_page.items[0].quote_text
    )


@pytest.mark.parametrize("invalidation", ["state", "blocking_lint"])
def test_repository_revalidates_every_selected_basis_item(
    invalidation: str,
) -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        relation_id, _support_claim_id, _group_id = hbf_relation_ids(session, node_ids)
        search_document_id = current_search_document_id(session, node_ids["sk_hynix"])
        current_basis_ids = set(
            session.scalars(
                sa.select(search_document_basis.c.knowledge_item_id).where(
                    search_document_basis.c.node_search_document_id
                    == search_document_id
                )
            )
        )
        unrelated_basis_id = session.scalar(
            sa.select(knowledge_item.c.knowledge_item_id)
            .where(
                knowledge_item.c.item_kind == "CLAIM",
                knowledge_item.c.current_state.in_(
                    ("EVIDENCE_VERIFIED", "HUMAN_VERIFIED")
                ),
                knowledge_item.c.knowledge_item_id.not_in(current_basis_ids),
            )
            .order_by(knowledge_item.c.knowledge_item_id)
            .limit(1)
        )
        assert unrelated_basis_id is not None
        session.execute(
            search_document_basis.insert().values(
                node_search_document_id=search_document_id,
                knowledge_item_id=unrelated_basis_id,
            )
        )
        assert any(
            item.relation_id == relation_id
            for item in list_node_relations(
                session, node_ids["sk_hynix"], cursor=None, limit=20
            ).items
        )
        assert list_relation_evidence(session, relation_id, cursor=None, limit=10).items

        if invalidation == "state":
            session.execute(
                knowledge_item.update()
                .where(knowledge_item.c.knowledge_item_id == unrelated_basis_id)
                .values(current_state="ON_HOLD")
            )
        else:
            policy_rule = session.execute(
                sa.select(
                    lint_policy_rule.c.lint_policy_rule_id,
                    lint_policy_rule.c.lint_policy_version_id,
                )
                .where(lint_policy_rule.c.severity == "BLOCKING")
                .limit(1)
            ).one()
            run_id = session.execute(
                lint_run.insert()
                .values(
                    lint_policy_version_id=policy_rule.lint_policy_version_id,
                    status="SUCCESS",
                    started_at=NOW - timedelta(seconds=1),
                    completed_at=NOW,
                )
                .returning(lint_run.c.lint_run_id)
            ).scalar_one()
            session.execute(
                lint_finding.insert().values(
                    finding_key=sha256(
                        f"relation-basis-test:{unrelated_basis_id}".encode()
                    ).digest(),
                    knowledge_item_id=unrelated_basis_id,
                    lint_policy_rule_id=policy_rule.lint_policy_rule_id,
                    first_detected_run_id=run_id,
                    latest_detected_run_id=run_id,
                    first_detected_at=NOW,
                    last_detected_at=NOW,
                    message="선택된 검색 문서 basis 회귀 검사 차단 finding",
                )
            )

        with pytest.raises(PublicationNotReadyError):
            get_exploration(
                session,
                node_ids["sk_hynix"],
                TimeWindow.RECENT_90_DAYS,
                now=NOW,
            )
        with pytest.raises(PublicationNotReadyError):
            list_peripheral_nodes(
                session,
                node_ids["sk_hynix"],
                TimeWindow.RECENT_90_DAYS,
                cursor=None,
                limit=20,
                now=NOW,
            )
        with pytest.raises(PublicationNotReadyError):
            list_node_relations(session, node_ids["sk_hynix"], cursor=None, limit=20)
        with pytest.raises(RelationEvidenceNotFoundError):
            list_relation_evidence(session, relation_id, cursor=None, limit=10)


def test_repository_separates_trace_count_support_groups_and_conflict() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        relation_id, support_claim_id, group_id = hbf_relation_ids(session, node_ids)
        search_document_id = current_search_document_id(session, node_ids["sk_hynix"])
        duplicate_observation_id, _ = insert_observation(
            session,
            key="same-group",
            body="같은 계보의 두 번째 공개 근거",
            published_at=NOW - timedelta(hours=2),
            evidence_group_id=group_id,
        )
        session.execute(
            claim_observation.insert().values(
                claim_id=support_claim_id,
                observation_id=duplicate_observation_id,
            )
        )
        dispute_observation_id, _ = insert_observation(
            session,
            key="dispute",
            body="관계를 반박하는 공개 근거",
            published_at=NOW - timedelta(hours=1),
        )
        dispute_claim_id = insert_relation_claim(
            session,
            relation_id=relation_id,
            search_document_id=search_document_id,
            stance="DISPUTE",
            observation_id=dispute_observation_id,
            key="dispute",
        )
        unknown_observation_id, _ = insert_observation(
            session,
            key="unknown-date",
            body="게시일을 알 수 없는 반박 근거",
            published_at=None,
            paragraph_number=None,
        )
        insert_relation_claim(
            session,
            relation_id=relation_id,
            search_document_id=search_document_id,
            stance="DISPUTE",
            observation_id=unknown_observation_id,
            key="unknown-date",
        )
        conflict_id = session.execute(
            conflict_set.insert()
            .values(
                relation_id=relation_id,
                modality="FACT",
                current_state="AGENT_PROPOSED",
                created_at=NOW,
            )
            .returning(conflict_set.c.conflict_set_id)
        ).scalar_one()
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
                    "claim_id": dispute_claim_id,
                    "position_key": "dispute",
                },
            ],
        )

        relations_page = list_node_relations(
            session, node_ids["sk_hynix"], cursor=None, limit=20
        )
        first_page = list_relation_evidence(
            session, relation_id, TimeWindow.ALL_TIME, cursor=None, limit=2
        )
        assert first_page.next_cursor is not None
        other_relation_id = session.scalar(
            sa.select(relation.c.relation_id)
            .where(relation.c.relation_id != relation_id)
            .order_by(relation.c.relation_id)
            .limit(1)
        )
        assert other_relation_id is not None
        with pytest.raises(InvalidCursorError):
            list_relation_evidence(
                session,
                int(other_relation_id),
                TimeWindow.ALL_TIME,
                cursor=first_page.next_cursor,
                limit=2,
            )
        second_page = list_relation_evidence(
            session,
            relation_id,
            TimeWindow.ALL_TIME,
            cursor=first_page.next_cursor,
            limit=2,
        )

        relation_item = next(
            item for item in relations_page.items if item.relation_id == relation_id
        )
        traces = first_page.items + second_page.items
        assert relation_item.supporting_evidence_group_count == 1
        assert relation_item.has_conflict is True
        assert first_page.trace_count == 4
        assert second_page.trace_count == 4
        assert [item.stance for item in traces].count("DISPUTE") == 2
        assert traces[-1].source.published_at is None
        assert traces[-1].locator.paragraph_number is None

        session.execute(
            knowledge_item.update()
            .where(knowledge_item.c.knowledge_item_id == dispute_claim_id)
            .values(current_state="ON_HOLD")
        )
        with pytest.raises(PublicationNotReadyError):
            list_node_relations(session, node_ids["sk_hynix"], cursor=None, limit=20)
        with pytest.raises(RelationEvidenceNotFoundError):
            list_relation_evidence(session, relation_id, cursor=None, limit=10)


def test_repository_uses_scoped_keyset_cursors_and_public_filters() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        first_page = list_node_relations(
            session, node_ids["sk_hynix"], cursor=None, limit=1
        )
        assert first_page.next_cursor is not None
        second_page = list_node_relations(
            session,
            node_ids["sk_hynix"],
            cursor=first_page.next_cursor,
            limit=1,
        )
        assert first_page.items[0].relation_id < second_page.items[0].relation_id
        assert second_page.next_cursor is None
        empty_page = list_node_relations(
            session,
            node_ids["sk_hynix"],
            cursor=encode_cursor(
                "node-relations",
                {
                    "node_id": node_ids["sk_hynix"],
                    "time_window": TimeWindow.RECENT_90_DAYS.value,
                },
                [1, 9_223_372_036_854_775_807],
            ),
            limit=20,
        )
        assert empty_page.items == []
        assert empty_page.next_cursor is None
        with pytest.raises(InvalidCursorError):
            list_node_relations(
                session,
                node_ids["hbf"],
                cursor=first_page.next_cursor,
                limit=1,
            )

        relation_id, claim_id, _group_id = hbf_relation_ids(session, node_ids)
        session.execute(
            knowledge_item.update()
            .where(knowledge_item.c.knowledge_item_id == claim_id)
            .values(current_state="ON_HOLD")
        )
        with pytest.raises(PublicationNotReadyError):
            list_node_relations(session, node_ids["sk_hynix"], cursor=None, limit=20)
        with pytest.raises(RelationEvidenceNotFoundError):
            list_relation_evidence(session, relation_id, cursor=None, limit=10)


def test_http_contract_returns_string_ids_and_hides_internal_ids() -> None:
    _created, node_ids = load_hbf_fixture()
    relations_status, relations_body = request(
        f"/api/v1/nodes/{node_ids['sk_hynix']}/relations?time_window=RECENT_90_DAYS&limit=1"
    )
    assert relations_status == 200
    assert set(relations_body) == {"items", "next_cursor"}
    relation_item = relations_body["items"][0]
    assert set(relation_item) == {
        "source_node_id",
        "target_node_id",
        "directionality",
        "relation_id",
        "other_node",
        "relation_type_display_name",
        "supporting_evidence_group_count",
        "has_conflict",
    }
    assert isinstance(relation_item["relation_id"], str)
    assert isinstance(relation_item["other_node"]["node_id"], str)

    evidence_status, evidence_body = request(
        f"/api/v1/relations/{relation_item['relation_id']}/evidence?time_window=RECENT_90_DAYS"
    )
    assert evidence_status == 200
    assert set(evidence_body) == {"items", "trace_count", "next_cursor"}
    evidence_item = evidence_body["items"][0]
    assert set(evidence_item) == {
        "item_key",
        "claim_text",
        "modality",
        "stance",
        "source",
        "quote_text",
        "locator",
    }
    serialized = json.dumps(evidence_body)
    for internal_name in (
        "source_document_id",
        "observation_id",
        "claim_id",
        "evidence_group_id",
    ):
        assert internal_name not in serialized


@pytest.mark.parametrize(
    "target",
    [
        "/api/v1/nodes/1/relations?limit=0",
        "/api/v1/nodes/1/relations?limit=51",
        "/api/v1/nodes/1/relations?cursor=not-base64!",
        "/api/v1/relations/1/evidence?limit=0",
        "/api/v1/relations/1/evidence?limit=51",
        "/api/v1/relations/1/evidence?cursor=not-base64!",
        "/api/v1/relations/not-an-id/evidence",
        "/api/v1/relations/9223372036854775808/evidence",
    ],
)
def test_http_contract_rejects_invalid_requests(target: str) -> None:
    load_hbf_fixture()
    status, body = request(target)
    assert status == 422
    assert body == {"error": {"code": "INVALID_REQUEST", "retryable": False}}


def test_http_contract_returns_not_found_and_publication_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_hbf_fixture()
    node_status, node_body = request(
        "/api/v1/nodes/999999/relations?time_window=RECENT_90_DAYS"
    )
    relation_status, relation_body = request(
        "/api/v1/relations/999999/evidence?time_window=RECENT_90_DAYS"
    )
    assert node_status == 404
    assert node_body == {"error": {"code": "NODE_NOT_FOUND", "retryable": False}}
    assert relation_status == 404
    assert relation_body == {
        "error": {"code": "RELATION_NOT_FOUND", "retryable": False}
    }

    def not_ready(*_args: object, **_kwargs: object) -> None:
        raise PublicationNotReadyError

    monkeypatch.setattr(api_module, "list_node_relations", not_ready)
    status, body = request("/api/v1/nodes/1/relations?time_window=RECENT_90_DAYS")
    assert status == 503
    assert body == {"error": {"code": "PUBLICATION_NOT_READY", "retryable": True}}
