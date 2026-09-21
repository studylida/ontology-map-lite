from datetime import UTC, datetime, timedelta
from hashlib import sha256

import sqlalchemy as sa
from test_relations import rollback_session

from ontology_map import insight_generation as service
from ontology_map.db import insight_generation as db
from ontology_map.db import schema as s
from ontology_map.db.panel_fixture import load_panel_fixture
from ontology_map.insight_generation_contracts import (
    InsightBundleProposal,
    InsightClaimReference,
    InsightReportCandidate,
    InsightSectionCandidate,
    InsightWindowProposal,
)


def _latest_ready_publication(session, node_id: int) -> dict:
    return dict(
        session.execute(
            sa.select(s.publication_affected_node)
            .join(
                s.promotion_batch,
                s.promotion_batch.c.promotion_batch_id
                == s.publication_affected_node.c.promotion_batch_id,
            )
            .where(
                s.publication_affected_node.c.node_id == node_id,
                s.promotion_batch.c.promotion_status == "COMMITTED",
                s.promotion_batch.c.publication_status == "READY",
            )
            .order_by(
                s.promotion_batch.c.ready_at.desc(),
                s.promotion_batch.c.promotion_batch_id.desc(),
            )
            .limit(1)
        )
        .mappings()
        .one()
    )


def _preparing_publication(session, node_id: int, now: datetime) -> tuple[int, dict]:
    ready = _latest_ready_publication(session, node_id)
    policy = session.scalar(
        sa.select(s.promotion_batch.c.lint_policy_version_id).where(
            s.promotion_batch.c.promotion_batch_id == ready["promotion_batch_id"]
        )
    )
    batch_id = session.scalar(
        s.promotion_batch.insert()
        .values(
            lint_policy_version_id=policy,
            promotion_status="COMMITTED",
            publication_status="PREPARING",
            started_at=now,
            committed_at=now,
        )
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    assert batch_id is not None
    preparing = dict(ready)
    preparing["promotion_batch_id"] = batch_id
    session.execute(s.publication_affected_node.insert().values(**preparing))
    return int(batch_id), ready


def _running_task(session, seed: bytes, now: datetime) -> int:
    version = (
        session.scalar(
            sa.select(sa.func.max(s.output_schema_definition.c.version_no)).where(
                s.output_schema_definition.c.task_kind == "NODE_INSIGHT"
            )
        )
        or 0
    ) + 1
    contract_id = session.scalar(
        s.output_schema_definition.insert()
        .values(
            task_kind="NODE_INSIGHT",
            version_no=version,
            schema_json=service.output_schema(),
            is_active=False,
        )
        .returning(s.output_schema_definition.c.output_schema_definition_id)
    )
    assert contract_id is not None
    task_id = session.scalar(
        s.model_task.insert()
        .values(
            task_kind="NODE_INSIGHT",
            input_hash=sha256(seed + b":input").digest(),
            output_schema_definition_id=contract_id,
            model_version=service.MODEL_VERSION,
            prompt_version=service.PROMPT_VERSION,
            cache_key=sha256(seed + b":cache").digest(),
            status="RUNNING",
            attempt_count=1,
            lease_owner="insight-test-worker",
            lease_expires_at=now + timedelta(minutes=10),
        )
        .returning(s.model_task.c.model_task_id)
    )
    assert task_id is not None
    session.execute(
        s.agent_attempt.insert().values(
            model_task_id=task_id,
            attempt_no=1,
            outcome="SUCCESS",
            attempted_at=now,
        )
    )
    return int(task_id)


def _available_claims(prepared, *, year: bool = False) -> list[int]:
    window = (
        prepared.agent_input.recent_1_year
        if year
        else prepared.agent_input.recent_90_days
    )
    conflict_claims = {
        claim_id for pair in window.conflict_pairs for claim_id in pair.claim_ids
    }
    return [
        item.claim_id
        for item in window.claims
        if item.period_role == "IN_WINDOW" and item.claim_id not in conflict_claims
    ]


def _report(
    claim_ids: list[int],
    *,
    question: str,
    invalid_single_claim: bool = False,
) -> InsightReportCandidate:
    first = InsightSectionCandidate(
        display_order=1,
        title="첫 번째 주요 발견",
        synthesis_text="두 근거를 함께 보면 첫 번째 흐름을 확인할 수 있습니다.",
        claims=(
            InsightClaimReference(
                claim_id=claim_ids[0], role="KEY_CLAIM", display_order=1
            ),
        )
        if invalid_single_claim
        else (
            InsightClaimReference(
                claim_id=claim_ids[0], role="KEY_CLAIM", display_order=1
            ),
            InsightClaimReference(
                claim_id=claim_ids[1],
                role="SUPPORTING_CLAIM",
                display_order=2,
            ),
        ),
    )
    sections = [first]
    if not invalid_single_claim and len(claim_ids) >= 3:
        sections.append(
            InsightSectionCandidate(
                display_order=2,
                title="두 번째 주요 발견",
                synthesis_text=(
                    "다른 근거 조합에서는 두 번째 흐름을 확인할 수 있습니다."
                ),
                claims=(
                    InsightClaimReference(
                        claim_id=claim_ids[1],
                        role="CONTRASTING_CLAIM",
                        display_order=1,
                    ),
                    InsightClaimReference(
                        claim_id=claim_ids[2], role="KEY_CLAIM", display_order=2
                    ),
                ),
            )
        )
    return InsightReportCandidate(
        analysis_question_text=question,
        title="공개 근거에서 확인되는 주요 흐름",
        summary_text="공개 근거를 종합하면 두 가지 주요 흐름을 확인할 수 있습니다.",
        synthesis_text=(
            "주요 발견을 함께 보면 현재 자료가 보여주는 범위를 설명할 수 있습니다."
        ),
        caveat_text="현재 공개 자료 밖의 결과와 장기 성과는 확인할 수 없습니다.",
        sections=tuple(sections),
    )


def _bundle(
    ninety: InsightReportCandidate | None,
    year: InsightReportCandidate | None,
) -> InsightBundleProposal:
    return InsightBundleProposal(
        recent_90_days=InsightWindowProposal(report=ninety),
        recent_1_year=InsightWindowProposal(report=year),
    )


def test_apply_bundle_persists_report_empty_union_pointer_and_success() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, previous = _preparing_publication(session, ids["gaon"], now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        claim_ids = _available_claims(prepared)
        assert len(claim_ids) >= 3
        report = _report(
            claim_ids,
            question="최근 90일에 확인되는 핵심 흐름은 무엇인가요?",
        )
        task_id = _running_task(session, b"report-empty", now)

        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(report, None),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "SUCCESS"
        assert result.report_ids[0] is not None
        assert result.report_ids[1] is None
        assert (
            session.scalar(
                sa.select(s.model_task.c.status).where(
                    s.model_task.c.model_task_id == task_id
                )
            )
            == "SUCCESS"
        )
        windows = (
            session.execute(
                sa.select(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == task_id)
                .order_by(s.node_insight_window.c.time_window)
            )
            .mappings()
            .all()
        )
        assert len(windows) == 2
        assert sum(row["node_insight_id"] is not None for row in windows) == 1

        report_id = int(result.report_ids[0])
        report_claims = (
            session.execute(
                sa.select(s.node_insight_claim)
                .where(s.node_insight_claim.c.node_insight_id == report_id)
                .order_by(s.node_insight_claim.c.display_order)
            )
            .mappings()
            .all()
        )
        section_claim_ids = {
            int(row[0])
            for row in session.execute(
                sa.select(s.node_insight_section_claim.c.claim_id)
                .join(s.node_insight_section)
                .where(s.node_insight_section.c.node_insight_id == report_id)
            ).all()
        }
        assert {int(row["claim_id"]) for row in report_claims} == section_claim_ids
        reused = next(row for row in report_claims if row["claim_id"] == claim_ids[1])
        assert reused["role"] == "SUPPORTING_CLAIM"

        pointer = session.scalar(
            sa.select(s.publication_affected_node.c.node_insight_model_task_id).where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == ids["gaon"],
            )
        )
        assert pointer == task_id
        old_pointer = session.scalar(
            sa.select(s.publication_affected_node.c.node_insight_model_task_id).where(
                s.publication_affected_node.c.promotion_batch_id
                == previous["promotion_batch_id"],
                s.publication_affected_node.c.node_id == ids["gaon"],
            )
        )
        assert old_pointer == previous["node_insight_model_task_id"]


def test_apply_bundle_persists_empty_empty_as_success() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, _ = _preparing_publication(session, ids["empty"], now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["empty"],
            as_of_at=now,
        )
        assert not prepared.agent_input.recent_90_days.claims
        task_id = _running_task(session, b"empty-empty", now)
        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(None, None),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "SUCCESS"
        assert result.report_ids == (None, None)
        windows = (
            session.execute(
                sa.select(s.node_insight_window).where(
                    s.node_insight_window.c.model_task_id == task_id
                )
            )
            .mappings()
            .all()
        )
        assert len(windows) == 2
        assert all(row["node_insight_id"] is None for row in windows)


def test_one_invalid_window_blocks_entire_new_bundle_and_preserves_pointer() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, _ = _preparing_publication(session, ids["gaon"], now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        ninety_ids = _available_claims(prepared)
        year_ids = _available_claims(prepared, year=True)
        task_id = _running_task(session, b"atomic-block", now)
        prior_pointer = prepared.prior_insight_model_task_id
        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(
                _report(
                    ninety_ids,
                    question="최근 90일 흐름은 무엇인가요?",
                ),
                _report(
                    year_ids,
                    question="지난 1년 흐름은 무엇인가요?",
                    invalid_single_claim=True,
                ),
            ),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.report_ids == (None, None)
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == task_id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight)
                .where(s.node_insight.c.model_task_id == task_id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(
                    s.publication_affected_node.c.node_insight_model_task_id
                ).where(
                    s.publication_affected_node.c.promotion_batch_id == batch_id,
                    s.publication_affected_node.c.node_id == ids["gaon"],
                )
            )
            == prior_pointer
        )


def test_stale_basis_blocks_bundle_without_changing_pointer() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, _ = _preparing_publication(session, ids["gaon"], now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        claim_id = prepared.agent_input.recent_90_days.claims[0].claim_id
        task_id = _running_task(session, b"stale-basis", now)
        session.execute(
            s.knowledge_item.update()
            .where(s.knowledge_item.c.knowledge_item_id == claim_id)
            .values(current_state="ON_HOLD")
        )
        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(None, None),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.reason == "STALE_INPUT"
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == task_id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(
                    s.publication_affected_node.c.node_insight_model_task_id
                ).where(
                    s.publication_affected_node.c.promotion_batch_id == batch_id,
                    s.publication_affected_node.c.node_id == ids["gaon"],
                )
            )
            == prepared.prior_insight_model_task_id
        )


def test_changed_publication_pointer_blocks_bundle_without_overwrite() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, _ = _preparing_publication(session, ids["gaon"], now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        task_id = _running_task(session, b"stale-publication", now)
        changed_pointer = (
            None if prepared.prior_insight_model_task_id is not None else task_id
        )
        session.execute(
            s.publication_affected_node.update()
            .where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == ids["gaon"],
            )
            .values(node_insight_model_task_id=changed_pointer)
        )

        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(None, None),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.reason == "STALE_PUBLICATION"
        assert result.report_ids == (None, None)
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == task_id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(
                    s.publication_affected_node.c.node_insight_model_task_id
                ).where(
                    s.publication_affected_node.c.promotion_batch_id == batch_id,
                    s.publication_affected_node.c.node_id == ids["gaon"],
                )
            )
            == changed_pointer
        )
