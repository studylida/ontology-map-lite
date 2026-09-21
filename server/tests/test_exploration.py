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
from ontology_map import panel
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.panel_fixture import load_panel_fixture
from ontology_map.db.schema import (
    claim_observation,
    claim_relation,
    conflict_member,
    conflict_set,
    followup_question,
    knowledge_item,
    lint_finding,
    lint_policy_rule,
    lint_run,
    node,
    node_alias,
    observation,
    promotion_batch,
    publication_affected_node,
    relation,
    relation_type_revision,
    source_document,
)
from ontology_map.db.session import get_engine
from ontology_map.exploration import (
    MAX_DIRECT_NODES,
    MAX_RELATIONS,
    MAX_TWO_HOP_NODES,
    PublicationNotReadyError,
    TimeWindow,
    get_exploration,
)
from ontology_map.main import app

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


def hbf_relation_evidence_ids(
    session: Session, node_ids: dict[str, int]
) -> tuple[int, int, int]:
    sk_hynix_id = node_ids["sk_hynix"]
    hbf_id = node_ids["hbf"]
    relation_id = session.scalar(
        sa.select(relation.c.relation_id).where(
            sa.or_(
                sa.and_(
                    relation.c.source_node_id == sk_hynix_id,
                    relation.c.target_node_id == hbf_id,
                ),
                sa.and_(
                    relation.c.source_node_id == hbf_id,
                    relation.c.target_node_id == sk_hynix_id,
                ),
            )
        )
    )
    assert relation_id is not None
    claim_id = session.scalar(
        sa.select(claim_relation.c.claim_id)
        .where(
            claim_relation.c.relation_id == relation_id,
            claim_relation.c.stance == "SUPPORT",
        )
        .order_by(claim_relation.c.claim_id)
        .limit(1)
    )
    assert claim_id is not None
    evidence_group_id = session.scalar(
        sa.select(source_document.c.evidence_group_id)
        .select_from(claim_observation.join(observation).join(source_document))
        .where(claim_observation.c.claim_id == claim_id)
        .order_by(source_document.c.evidence_group_id)
        .limit(1)
    )
    assert evidence_group_id is not None
    return int(relation_id), int(claim_id), int(evidence_group_id)


@pytest.mark.parametrize("directionality", ["DIRECTED", "SYMMETRIC"])
def test_repository_builds_hbf_graph_and_can_change_center(directionality: str) -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        session.execute(
            relation_type_revision.update().values(directionality=directionality)
        )
        first = get_exploration(
            session, node_ids["sk_hynix"], TimeWindow.RECENT_90_DAYS, now=NOW
        )
        next_node_id = next(
            graph_node.node_id
            for graph_node in first.graph.nodes
            if graph_node.node_id != first.center_node_id
        )
        second = get_exploration(
            session, next_node_id, TimeWindow.RECENT_1_YEAR, now=NOW
        )

    assert first.graph.nodes[0].tier == "CENTER"
    assert [node.name for node in first.graph.nodes] == [
        "SK하이닉스",
        "HBF",
        "FMS 2026 HBF 발표",
        "SanDisk",
        "UCIe",
    ]
    assert len(first.graph.nodes) <= 1 + MAX_DIRECT_NODES + MAX_TWO_HOP_NODES
    assert all(item.directionality == directionality for item in first.graph.relations)
    assert len(first.graph.relations) <= MAX_RELATIONS
    assert len(first.recommendations) == 4
    assert [question.slot for question in first.followup_questions] == [1, 2]
    assert second.center_node_id == next_node_id
    assert second.graph.nodes[0].node_id == next_node_id


def test_time_window_hides_old_graph_but_all_time_keeps_it() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        session.execute(
            source_document.update().values(published_at=NOW - timedelta(days=500))
        )

        recent = get_exploration(
            session, node_ids["sk_hynix"], TimeWindow.RECENT_1_YEAR, now=NOW
        )
        all_time = get_exploration(
            session, node_ids["sk_hynix"], TimeWindow.ALL_TIME, now=NOW
        )

        assert len(recent.graph.nodes) == 1
        assert recent.graph.relations == []
        assert recent.graph.nodes[0].activity_evidence_group_count == 0
        assert len(all_time.graph.nodes) > 1
        assert all_time.graph.relations


def test_ready_empty_questions_do_not_require_legacy_followup_slots() -> None:
    _created, node_ids = load_panel_fixture()
    with rollback_session() as session:
        node_id = node_ids["empty"]
        context_id = session.scalar(
            sa.select(publication_affected_node.c.node_context_id).where(
                publication_affected_node.c.node_id == node_id
            )
        )
        assert context_id is not None
        session.execute(
            followup_question.delete().where(
                followup_question.c.node_context_id == context_id
            )
        )
        assert (
            panel.list_questions(
                session, node_id, TimeWindow.RECENT_90_DAYS, cursor=None
            )["items"]
            == []
        )
        result = get_exploration(session, node_id, TimeWindow.RECENT_90_DAYS)

    assert result.center_node_id == node_id
    assert result.followup_questions == []


def test_repository_keeps_previous_ready_when_newer_publication_fails() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        lint_policy_version_id = session.scalar(
            sa.select(promotion_batch.c.lint_policy_version_id).limit(1)
        )
        failed_batch_id = session.execute(
            promotion_batch.insert()
            .values(
                lint_policy_version_id=lint_policy_version_id,
                promotion_status="COMMITTED",
                publication_status="FAILED",
                started_at=NOW - timedelta(minutes=2),
                committed_at=NOW - timedelta(minutes=1),
                publication_failure_reason="integration test failure",
            )
            .returning(promotion_batch.c.promotion_batch_id)
        ).scalar_one()
        session.execute(
            publication_affected_node.insert().values(
                promotion_batch_id=failed_batch_id,
                node_id=node_ids["sk_hynix"],
            )
        )

        result = get_exploration(
            session, node_ids["sk_hynix"], TimeWindow.RECENT_90_DAYS, now=NOW
        )

    assert result.center_node_id == node_ids["sk_hynix"]


def test_repository_counts_distinct_evidence_then_rechecks_relation_basis() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        relation_id, claim_id, evidence_group_id = hbf_relation_evidence_ids(
            session, node_ids
        )
        baseline = get_exploration(
            session, node_ids["sk_hynix"], TimeWindow.RECENT_90_DAYS, now=NOW
        )
        relation = next(
            item for item in baseline.graph.relations if item.relation_id == relation_id
        )
        assert relation.supporting_evidence_group_count == 1

        body = "추가 근거"
        source_id = session.execute(
            source_document.insert()
            .values(
                evidence_group_id=evidence_group_id,
                source_key="integration-test:duplicate-group",
                version_no=1,
                canonical_url="https://example.com/integration-test",
                publisher_name="통합 테스트",
                title="같은 근거 묶음의 추가 문서",
                original_language="ko",
                normalized_body=body,
                body_hash=sha256(body.encode()).digest(),
                published_at=NOW - timedelta(days=1),
                published_precision="DAY",
                modified_precision="UNKNOWN",
                last_checked_at=NOW,
                last_check_status="SUCCESS",
            )
            .returning(source_document.c.source_document_id)
        ).scalar_one()
        observation_id = session.execute(
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
        session.execute(
            claim_observation.insert().values(
                claim_id=claim_id, observation_id=observation_id
            )
        )

        duplicate_group = get_exploration(
            session, node_ids["sk_hynix"], TimeWindow.RECENT_90_DAYS, now=NOW
        )
        relation = next(
            item
            for item in duplicate_group.graph.relations
            if item.relation_id == relation_id
        )
        assert relation.supporting_evidence_group_count == 1

        session.execute(
            knowledge_item.update()
            .where(knowledge_item.c.knowledge_item_id == relation_id)
            .values(current_state="ON_HOLD")
        )
        with pytest.raises(PublicationNotReadyError):
            get_exploration(
                session, node_ids["sk_hynix"], TimeWindow.RECENT_90_DAYS, now=NOW
            )


def test_repository_rejects_open_blocking_lint_in_selected_basis() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        relation_id, _claim_id, _evidence_group_id = hbf_relation_evidence_ids(
            session, node_ids
        )
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
                finding_key=sha256(f"test:{relation_id}".encode()).digest(),
                knowledge_item_id=relation_id,
                lint_policy_rule_id=policy_rule.lint_policy_rule_id,
                first_detected_run_id=run_id,
                latest_detected_run_id=run_id,
                first_detected_at=NOW,
                last_detected_at=NOW,
                message="통합 테스트 차단 finding",
            )
        )

        with pytest.raises(PublicationNotReadyError):
            get_exploration(
                session, node_ids["sk_hynix"], TimeWindow.RECENT_90_DAYS, now=NOW
            )


def test_repository_reports_public_conflict_separately() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        relation_id, claim_id, _evidence_group_id = hbf_relation_evidence_ids(
            session, node_ids
        )
        conflict_set_id = session.execute(
            conflict_set.insert()
            .values(
                relation_id=relation_id,
                modality="FACT",
                current_state="AGENT_PROPOSED",
            )
            .returning(conflict_set.c.conflict_set_id)
        ).scalar_one()
        session.execute(
            conflict_member.insert().values(
                conflict_set_id=conflict_set_id,
                claim_id=claim_id,
                position_key="integration-test",
            )
        )
        result = get_exploration(
            session, node_ids["sk_hynix"], TimeWindow.RECENT_90_DAYS, now=NOW
        )

    relation = next(
        item for item in result.graph.relations if item.relation_id == relation_id
    )
    assert relation.supporting_evidence_group_count == 1
    assert relation.has_conflict


def test_repository_distinguishes_missing_ready_publication() -> None:
    load_hbf_fixture()
    with rollback_session() as session:
        batch_id = session.scalar(
            sa.select(promotion_batch.c.promotion_batch_id).limit(1)
        )
        node_type_id = session.scalar(sa.select(node.c.node_type_id).limit(1))
        new_node_id = session.execute(
            knowledge_item.insert()
            .values(
                item_kind="NODE",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(knowledge_item.c.knowledge_item_id)
        ).scalar_one()
        session.execute(
            node.insert().values(node_id=new_node_id, node_type_id=node_type_id)
        )
        session.execute(
            node_alias.insert().values(
                node_id=new_node_id,
                alias_text="READY 없는 공개 노드",
                language="ko",
                is_preferred=True,
            )
        )

        with pytest.raises(PublicationNotReadyError):
            get_exploration(session, new_node_id, TimeWindow.RECENT_90_DAYS, now=NOW)


def test_http_contract_returns_string_ids_and_only_approved_fields() -> None:
    _created, node_ids = load_hbf_fixture()
    status, body = request(
        f"/api/v1/exploration/{node_ids['sk_hynix']}?time_window=RECENT_90_DAYS"
    )

    assert status == 200
    assert set(body) == {
        "center_node_id",
        "context_text",
        "graph",
        "recommendations",
        "followup_questions",
    }
    assert isinstance(body["center_node_id"], str)
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
    assert all(isinstance(item["node_id"], str) for item in body["graph"]["nodes"])
    assert all(
        isinstance(item["relation_id"], str) for item in body["graph"]["relations"]
    )
    names = {n["node_id"]: n["name"] for n in body["graph"]["nodes"]}
    edges = {r["relation_id"]: r for r in body["graph"]["relations"]}
    for recommendation in body["recommendations"]:
        path = recommendation["path"]
        assert (
            len(path)
            == {"DIRECT": 1, "TWO_HOP": 2, "AMBIENT": 0}[recommendation["reason_code"]]
        )
        current = body["center_node_id"]
        for edge in path:
            assert edge["relation_id"] in edges
            assert edge["source_node_name"] == names[edge["source_node_id"]]
            assert edge["target_node_name"] == names[edge["target_node_id"]]
            endpoints = {edge["source_node_id"], edge["target_node_id"]}
            assert current in endpoints
            current = (endpoints - {current}).pop()
        if path:
            assert current == recommendation["target_node"]["node_id"]
    assert len(body["followup_questions"]) == 2

    target_id = body["graph"]["nodes"][1]["node_id"]
    next_status, next_body = request(
        f"/api/v1/exploration/{target_id}?time_window=RECENT_1_YEAR"
    )
    assert next_status == 200
    assert next_body["center_node_id"] == target_id


@pytest.mark.parametrize(
    ("target", "status", "code"),
    [
        (
            "/api/v1/exploration/999999?time_window=RECENT_90_DAYS",
            404,
            "NODE_NOT_FOUND",
        ),
        (
            "/api/v1/exploration/not-an-id?time_window=RECENT_90_DAYS",
            422,
            "INVALID_REQUEST",
        ),
        (
            "/api/v1/exploration/1?time_window=RECENT_30_DAYS",
            422,
            "INVALID_REQUEST",
        ),
        (
            "/api/v1/exploration/9223372036854775808?time_window=RECENT_90_DAYS",
            422,
            "INVALID_REQUEST",
        ),
    ],
)
def test_http_contract_errors(target: str, status: int, code: str) -> None:
    load_hbf_fixture()
    response_status, body = request(target)
    assert response_status == status
    assert body == {"error": {"code": code, "retryable": False}}


def test_http_contract_publication_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def not_ready(*_args: object, **_kwargs: object) -> None:
        raise PublicationNotReadyError

    monkeypatch.setattr(api_module, "get_exploration", not_ready)
    status, body = request("/api/v1/exploration/1?time_window=RECENT_90_DAYS")
    assert status == 503
    assert body == {"error": {"code": "PUBLICATION_NOT_READY", "retryable": True}}
