from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import followup_generation as product
from ontology_map.db import followup_tasks
from ontology_map.db import schema as s
from ontology_map.db.panel_fixture import load_panel_fixture
from ontology_map.db.session import get_engine
from ontology_map.durable_provider import ConfirmedProviderFailure
from ontology_map.followup_generation_contracts import (
    FollowupClaimReference,
    FollowupQuestionCandidate,
    FollowupQuestionsProposal,
    FollowupWindowProposal,
    PreparedFollowup,
)
from ontology_map.followup_runner import run_followup
from ontology_map.model_studio import CallFailed


@dataclass
class FollowupCase:
    engine: sa.Engine
    batch_id: int
    node_id: int
    document_id: int
    old_context_id: int
    context_id: int
    old_question_set_ids: tuple[int, ...]

    def ensure(self, as_of_at: datetime) -> followup_tasks.EnqueuedFollowup:
        with Session(self.engine) as session, session.begin():
            return followup_tasks.enqueue_followup(
                session,
                self.context_id,
                as_of_at,
            )


def _digest(label: str) -> bytes:
    return sha256(f"followup-runner:{label}:{uuid4().hex}".encode()).digest()


@pytest.fixture
def followup_case() -> FollowupCase:
    _, ids = load_panel_fixture()
    engine = get_engine()
    now = datetime.now(UTC)

    with engine.begin() as connection:
        publication = (
            connection.execute(
                sa.select(s.publication_affected_node)
                .where(s.publication_affected_node.c.node_id == ids["gaon"])
                .order_by(s.publication_affected_node.c.promotion_batch_id.desc())
                .limit(1)
            )
            .mappings()
            .one()
        )
        batch_id = int(publication["promotion_batch_id"])
        node_id = int(publication["node_id"])
        document_id = int(publication["node_search_document_id"])
        old_context_id = int(publication["node_context_id"])
        batch = (
            connection.execute(
                sa.select(s.promotion_batch).where(
                    s.promotion_batch.c.promotion_batch_id == batch_id
                )
            )
            .mappings()
            .one()
        )
        old_publication_status = str(batch["publication_status"])
        old_ready_at = batch["ready_at"]
        old_question_set_ids = tuple(
            int(value)
            for value in connection.scalars(
                sa.select(s.node_question_set.c.question_set_id)
                .where(s.node_question_set.c.node_context_id == old_context_id)
                .order_by(s.node_question_set.c.question_set_id)
            ).all()
        )
        assert old_question_set_ids

        active_followup = (
            connection.execute(
                sa.select(s.output_schema_definition).where(
                    s.output_schema_definition.c.task_kind == "FOLLOWUP_QUESTIONS",
                    s.output_schema_definition.c.is_active,
                )
            )
            .mappings()
            .one()
        )
        old_followup_contract_id = int(active_followup["output_schema_definition_id"])
        next_followup_version = (
            int(
                connection.scalar(
                    sa.select(
                        sa.func.max(s.output_schema_definition.c.version_no)
                    ).where(
                        s.output_schema_definition.c.task_kind == "FOLLOWUP_QUESTIONS"
                    )
                )
                or 0
            )
            + 1
        )
        connection.execute(
            s.output_schema_definition.update()
            .where(
                s.output_schema_definition.c.output_schema_definition_id
                == old_followup_contract_id
            )
            .values(is_active=False)
        )
        followup_contract_id = int(
            connection.scalar(
                s.output_schema_definition.insert()
                .values(
                    task_kind="FOLLOWUP_QUESTIONS",
                    version_no=next_followup_version,
                    schema_json=product.output_schema(),
                    is_active=True,
                )
                .returning(s.output_schema_definition.c.output_schema_definition_id)
            )
        )

        context_contract_id = int(
            connection.scalar(
                sa.select(s.output_schema_definition.c.output_schema_definition_id)
                .where(
                    s.output_schema_definition.c.task_kind == "NODE_CONTEXT",
                    s.output_schema_definition.c.is_active,
                )
                .limit(1)
            )
        )
        context_input = _digest("context-input")
        context_task_id = int(
            connection.scalar(
                s.model_task.insert()
                .values(
                    task_kind="NODE_CONTEXT",
                    input_hash=context_input,
                    output_schema_definition_id=context_contract_id,
                    model_version="followup-runner-test",
                    prompt_version="followup-runner-test",
                    cache_key=_digest("context-cache"),
                    status="SUCCESS",
                    attempt_count=1,
                    finished_at=now,
                )
                .returning(s.model_task.c.model_task_id)
            )
        )
        connection.execute(
            s.agent_attempt.insert().values(
                model_task_id=context_task_id,
                attempt_no=1,
                outcome="SUCCESS",
                attempted_at=now,
            )
        )
        context_id = int(
            connection.scalar(
                s.node_context.insert()
                .values(
                    node_id=node_id,
                    node_search_document_id=document_id,
                    model_task_id=context_task_id,
                    language="ko",
                    context_text="durable FOLLOWUP runner 테스트용 교체 context",
                )
                .returning(s.node_context.c.node_context_id)
            )
        )
        connection.execute(
            s.promotion_batch.update()
            .where(s.promotion_batch.c.promotion_batch_id == batch_id)
            .values(publication_status="PREPARING", ready_at=None)
        )
        connection.execute(
            s.publication_affected_node.update()
            .where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == node_id,
            )
            .values(node_context_id=context_id)
        )

    case = FollowupCase(
        engine=engine,
        batch_id=batch_id,
        node_id=node_id,
        document_id=document_id,
        old_context_id=old_context_id,
        context_id=context_id,
        old_question_set_ids=old_question_set_ids,
    )
    try:
        yield case
    finally:
        with engine.begin() as connection:
            connection.execute(
                s.publication_affected_node.update()
                .where(
                    s.publication_affected_node.c.promotion_batch_id == batch_id,
                    s.publication_affected_node.c.node_id == node_id,
                )
                .values(node_context_id=old_context_id)
            )
            connection.execute(
                s.promotion_batch.update()
                .where(s.promotion_batch.c.promotion_batch_id == batch_id)
                .values(
                    publication_status=old_publication_status,
                    ready_at=old_ready_at,
                )
            )

            set_ids = sa.select(s.node_question_set.c.question_set_id).where(
                s.node_question_set.c.node_context_id == context_id
            )
            question_ids = sa.select(s.node_question.c.question_id).where(
                s.node_question.c.question_set_id.in_(set_ids)
            )
            connection.execute(
                s.node_question_claim.delete().where(
                    s.node_question_claim.c.question_id.in_(question_ids)
                )
            )
            connection.execute(
                s.node_question.delete().where(
                    s.node_question.c.question_set_id.in_(set_ids)
                )
            )
            connection.execute(
                s.node_question_set.delete().where(
                    s.node_question_set.c.node_context_id == context_id
                )
            )

            followup_task_ids = list(
                connection.scalars(
                    sa.select(s.model_task.c.model_task_id).where(
                        s.model_task.c.output_schema_definition_id
                        == followup_contract_id
                    )
                ).all()
            )
            if followup_task_ids:
                connection.execute(
                    s.provider_call_slot.delete().where(
                        s.provider_call_slot.c.model_task_id.in_(followup_task_ids)
                    )
                )
                connection.execute(
                    s.agent_attempt.delete().where(
                        s.agent_attempt.c.model_task_id.in_(followup_task_ids)
                    )
                )
                connection.execute(
                    s.model_task.delete().where(
                        s.model_task.c.model_task_id.in_(followup_task_ids)
                    )
                )

            connection.execute(
                s.node_context.delete().where(
                    s.node_context.c.node_context_id == context_id
                )
            )
            connection.execute(
                s.agent_attempt.delete().where(
                    s.agent_attempt.c.model_task_id == context_task_id
                )
            )
            connection.execute(
                s.model_task.delete().where(
                    s.model_task.c.model_task_id == context_task_id
                )
            )
            connection.execute(
                s.output_schema_definition.delete().where(
                    s.output_schema_definition.c.output_schema_definition_id
                    == followup_contract_id
                )
            )
            connection.execute(
                s.output_schema_definition.update()
                .where(
                    s.output_schema_definition.c.output_schema_definition_id
                    == old_followup_contract_id
                )
                .values(is_active=True)
            )


def _state(case: FollowupCase, task_id: int) -> dict[str, object]:
    with case.engine.connect() as connection:
        return dict(
            connection.execute(
                sa.select(s.model_task).where(s.model_task.c.model_task_id == task_id)
            )
            .mappings()
            .one()
        )


def _old_sets(case: FollowupCase) -> tuple[int, ...]:
    with case.engine.connect() as connection:
        return tuple(
            int(value)
            for value in connection.scalars(
                sa.select(s.node_question_set.c.question_set_id)
                .where(s.node_question_set.c.node_context_id == case.old_context_id)
                .order_by(s.node_question_set.c.question_set_id)
            ).all()
        )


def _valid_proposal(prepared: PreparedFollowup) -> FollowupQuestionsProposal:
    conflict_claim_ids = {
        claim_id
        for pair in prepared.agent_input.recent_90_days.conflict_pairs
        for claim_id in pair.claim_ids
    }
    claim_id = next(
        item.claim_id
        for item in prepared.agent_input.recent_90_days.claims
        if item.period_role == "IN_WINDOW" and item.claim_id not in conflict_claim_ids
    )
    return FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                FollowupQuestionCandidate(
                    display_order=1,
                    question_text="현재 자료에서 직접 확인되는 역할은 무엇인가요?",
                    answer_text=(
                        "현재 자료에서는 공동 사업과 연결된 역할을 확인할 수 있습니다. "
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
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )


def _expire_lease(case: FollowupCase, task_id: int) -> None:
    with case.engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE model_task SET lease_expires_at="
                "clock_timestamp()-interval '1 second' WHERE model_task_id=:id"
            ),
            {"id": task_id},
        )


def _make_retry_due(case: FollowupCase, task_id: int) -> None:
    with case.engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE model_task SET next_attempt_at="
                "clock_timestamp()-interval '1 second' WHERE model_task_id=:id"
            ),
            {"id": task_id},
        )


def test_ensure_is_idempotent_for_window_bundle(
    followup_case: FollowupCase,
) -> None:
    now = datetime.now(UTC)
    first = followup_case.ensure(now)
    same = followup_case.ensure(now)

    assert first.created is True
    assert same.created is False
    assert same.task_id == first.task_id


def test_runner_applies_valid_result_after_durable_reservation_once(
    followup_case: FollowupCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = followup_case.ensure(now)
    proposal = _valid_proposal(enqueued.prepared)
    calls = 0

    def prepare_provider(_prepared: PreparedFollowup):
        def send() -> FollowupQuestionsProposal:
            nonlocal calls
            calls += 1
            with followup_case.engine.connect() as connection:
                assert (
                    connection.scalar(
                        sa.select(s.provider_call_slot.c.state).where(
                            s.provider_call_slot.c.model_task_id == enqueued.task_id
                        )
                    )
                    == "RESERVED"
                )
            return proposal

        return send

    result = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-test-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=prepare_provider,
    )
    assert result.task_status == "SUCCESS"
    assert result.disposition == "APPLIED"
    assert result.apply_result is not None
    assert result.apply_result.stored_count == 1
    assert calls == 1

    task = _state(followup_case, enqueued.task_id)
    assert task["attempt_count"] == 1
    assert task["lease_owner"] is None
    assert task["lease_expires_at"] is None
    with followup_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(s.provider_call_slot.c.state).where(
                    s.provider_call_slot.c.model_task_id == enqueued.task_id
                )
            )
            == "COMPLETED"
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question_set)
                .where(s.node_question_set.c.model_task_id == enqueued.task_id)
            )
            == 2
        )

    again = followup_case.ensure(now)
    assert again.task_id == enqueued.task_id
    assert again.status == "SUCCESS"
    rerun = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-test-worker-2",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=prepare_provider,
    )
    assert rerun.disposition == "NOT_CLAIMED"
    assert calls == 1
    assert _old_sets(followup_case) == followup_case.old_question_set_ids


def test_runner_persists_normal_empty_for_both_windows(
    followup_case: FollowupCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = followup_case.ensure(now)

    result = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-empty-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=lambda _prepared: (
            lambda: FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            )
        ),
    )
    assert result.task_status == "SUCCESS"
    assert result.apply_result is not None
    assert result.apply_result.stored_count == 0
    with followup_case.engine.connect() as connection:
        set_ids = tuple(
            connection.scalars(
                sa.select(s.node_question_set.c.question_set_id).where(
                    s.node_question_set.c.model_task_id == enqueued.task_id
                )
            ).all()
        )
        assert len(set_ids) == 2
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question)
                .where(s.node_question.c.question_set_id.in_(set_ids))
            )
            == 0
        )


def test_ambiguous_reply_stays_reserved_then_reclaims_unknown(
    followup_case: FollowupCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = followup_case.ensure(now)

    def ambiguous(_prepared: PreparedFollowup):
        def send() -> FollowupQuestionsProposal:
            request = httpx.Request("POST", "https://provider.invalid/test")
            raise httpx.ReadTimeout("lost reply", request=request)

        return send

    first = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-ambiguous-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=ambiguous,
    )
    assert first.disposition == "AWAITING_RECLAIM"
    with followup_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(s.provider_call_slot.c.state).where(
                    s.provider_call_slot.c.model_task_id == enqueued.task_id
                )
            )
            == "RESERVED"
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.agent_attempt)
                .where(s.agent_attempt.c.model_task_id == enqueued.task_id)
            )
            == 0
        )

    _expire_lease(followup_case, enqueued.task_id)
    second = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-reclaim-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=lambda _prepared: (
            lambda: FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            )
        ),
    )
    assert second.task_status == "SUCCESS"
    with followup_case.engine.connect() as connection:
        states = tuple(
            connection.scalars(
                sa.select(s.provider_call_slot.c.state)
                .where(s.provider_call_slot.c.model_task_id == enqueued.task_id)
                .order_by(s.provider_call_slot.c.slot_no)
            ).all()
        )
        assert states == ("UNKNOWN", "COMPLETED")
        attempts = tuple(
            connection.scalars(
                sa.select(s.agent_attempt.c.attempt_no)
                .where(s.agent_attempt.c.model_task_id == enqueued.task_id)
                .order_by(s.agent_attempt.c.attempt_no)
            ).all()
        )
        assert attempts == (2,)


def test_confirmed_transient_provider_failure_retries_then_applies(
    followup_case: FollowupCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = followup_case.ensure(now)

    def rate_limited(_prepared: PreparedFollowup):
        def send() -> FollowupQuestionsProposal:
            raise ConfirmedProviderFailure("RATE_LIMITED", transient=True)

        return send

    first = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-rate-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=rate_limited,
    )
    assert first.task_status == "RETRY_WAIT"
    _make_retry_due(followup_case, enqueued.task_id)

    second = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-rate-retry-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=lambda _prepared: (
            lambda: FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            )
        ),
    )
    assert second.task_status == "SUCCESS"
    with followup_case.engine.connect() as connection:
        assert tuple(
            connection.scalars(
                sa.select(s.agent_attempt.c.outcome)
                .where(s.agent_attempt.c.model_task_id == enqueued.task_id)
                .order_by(s.agent_attempt.c.attempt_no)
            ).all()
        ) == ("RATE_LIMITED", "SUCCESS")


def test_output_contract_error_gets_one_retry_then_final_failure(
    followup_case: FollowupCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = followup_case.ensure(now)

    def malformed(_prepared: PreparedFollowup):
        def send() -> FollowupQuestionsProposal:
            raise CallFailed("OUTPUT_CONTRACT_ERROR", fatal=False)

        return send

    first = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-contract-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=malformed,
    )
    assert first.task_status == "RETRY_WAIT"
    _make_retry_due(followup_case, enqueued.task_id)

    second = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-contract-retry-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=malformed,
    )
    assert second.task_status == "FINAL_FAILED"
    with followup_case.engine.connect() as connection:
        assert tuple(
            connection.scalars(
                sa.select(s.agent_attempt.c.outcome)
                .where(s.agent_attempt.c.model_task_id == enqueued.task_id)
                .order_by(s.agent_attempt.c.attempt_no)
            ).all()
        ) == ("OUTPUT_CONTRACT_ERROR", "OUTPUT_CONTRACT_ERROR")


def test_lease_loss_discards_runtime_proposal_without_product_apply(
    followup_case: FollowupCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = followup_case.ensure(now)

    def expire_during_send(_prepared: PreparedFollowup):
        def send() -> FollowupQuestionsProposal:
            _expire_lease(followup_case, enqueued.task_id)
            return FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            )

        return send

    result = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-lease-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=expire_during_send,
    )
    assert result.disposition == "LEASE_LOST"
    with followup_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question_set)
                .where(s.node_question_set.c.model_task_id == enqueued.task_id)
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.select(s.provider_call_slot.c.state).where(
                    s.provider_call_slot.c.model_task_id == enqueued.task_id
                )
            )
            == "RESERVED"
        )


def test_context_pointer_change_after_provider_success_blocks_apply(
    followup_case: FollowupCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = followup_case.ensure(now)

    def stale_pointer(_prepared: PreparedFollowup):
        def send() -> FollowupQuestionsProposal:
            with followup_case.engine.begin() as connection:
                connection.execute(
                    s.publication_affected_node.update()
                    .where(
                        s.publication_affected_node.c.promotion_batch_id
                        == followup_case.batch_id,
                        s.publication_affected_node.c.node_id == followup_case.node_id,
                    )
                    .values(node_context_id=followup_case.old_context_id)
                )
            return FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            )

        return send

    result = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-stale-worker",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=stale_pointer,
    )
    assert result.task_status == "VALIDATION_BLOCKED"
    assert result.disposition == "VALIDATION_BLOCKED"
    assert result.apply_result is not None
    assert result.apply_result.reason == "STALE_PUBLICATION"
    with followup_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question_set)
                .where(s.node_question_set.c.model_task_id == enqueued.task_id)
            )
            == 0
        )
    assert _old_sets(followup_case) == followup_case.old_question_set_ids
