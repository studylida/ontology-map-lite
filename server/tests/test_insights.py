from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from test_relations import request, rollback_session

from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.session import get_engine, open_read_session
from ontology_map.exploration import PublicationNotReadyError, TimeWindow
from ontology_map.insights import InsightNotFoundError, get_insight, list_node_insights
from ontology_map.main import app

WINDOW = TimeWindow.RECENT_90_DAYS


def test_stored_insight_contract_and_distinct_evidence() -> None:
    _, ids = load_hbf_fixture()
    status, body = request(
        f"/api/v1/nodes/{ids['sk_hynix']}/insights?time_window={WINDOW}"
    )
    assert status == 200
    item = body["items"][0]
    assert set(item) == {"insight_id", "slot", "title", "evidence_group_count"}
    assert isinstance(item["insight_id"], str)
    status, detail = request(f"/api/v1/insights/{item['insight_id']}")
    assert status == 200 and detail["claims"]
    assert {"summary", "synthesis", "caveat"} <= detail.keys()
    assert detail["claims"][0]["role"] == "KEY_CLAIM"
    assert detail["claims"][0]["traces"][0]["locator"]["end_char"] > 0
    with Session(get_engine()) as session:
        count = session.scalar(
            sa.select(sa.func.count(sa.distinct(s.source_document.c.evidence_group_id)))
            .select_from(
                s.node_insight_claim.join(
                    s.claim_observation,
                    s.claim_observation.c.claim_id == s.node_insight_claim.c.claim_id,
                )
                .join(s.observation)
                .join(s.source_document)
            )
            .where(s.node_insight_claim.c.node_insight_id == int(item["insight_id"]))
        )
        assert item["evidence_group_count"] == count
    assert request("/api/v1/nodes/1/insights?time_window=ALL")[0] == 422
    assert request("/api/v1/insights/9223372036854775808")[0] == 422
    assert request("/api/v1/insights/9223372036854775807")[0] == 404


@pytest.mark.parametrize("hidden_basis", ["claim", "other_basis"])
def test_hidden_basis_hides_whole_insight_in_list_and_detail(hidden_basis: str) -> None:
    _, ids = load_hbf_fixture()
    with rollback_session() as session:
        insight = list_node_insights(session, ids["sk_hynix"], WINDOW)[0]
        claim_id = session.scalar(
            sa.select(s.node_insight_claim.c.claim_id).where(
                s.node_insight_claim.c.node_insight_id == insight.node_insight_id
            )
        )
        hidden_id = claim_id
        if hidden_basis == "other_basis":
            document_id = session.scalar(
                sa.select(s.node_insight.c.node_search_document_id).where(
                    s.node_insight.c.node_insight_id == insight.node_insight_id
                )
            )
            hidden_id = session.scalar(
                sa.select(s.search_document_basis.c.knowledge_item_id).where(
                    s.search_document_basis.c.node_search_document_id == document_id,
                    s.search_document_basis.c.knowledge_item_id.not_in(
                        [ids["sk_hynix"], claim_id]
                    ),
                )
            )
        assert hidden_id is not None
        session.execute(
            s.knowledge_item.update()
            .where(s.knowledge_item.c.knowledge_item_id == hidden_id)
            .values(current_state="ON_HOLD")
        )
        assert list_node_insights(session, ids["sk_hynix"], WINDOW) == []
        with pytest.raises(InsightNotFoundError):
            get_insight(session, insight.node_insight_id)


def test_success_empty_differs_from_missing_bundle_and_legacy_http() -> None:
    _, ids = load_hbf_fixture()
    with rollback_session() as session:
        item = list_node_insights(session, ids["sk_hynix"], WINDOW)[0]
        task_id = session.scalar(
            sa.select(s.node_insight.c.model_task_id).where(
                s.node_insight.c.node_insight_id == item.node_insight_id
            )
        )
        session.execute(
            s.node_insight_claim.delete().where(
                s.node_insight_claim.c.node_insight_id == item.node_insight_id
            )
        )
        session.execute(
            s.node_insight.delete().where(
                s.node_insight.c.node_insight_id == item.node_insight_id
            )
        )
        assert list_node_insights(session, ids["sk_hynix"], WINDOW) == []
        session.execute(
            s.publication_affected_node.update()
            .where(s.publication_affected_node.c.node_insight_model_task_id == task_id)
            .values(node_insight_model_task_id=None)
        )
        with pytest.raises(PublicationNotReadyError):
            list_node_insights(session, ids["sk_hynix"], WINDOW)
        app.dependency_overrides[open_read_session] = lambda: session
        try:
            status, body = request(
                f"/api/v1/nodes/{ids['sk_hynix']}/insights?time_window={WINDOW}"
            )
            assert status == 503 and body["error"]["retryable"]
        finally:
            app.dependency_overrides.clear()


def test_blocking_lint_hides_insight_and_failed_publication_keeps_previous() -> None:
    _, ids = load_hbf_fixture()
    with rollback_session() as session:
        item = list_node_insights(session, ids["sk_hynix"], WINDOW)[0]
        rule = session.execute(
            sa.select(
                s.lint_policy_rule.c.lint_policy_rule_id,
                s.lint_policy_rule.c.lint_policy_version_id,
            )
            .where(s.lint_policy_rule.c.severity == "BLOCKING")
            .limit(1)
        ).one()
        now = datetime.now(UTC)
        batch_id = session.execute(
            s.promotion_batch.insert()
            .values(
                lint_policy_version_id=rule.lint_policy_version_id,
                promotion_status="COMMITTED",
                publication_status="FAILED",
                started_at=now - timedelta(seconds=1),
                committed_at=now,
                publication_failure_reason="검토용 실패",
            )
            .returning(s.promotion_batch.c.promotion_batch_id)
        ).scalar_one()
        session.execute(
            s.publication_affected_node.insert().values(
                promotion_batch_id=batch_id, node_id=ids["sk_hynix"]
            )
        )
        assert list_node_insights(session, ids["sk_hynix"], WINDOW)[0] == item
        claim_id = session.scalar(
            sa.select(s.node_insight_claim.c.claim_id).where(
                s.node_insight_claim.c.node_insight_id == item.node_insight_id
            )
        )
        run_id = session.execute(
            s.lint_run.insert()
            .values(
                lint_policy_version_id=rule.lint_policy_version_id,
                status="SUCCESS",
                started_at=now - timedelta(seconds=1),
                completed_at=now,
            )
            .returning(s.lint_run.c.lint_run_id)
        ).scalar_one()
        session.execute(
            s.lint_finding.insert().values(
                finding_key=sha256(f"insight-test:{claim_id}".encode()).digest(),
                knowledge_item_id=claim_id,
                lint_policy_rule_id=rule.lint_policy_rule_id,
                first_detected_run_id=run_id,
                latest_detected_run_id=run_id,
                first_detected_at=now,
                last_detected_at=now,
                message="검토용 차단",
            )
        )
        assert list_node_insights(session, ids["sk_hynix"], WINDOW) == []
        with pytest.raises(InsightNotFoundError):
            get_insight(session, item.node_insight_id)


@pytest.mark.parametrize(
    "days, expected",
    [
        (0, (0, 0)),
        (90, (1, 1)),
        (91, (0, 1)),
        (365, (0, 1)),
        (366, (0, 0)),
        (None, (0, 0)),
    ],
)
def test_evidence_count_uses_saved_as_of_and_half_open_window(
    days: int | None, expected: tuple[int, int]
) -> None:
    _, ids = load_hbf_fixture()
    with rollback_session() as session:
        item = list_node_insights(session, ids["sk_hynix"], WINDOW)[0]
        as_of = session.scalar(
            sa.select(s.node_insight.c.as_of_at).where(
                s.node_insight.c.node_insight_id == item.node_insight_id
            )
        )
        assert as_of is not None
        source_ids = (
            sa.select(s.observation.c.source_document_id)
            .select_from(
                s.node_insight_claim.join(
                    s.claim_observation,
                    s.claim_observation.c.claim_id == s.node_insight_claim.c.claim_id,
                ).join(s.observation)
            )
            .where(s.node_insight_claim.c.node_insight_id == item.node_insight_id)
        )
        session.execute(
            s.source_document.update()
            .where(s.source_document.c.source_document_id.in_(source_ids))
            .values(
                published_at=as_of - timedelta(days=days) if days is not None else None,
                published_precision="INSTANT" if days is not None else "UNKNOWN",
            )
        )
        actual = tuple(
            list_node_insights(session, ids["sk_hynix"], window)[0].evidence_group_count
            for window in (
                TimeWindow.RECENT_90_DAYS,
                TimeWindow.RECENT_1_YEAR,
            )
        )
        assert actual == expected
