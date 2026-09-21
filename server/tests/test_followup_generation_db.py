from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
import sqlalchemy as sa
from test_relations import rollback_session

from ontology_map import followup_generation as service
from ontology_map.db import followup_generation as db
from ontology_map.db import panel as panel_queries
from ontology_map.db import schema as s
from ontology_map.db.panel_fixture import load_panel_fixture
from ontology_map.followup_generation_contracts import (
    FollowupClaimReference,
    FollowupQuestionCandidate,
    FollowupQuestionsProposal,
    FollowupWindowProposal,
)


def _clear_question_sets(session, context_id: int) -> None:
    set_ids = sa.select(s.node_question_set.c.question_set_id).where(
        s.node_question_set.c.node_context_id == context_id
    )
    question_ids = sa.select(s.node_question.c.question_id).where(
        s.node_question.c.question_set_id.in_(set_ids)
    )
    session.execute(
        s.node_question_claim.delete().where(
            s.node_question_claim.c.question_id.in_(question_ids)
        )
    )
    session.execute(
        s.node_question.delete().where(s.node_question.c.question_set_id.in_(set_ids))
    )
    session.execute(
        s.node_question_set.delete().where(
            s.node_question_set.c.node_context_id == context_id
        )
    )


def _publication_batch(session, context_id: int) -> int:
    batch_id = session.scalar(
        sa.select(s.publication_affected_node.c.promotion_batch_id).where(
            s.publication_affected_node.c.node_context_id == context_id
        )
    )
    assert batch_id is not None
    return int(batch_id)


def _make_preparing(session, context_id: int) -> int:
    batch_id = _publication_batch(session, context_id)
    session.execute(
        s.promotion_batch.update()
        .where(s.promotion_batch.c.promotion_batch_id == batch_id)
        .values(publication_status="PREPARING", ready_at=None)
    )
    return batch_id


def _make_claim_background(session, claim_id: int, now: datetime) -> None:
    document_ids = (
        sa.select(s.observation.c.source_document_id)
        .join(
            s.claim_observation,
            s.claim_observation.c.observation_id == s.observation.c.observation_id,
        )
        .where(s.claim_observation.c.claim_id == claim_id)
    )
    session.execute(
        s.source_document.update()
        .where(s.source_document.c.source_document_id.in_(document_ids))
        .values(published_at=now - timedelta(days=200), published_precision="DAY")
    )


def _running_task(session, seed: bytes, now: datetime) -> int:
    version = (
        session.scalar(
            sa.select(sa.func.max(s.output_schema_definition.c.version_no)).where(
                s.output_schema_definition.c.task_kind == "FOLLOWUP_QUESTIONS"
            )
        )
        or 0
    ) + 1
    contract_id = session.scalar(
        s.output_schema_definition.insert()
        .values(
            task_kind="FOLLOWUP_QUESTIONS",
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
            task_kind="FOLLOWUP_QUESTIONS",
            input_hash=sha256(seed + b":input").digest(),
            output_schema_definition_id=contract_id,
            model_version=service.MODEL_VERSION,
            prompt_version=service.PROMPT_VERSION,
            cache_key=sha256(seed + b":cache").digest(),
            status="RUNNING",
            attempt_count=1,
            lease_owner="followup-test-worker",
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


def _ordinary_in_window_claim(prepared) -> int:
    conflict_claims = {
        claim_id
        for pair in prepared.agent_input.recent_90_days.conflict_pairs
        for claim_id in pair.claim_ids
    }
    return next(
        item.claim_id
        for item in prepared.agent_input.recent_90_days.claims
        if item.period_role == "IN_WINDOW" and item.claim_id not in conflict_claims
    )


def test_prepare_followup_requires_preparing_publication() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        with pytest.raises(db.FollowupPreparationError):
            db.prepare_followup(
                session,
                context_id,
                datetime.now(UTC),
            )


def test_apply_followup_persists_valid_partial_result_and_task_success() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        _make_preparing(session, context_id)
        _clear_question_sets(session, context_id)
        now = datetime.now(UTC)
        prepared = db.prepare_followup(session, context_id, now)
        claim_id = _ordinary_in_window_claim(prepared)
        task_id = _running_task(session, b"valid", now)
        proposal = FollowupQuestionsProposal(
            recent_90_days=FollowupWindowProposal(
                questions=(
                    FollowupQuestionCandidate(
                        display_order=1,
                        question_text="현재 자료에서 직접 확인되는 역할은 무엇인가요?",
                        answer_text=(
                            "현재 자료에서는 공동 사업과 연결된 역할을 "
                            "확인할 수 있습니다. "
                            "제공된 근거만으로 범위 밖 성과까지 판단할 수 없습니다."
                        ),
                        claims=(
                            FollowupClaimReference(
                                claim_id=claim_id,
                                role="KEY_CLAIM",
                                display_order=1,
                            ),
                        ),
                    ),
                    FollowupQuestionCandidate(
                        display_order=2,
                        question_text="검증되지 않은 Claim으로도 답할 수 있나요?",
                        answer_text=(
                            "검증되지 않은 근거는 사용할 수 없습니다. "
                            "이 후보는 전체 질문 단위로 제외되어야 합니다."
                        ),
                        claims=(
                            FollowupClaimReference(
                                claim_id=9223372036854775807,
                                role="KEY_CLAIM",
                                display_order=1,
                            ),
                        ),
                    ),
                )
            ),
            recent_1_year=FollowupWindowProposal(questions=()),
        )

        result = db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=proposal,
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "SUCCESS"
        assert result.stored_count == 1
        task = (
            session.execute(
                sa.select(s.model_task).where(s.model_task.c.model_task_id == task_id)
            )
            .mappings()
            .one()
        )
        assert task["status"] == "SUCCESS"
        assert task["lease_owner"] is None
        questions = (
            session.execute(
                sa.select(s.node_question).where(
                    s.node_question.c.question_set_id == result.question_set_ids[0]
                )
            )
            .mappings()
            .all()
        )
        assert len(questions) == 1
        assert questions[0]["section_id"] is None


def test_apply_followup_persists_normal_empty_as_success() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        _make_preparing(session, context_id)
        _clear_question_sets(session, context_id)
        now = datetime.now(UTC)
        prepared = db.prepare_followup(session, context_id, now)
        task_id = _running_task(session, b"empty", now)
        result = db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "SUCCESS"
        assert result.stored_count == 0
        assert result.question_set_ids is not None
        count = session.scalar(
            sa.select(sa.func.count())
            .select_from(s.node_question)
            .where(s.node_question.c.question_set_id.in_(result.question_set_ids))
        )
        assert count == 0


def test_apply_followup_blocks_when_all_candidates_fail_validation() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        _make_preparing(session, context_id)
        _clear_question_sets(session, context_id)
        now = datetime.now(UTC)
        prepared = db.prepare_followup(session, context_id, now)
        task_id = _running_task(session, b"all-blocked", now)
        proposal = FollowupQuestionsProposal(
            recent_90_days=FollowupWindowProposal(
                questions=(
                    FollowupQuestionCandidate(
                        display_order=1,
                        question_text="검증되지 않은 Claim만으로 답할 수 있나요?",
                        answer_text=(
                            "이 후보는 준비된 입력 밖의 Claim을 참조합니다. "
                            "따라서 질문 전체가 저장 대상에서 제외되어야 합니다."
                        ),
                        claims=(
                            FollowupClaimReference(
                                claim_id=9223372036854775807,
                                role="KEY_CLAIM",
                                display_order=1,
                            ),
                        ),
                    ),
                )
            ),
            recent_1_year=FollowupWindowProposal(questions=()),
        )

        result = db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=proposal,
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.reason == "ALL_CANDIDATES_BLOCKED"
        assert result.question_set_ids is None
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question_set)
                .where(s.node_question_set.c.model_task_id == task_id)
            )
            == 0
        )


def test_all_period_role_invalid_candidates_block_task() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        _make_preparing(session, context_id)
        _clear_question_sets(session, context_id)
        now = datetime.now(UTC)
        initial = db.prepare_followup(session, context_id, now)
        background_claim = _ordinary_in_window_claim(initial)
        _make_claim_background(session, background_claim, now)
        prepared = db.prepare_followup(session, context_id, now)
        assert (
            next(
                item
                for item in prepared.agent_input.recent_90_days.claims
                if item.claim_id == background_claim
            ).period_role
            == "BACKGROUND"
        )
        supporting_claim = _ordinary_in_window_claim(prepared)
        task_id = _running_task(session, b"period-all-blocked", now)
        proposal = FollowupQuestionsProposal(
            recent_90_days=FollowupWindowProposal(
                questions=(
                    FollowupQuestionCandidate(
                        display_order=1,
                        question_text="기간 밖 근거를 핵심 근거로 쓸 수 있나요?",
                        answer_text="기간 밖 근거는 핵심 근거가 아닙니다.",
                        claims=(
                            FollowupClaimReference(
                                claim_id=background_claim,
                                role="KEY_CLAIM",
                                display_order=1,
                            ),
                            FollowupClaimReference(
                                claim_id=supporting_claim,
                                role="SUPPORTING_CLAIM",
                                display_order=2,
                            ),
                        ),
                    ),
                )
            ),
            recent_1_year=FollowupWindowProposal(questions=()),
        )
        result = db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=proposal,
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.reason == "ALL_CANDIDATES_BLOCKED"
        assert result.question_set_ids is None


def test_apply_followup_blocks_stale_basis_without_writing_result() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        _make_preparing(session, context_id)
        _clear_question_sets(session, context_id)
        now = datetime.now(UTC)
        prepared = db.prepare_followup(session, context_id, now)
        claim_id = prepared.agent_input.recent_90_days.claims[0].claim_id
        task_id = _running_task(session, b"stale", now)
        session.execute(
            s.knowledge_item.update()
            .where(s.knowledge_item.c.knowledge_item_id == claim_id)
            .values(current_state="ON_HOLD")
        )
        result = db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.reason == "STALE_INPUT"
        assert result.question_set_ids is None
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question_set)
                .where(s.node_question_set.c.model_task_id == task_id)
            )
            == 0
        )


def test_apply_followup_blocks_if_preparing_publication_becomes_ready() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        batch_id = _make_preparing(session, context_id)
        _clear_question_sets(session, context_id)
        now = datetime.now(UTC)
        prepared = db.prepare_followup(session, context_id, now)
        task_id = _running_task(session, b"publication-ready", now)
        session.execute(
            s.promotion_batch.update()
            .where(s.promotion_batch.c.promotion_batch_id == batch_id)
            .values(publication_status="READY", ready_at=now)
        )
        result = db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.reason == "STALE_PUBLICATION"
        assert result.question_set_ids is None


def test_apply_followup_blocks_if_publication_context_pointer_changes() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        batch_id = _make_preparing(session, context_id)
        _clear_question_sets(session, context_id)
        now = datetime.now(UTC)
        prepared = db.prepare_followup(session, context_id, now)
        task_id = _running_task(session, b"pointer-stale", now)
        session.execute(
            s.publication_affected_node.update()
            .where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == prepared.agent_input.node_id,
            )
            .values(node_context_id=None)
        )
        result = db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.reason == "STALE_PUBLICATION"
        assert result.question_set_ids is None


def test_stale_publication_preserves_previous_ready_question_sets() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        old_set_ids = tuple(
            session.scalars(
                sa.select(s.node_question_set.c.question_set_id)
                .where(s.node_question_set.c.node_context_id == context_id)
                .order_by(s.node_question_set.c.question_set_id)
            ).all()
        )
        assert old_set_ids
        batch_id = _make_preparing(session, context_id)
        now = datetime.now(UTC)
        prepared = db.prepare_followup(session, context_id, now)
        task_id = _running_task(session, b"preserve-ready", now)
        session.execute(
            s.promotion_batch.update()
            .where(s.promotion_batch.c.promotion_batch_id == batch_id)
            .values(publication_status="READY", ready_at=now)
        )
        result = db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "VALIDATION_BLOCKED"
        remaining = tuple(
            session.scalars(
                sa.select(s.node_question_set.c.question_set_id)
                .where(s.node_question_set.c.node_context_id == context_id)
                .order_by(s.node_question_set.c.question_set_id)
            ).all()
        )
        assert remaining == old_set_ids
