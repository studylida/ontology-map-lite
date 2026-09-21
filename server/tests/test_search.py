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

from ontology_map.db import search as search_repository
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.schema import (
    knowledge_item,
    lint_finding,
    lint_policy_rule,
    lint_run,
    node,
    node_alias,
    node_merge,
    node_search_document,
    promotion_batch,
    publication_affected_node,
    search_document_basis,
)
from ontology_map.db.session import get_engine
from ontology_map.main import app
from ontology_map.search import search_nodes

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


def hold_existing_nodes(session: Session) -> None:
    session.execute(
        knowledge_item.update()
        .where(knowledge_item.c.item_kind == "NODE")
        .values(current_state="ON_HOLD")
    )


def insert_public_node(
    session: Session,
    name: str,
    *,
    identity_text: str | None = None,
    knowledge_text: str = "공개 Relation 없는 테스트 node",
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
            identity_text=identity_text or name,
            knowledge_text=knowledge_text,
            input_hash=sha256(f"search:{node_id}".encode()).digest(),
            generator_version="search-117-test-v1",
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
    return int(node_id)


def add_alias(session: Session, node_id: int, alias: str) -> None:
    session.execute(
        node_alias.insert().values(
            node_id=node_id,
            alias_text=alias,
            language="ko",
            is_preferred=False,
        )
    )


def add_blocking_finding(session: Session, node_id: int) -> None:
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
            finding_key=sha256(f"search-test:{node_id}".encode()).digest(),
            knowledge_item_id=node_id,
            lint_policy_rule_id=policy_rule.lint_policy_rule_id,
            first_detected_run_id=run_id,
            latest_detected_run_id=run_id,
            first_detected_at=NOW,
            last_detected_at=NOW,
            message="검색 통합 테스트 차단 finding",
        )
    )


def test_repository_orders_three_buckets_dedupes_and_preserves_limit() -> None:
    load_hbf_fixture()
    with rollback_session() as session:
        hold_existing_nodes(session)
        exact_id = insert_public_node(
            session,
            "정확 후보",
            identity_text="bucketterm",
            knowledge_text="bucketterm",
        )
        add_alias(session, exact_id, "bucketterm")
        identity_id = insert_public_node(
            session,
            "정체성 후보",
            identity_text="bucketterm",
            knowledge_text="bucketterm",
        )
        knowledge_id = insert_public_node(
            session,
            "설명 후보",
            identity_text="otheridentity",
            knowledge_text="bucketterm",
        )
        results = search_nodes(session, "bucketterm", 3)

    ids = [result.node_id for result in results]
    assert ids == [exact_id, identity_id, knowledge_id]
    assert len(ids) == len(set(ids)) == 3
    assert ids.count(identity_id) == 1


def test_exact_alias_bucket_uses_node_id_for_deterministic_order() -> None:
    load_hbf_fixture()
    with rollback_session() as session:
        hold_existing_nodes(session)
        first_id = insert_public_node(session, "exact-a")
        second_id = insert_public_node(session, "exact-b")
        add_alias(session, first_id, "same-exact")
        add_alias(session, second_id, "same-exact")
        results = search_nodes(session, "same-exact", 2)

    assert [result.node_id for result in results] == [first_id, second_id]


@pytest.mark.parametrize("field", ["identity", "knowledge"])
def test_fts_bucket_breaks_rank_ties_by_node_id(field: str) -> None:
    load_hbf_fixture()
    with rollback_session() as session:
        hold_existing_nodes(session)
        kwargs = (
            {"identity_text": "rankterm", "knowledge_text": "other"}
            if field == "identity"
            else {"identity_text": "other", "knowledge_text": "rankterm"}
        )
        first_id = insert_public_node(session, f"{field}-a", **kwargs)
        second_id = insert_public_node(session, f"{field}-b", **kwargs)
        results = search_nodes(session, "rankterm", 2)

    assert [result.node_id for result in results] == [first_id, second_id]


@pytest.mark.parametrize(
    ("expression", "exclude_identity", "identity_text", "knowledge_text"),
    [
        (
            search_repository._IDENTITY_FTS_EXPRESSION,
            False,
            "indexterm",
            "other",
        ),
        (
            search_repository._KNOWLEDGE_FTS_EXPRESSION,
            True,
            "other",
            "indexterm",
        ),
    ],
)
def test_fts_bucket_can_use_frozen_combined_gin_index(
    expression: str,
    exclude_identity: bool,
    identity_text: str,
    knowledge_text: str,
) -> None:
    load_hbf_fixture()
    with rollback_session() as session:
        hold_existing_nodes(session)
        insert_public_node(
            session,
            "index candidate",
            identity_text=identity_text,
            knowledge_text=knowledge_text,
        )
        session.execute(sa.text("SET LOCAL enable_seqscan = off"))
        plan = session.execute(
            sa.text(
                "EXPLAIN (COSTS OFF) "
                + search_repository._fts_sql(
                    expression,
                    exclude_identity=exclude_identity,
                )
            ),
            {"query": "indexterm", "limit": 5},
        ).scalars()

        plan_lines = [str(line) for line in plan]

    assert any("ix_node_search_document__fts" in line for line in plan_lines), (
        "\n".join(plan_lines)
    )


def test_exact_and_fts_converging_after_merge_return_canonical_once() -> None:
    load_hbf_fixture()
    with rollback_session() as session:
        hold_existing_nodes(session)
        source_id = insert_public_node(
            session,
            "merge-term",
            identity_text="merge-term",
            knowledge_text="merge-term",
        )
        canonical_id = insert_public_node(
            session,
            "canonical target",
            identity_text="merge-term",
            knowledge_text="merge-term",
        )
        session.execute(
            node_merge.insert().values(
                source_node_id=source_id,
                canonical_node_id=canonical_id,
                merge_reason="검색 #117 canonical dedupe 테스트",
                merged_at=NOW,
            )
        )
        results = search_nodes(session, "merge-term", 5)

    assert [result.node_id for result in results] == [canonical_id]
    assert results[0].name == "canonical target"


def test_repository_includes_relationless_public_node() -> None:
    load_hbf_fixture()
    with rollback_session() as session:
        node_id = insert_public_node(session, "독립 공개 node")
        results = search_nodes(session, "독립 공개 node", 5)

    assert len(results) == 1
    assert results[0].node_id == node_id


def test_repository_rechecks_node_lint_and_selected_basis() -> None:
    _created, node_ids = load_hbf_fixture()
    with rollback_session() as session:
        add_blocking_finding(session, node_ids["hbf"])
        blocked = search_nodes(session, "HBF", 20)

        basis_item_id = session.scalar(
            sa.select(search_document_basis.c.knowledge_item_id)
            .join(
                publication_affected_node,
                publication_affected_node.c.node_search_document_id
                == search_document_basis.c.node_search_document_id,
            )
            .join(
                promotion_batch,
                promotion_batch.c.promotion_batch_id
                == publication_affected_node.c.promotion_batch_id,
            )
            .where(
                publication_affected_node.c.node_id == node_ids["sk_hynix"],
                promotion_batch.c.publication_status == "READY",
                search_document_basis.c.knowledge_item_id != node_ids["sk_hynix"],
            )
            .order_by(
                promotion_batch.c.ready_at.desc(),
                promotion_batch.c.promotion_batch_id.desc(),
                search_document_basis.c.knowledge_item_id,
            )
            .limit(1)
        )
        assert basis_item_id is not None
        session.execute(
            knowledge_item.update()
            .where(knowledge_item.c.knowledge_item_id == basis_item_id)
            .values(current_state="ON_HOLD")
        )
        invalid_basis = search_nodes(session, "SK하이닉스", 20)

    assert all(result.node_id != node_ids["hbf"] for result in blocked)
    assert all(result.node_id != node_ids["sk_hynix"] for result in invalid_basis)


def test_http_contract_returns_minimal_items_and_empty_results() -> None:
    load_hbf_fixture()
    status, body = request("/api/v1/nodes/search?q=HBF&limit=2")
    empty_status, empty_body = request("/api/v1/nodes/search?q=no-such-node")

    assert status == 200
    assert set(body) == {"items"}
    assert len(body["items"]) == 2
    assert set(body["items"][0]) == {"node_id", "name", "node_type"}
    assert isinstance(body["items"][0]["node_id"], str)
    assert empty_status == 200
    assert empty_body == {"items": []}


def test_openapi_search_result_has_no_match_reasons() -> None:
    schema = app.openapi()["components"]["schemas"]["SearchResultResponse"]
    assert set(schema["properties"]) == {"node_id", "name", "node_type"}


@pytest.mark.parametrize(
    "target",
    [
        "/api/v1/nodes/search",
        "/api/v1/nodes/search?q=%20%20",
        "/api/v1/nodes/search?q=HBF&limit=0",
        "/api/v1/nodes/search?q=HBF&limit=21",
    ],
)
def test_http_contract_rejects_invalid_query(target: str) -> None:
    load_hbf_fixture()
    status, body = request(target)
    assert status == 422
    assert body == {"error": {"code": "INVALID_REQUEST", "retryable": False}}
