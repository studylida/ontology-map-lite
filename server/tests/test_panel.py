from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
import sqlalchemy as sa
from test_relations import request, rollback_session

from ontology_map import panel
from ontology_map.db import panel as queries
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.panel_fixture import load_panel_fixture
from ontology_map.exploration import TimeWindow
from ontology_map.pagination import InvalidCursorError

WINDOW = TimeWindow.RECENT_90_DAYS


def test_http_question_answer_report_and_claim_trace() -> None:
    _, ids = load_panel_fixture()
    base = f"/api/v1/nodes/{ids['gaon']}"
    status, page = request(f"{base}/questions?time_window={WINDOW}")
    assert status == 200 and len(page["items"]) == 4 and page["next_cursor"]
    query = urlencode({"time_window": WINDOW, "cursor": page["next_cursor"]})
    status, second = request(f"{base}/questions?{query}")
    assert status == 200 and len(second["items"]) == 2 and second["next_cursor"] is None
    qid = page["items"][3]["question_id"]
    status, answer = request(f"/api/v1/questions/{qid}")
    assert status == 200 and answer["section_id"] and len(answer["claims"]) == 2
    assert "9월" in answer["answer"] and "10월" in answer["answer"]
    assert "target_node_id" not in answer
    params = urlencode({"time_window": WINDOW, "as_of_at": answer["as_of_at"]})
    status, trace = request(
        f"{base}/claims/{answer['claims'][0]['claim_id']}/evidence?{params}"
    )
    assert status == 200 and trace["items"][0]["period_role"] == "IN_WINDOW"
    assert trace["items"][0]["locator"]["end_char"] > 0
    status, report = request(f"{base}/insight-report?time_window={WINDOW}&detail=true")
    assert status == 200 and len(report["items"]) == 1
    assert len(report["items"][0]["sections"]) == 2
    assert answer["section_id"] == report["items"][0]["sections"][0]["section_id"]
    assert request(f"{base}/questions?time_window=ALL")[0] == 422
    assert request("/api/v1/questions/9223372036854775808")[0] == 422
    assert (
        request(f"{base}/claims/1/evidence?time_window={WINDOW}&as_of_at=2026-01-01")[0]
        == 422
    )


def test_empty_is_distinct_from_legacy_or_unprepared() -> None:
    _, ids = load_panel_fixture()
    _, legacy = load_hbf_fixture()
    for path in ("questions", "insight-report"):
        status, body = request(
            f"/api/v1/nodes/{ids['empty']}/{path}?time_window={WINDOW}"
        )
        assert status == 200 and body["items"] == []
        status, body = request(
            f"/api/v1/nodes/{legacy['hbf']}/{path}?time_window={WINDOW}"
        )
        assert status == 503 and body["error"]["code"] == "PANEL_NOT_READY"


@pytest.mark.parametrize("state", ["ON_HOLD", "REJECTED"])
def test_hidden_basis_hides_entire_generated_results_and_direct_trace(
    state: str,
) -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        page = panel.list_questions(session, ids["gaon"], WINDOW, None)
        qid = int(page["items"][0]["question_id"])
        answer = panel.read_question(session, qid)
        claim_id = int(answer["claims"][0]["claim_id"])
        session.execute(
            s.knowledge_item.update()
            .where(s.knowledge_item.c.knowledge_item_id == claim_id)
            .values(current_state=state)
        )
        with pytest.raises(panel.PanelNotReadyError):
            panel.list_questions(session, ids["gaon"], WINDOW, None)
        with pytest.raises(panel.PanelNotReadyError):
            panel.read_question(session, qid)
        with pytest.raises(panel.PanelNotReadyError):
            panel.read_report(session, ids["gaon"], WINDOW, detail=True)
        with pytest.raises(panel.PanelNotReadyError):
            panel.claim_evidence(
                session, ids["gaon"], claim_id, WINDOW, answer["as_of_at"], None
            )
        with pytest.raises(panel.PanelNotReadyError):
            panel.list_claims(session, ids["gaon"], WINDOW, None, 10)


def test_question_pages_have_no_total_cap_and_cursor_is_scoped() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        page = panel.list_questions(session, ids["gaon"], WINDOW, None)
        original = queries.question(session, int(page["items"][0]["question_id"]))
        assert original is not None
        reference = queries.question_claims(session, original["question_id"])[0]
        for order in range(7, 14):
            qid = session.scalar(
                s.node_question.insert()
                .values(
                    question_set_id=original["question_set_id"],
                    display_order=order,
                    question_text=f"페이지 경계 시험 {order}",
                    answer_text="페이지 검증용 답변",
                )
                .returning(s.node_question.c.question_id)
            )
            session.execute(
                s.node_question_claim.insert().values(
                    question_id=qid,
                    claim_id=reference["claim_id"],
                    role="KEY_CLAIM",
                    display_order=1,
                )
            )
        seen = list(page["items"])
        cursor = page["next_cursor"]
        while cursor:
            result = panel.list_questions(session, ids["gaon"], WINDOW, cursor)
            seen.extend(result["items"])
            cursor = result["next_cursor"]
        assert len(seen) == 13 and len({q["question_id"] for q in seen}) == 13
        with pytest.raises(InvalidCursorError):
            panel.list_questions(
                session, ids["gaon"], TimeWindow.RECENT_1_YEAR, page["next_cursor"]
            )
        with pytest.raises(InvalidCursorError):
            panel.list_questions(session, ids["nuri"], WINDOW, page["next_cursor"])


def test_foreign_basis_references_are_rejected_even_if_public() -> None:
    _, ids = load_panel_fixture()
    _, legacy = load_hbf_fixture()
    with rollback_session() as session:
        page = panel.list_questions(session, ids["gaon"], WINDOW, None)
        qid = int(page["items"][0]["question_id"])
        foreign = panel.list_claims(session, legacy["sk_hynix"], WINDOW, None, 10)[
            "items"
        ][0]
        session.execute(
            s.node_question_claim.insert().values(
                question_id=qid,
                claim_id=int(foreign["claim_id"]),
                role="SUPPORTING_CLAIM",
                display_order=2,
            )
        )
        with pytest.raises(panel.PanelNotReadyError):
            panel.read_question(session, qid)
        with pytest.raises(panel.PanelNotFoundError):
            panel.claim_evidence(
                session,
                ids["gaon"],
                int(foreign["claim_id"]),
                WINDOW,
                datetime.now(UTC),
                None,
            )


def test_period_counts_background_unknown_and_event_claims() -> None:
    _, ids = load_panel_fixture()
    _, legacy = load_hbf_fixture()
    with rollback_session() as session:
        result = panel.list_claims(session, ids["gaon"], WINDOW, None, 1)
        claim_id = int(result["items"][0]["claim_id"])
        as_of = result["items"][0]["as_of_at"]
        source_ids = (
            sa.select(s.observation.c.source_document_id)
            .join(s.claim_observation)
            .where(s.claim_observation.c.claim_id == claim_id)
        )
        session.execute(
            s.source_document.update()
            .where(s.source_document.c.source_document_id.in_(source_ids))
            .values(published_at=as_of - timedelta(days=100))
        )
        assert claim_id not in {
            int(c["claim_id"])
            for c in panel.list_claims(session, ids["gaon"], WINDOW, None, 20)["items"]
        }
        assert claim_id in {
            int(c["claim_id"])
            for c in panel.list_claims(
                session, ids["gaon"], TimeWindow.RECENT_1_YEAR, None, 20
            )["items"]
        }
        assert (
            panel.claim_evidence(session, ids["gaon"], claim_id, WINDOW, as_of, None)[
                "items"
            ][0]["period_role"]
            == "BACKGROUND"
        )
        session.execute(
            s.source_document.update()
            .where(s.source_document.c.source_document_id.in_(source_ids))
            .values(published_at=None, published_precision="UNKNOWN")
        )
        assert (
            panel.claim_evidence(session, ids["gaon"], claim_id, WINDOW, as_of, None)[
                "items"
            ][0]["period_role"]
            == "UNKNOWN"
        )
        event = panel.list_claims(session, legacy["fms_2026"], WINDOW, None, 20)
        assert any(
            any(c["kind"] == "EVENT_TIME" for c in item["connections"])
            for item in event["items"]
        )


def test_report_mismatched_reference_is_hidden() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        report = panel.read_report(session, ids["gaon"], WINDOW, detail=True)["items"][
            0
        ]
        session.execute(
            s.node_insight_window.update()
            .where(s.node_insight_window.c.node_id == ids["gaon"])
            .values(as_of_at=datetime.now(UTC))
        )
        with pytest.raises(panel.PanelNotReadyError):
            panel.read_report(session, ids["gaon"], WINDOW, detail=True)
        assert report["sections"]


def test_invalid_cursor_timestamp_returns_client_error() -> None:
    from ontology_map.pagination import encode_cursor

    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = queries.context(session, ids["gaon"])
        assert context is not None
        cursor = encode_cursor(
            "panel-claims",
            {
                "node_id": ids["gaon"],
                "window": WINDOW.value,
                "document_id": context["node_search_document_id"],
            },
            [1, "0001-01-01T00:00:00+00:00"],
        )
        with pytest.raises(InvalidCursorError):
            panel.list_claims(session, ids["gaon"], WINDOW, cursor, 10)


def test_node_attribute_claim_and_same_lineage_deduplication() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        item = panel.list_claims(session, ids["gaon"], WINDOW, None, 10)["items"][0]
        claim_id = int(item["claim_id"])
        kind = session.scalar(
            sa.select(s.node.c.node_type_id).where(s.node.c.node_id == ids["gaon"])
        )
        attr = session.scalar(
            s.attribute.insert()
            .values(attribute_code="panel-test-role")
            .returning(s.attribute.c.attribute_id)
        )
        revision = session.scalar(
            s.attribute_revision.insert()
            .values(
                attribute_id=attr,
                version_no=1,
                display_name="담당 역할",
                target_node_type_id=kind,
                allowed_value_kind="STRING",
            )
            .returning(s.attribute_revision.c.attribute_revision_id)
        )
        session.execute(
            s.claim_attribute_value.insert().values(
                claim_id=claim_id,
                target_node_id=ids["gaon"],
                attribute_revision_id=revision,
                value_kind="STRING",
                string_value="제어 소프트웨어",
                date_from_precision="UNKNOWN",
                date_to_precision="UNKNOWN",
            )
        )
        obs = (
            session.execute(
                sa.select(s.observation)
                .join(s.claim_observation)
                .where(s.claim_observation.c.claim_id == claim_id)
            )
            .mappings()
            .first()
        )
        assert obs is not None
        source = dict(
            session.execute(
                sa.select(s.source_document).where(
                    s.source_document.c.source_document_id == obs["source_document_id"]
                )
            )
            .mappings()
            .one()
        )
        source.pop("source_document_id")
        source["version_no"] += 1
        doc = session.scalar(
            s.source_document.insert()
            .values(**source)
            .returning(s.source_document.c.source_document_id)
        )
        duplicate = dict(obs)
        duplicate.pop("observation_id")
        duplicate["source_document_id"] = doc
        obs_id = session.scalar(
            s.observation.insert()
            .values(**duplicate)
            .returning(s.observation.c.observation_id)
        )
        session.execute(
            s.claim_observation.insert().values(
                claim_id=claim_id, observation_id=obs_id
            )
        )
        rows = panel.list_claims(session, ids["gaon"], WINDOW, None, 20)["items"]
        matches = [r for r in rows if int(r["claim_id"]) == claim_id]
        assert len(matches) == 1 and matches[0]["evidence_group_count"] == 1
        assert any(
            c["kind"] == "ATTRIBUTE" and c["label"] == "담당 역할"
            for c in matches[0]["connections"]
        )


def test_failed_new_publication_preserves_selected_questions() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        original = panel.list_questions(session, ids["gaon"], WINDOW, None)
        now = datetime.now(UTC)
        policy = session.scalar(
            sa.select(s.lint_policy_version.c.lint_policy_version_id).where(
                s.lint_policy_version.c.is_active
            )
        )
        batch = session.scalar(
            s.promotion_batch.insert()
            .values(
                lint_policy_version_id=policy,
                promotion_status="COMMITTED",
                publication_status="FAILED",
                publication_failure_reason="개발용 실패 시나리오",
                started_at=now,
                committed_at=now,
            )
            .returning(s.promotion_batch.c.promotion_batch_id)
        )
        session.execute(
            s.publication_affected_node.insert().values(
                promotion_batch_id=batch, node_id=ids["gaon"]
            )
        )
        assert panel.list_questions(session, ids["gaon"], WINDOW, None) == original
        assert panel.read_report(session, ids["gaon"], WINDOW, detail=True)["items"]
