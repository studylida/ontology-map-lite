from datetime import UTC, datetime

import sqlalchemy as sa
from test_followup_runner_postgres import (
    FollowupCase,
    _state,
    _valid_proposal,
)
from test_followup_runner_postgres import (
    followup_case as _followup_case_fixture,
)

from ontology_map import followup_execution
from ontology_map.db import schema as s
from ontology_map.followup_generation_contracts import (
    FollowupQuestionsProposal,
    PreparedFollowup,
)
from ontology_map.followup_runner import run_followup
from ontology_map.model_studio import CallLimits

followup_case = _followup_case_fixture


def test_execution_limit_change_creates_distinct_durable_task(
    followup_case: FollowupCase,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    first = followup_case.ensure(now)
    original = followup_execution.FOLLOWUP_LIMITS
    monkeypatch.setattr(
        followup_execution,
        "FOLLOWUP_LIMITS",
        CallLimits(
            max_input_tokens=original.max_input_tokens,
            max_output_tokens=original.max_output_tokens - 1,
            max_request_bytes=original.max_request_bytes,
        ),
    )
    changed = followup_case.ensure(now)

    assert first.prepared == changed.prepared
    assert first.identity.promotion_batch_id == changed.identity.promotion_batch_id
    assert first.identity.node_context_id == changed.identity.node_context_id
    assert (
        first.identity.node_search_document_id
        == changed.identity.node_search_document_id
    )
    assert first.identity.model_version == changed.identity.model_version
    assert first.identity.prompt_version == changed.identity.prompt_version
    assert (
        first.identity.output_schema_definition_id
        == changed.identity.output_schema_definition_id
    )
    assert (
        first.identity.output_schema_version == changed.identity.output_schema_version
    )
    assert first.identity.input_hash != changed.identity.input_hash
    assert first.identity.cache_key != changed.identity.cache_key
    assert changed.created is True
    assert changed.task_id != first.task_id


def test_bundle_non_empty_result_applies_once_after_reserved_send(
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
        "followup-one-year-worker",
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
    assert task["status"] == "SUCCESS"
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
                sa.select(s.agent_attempt.c.outcome).where(
                    s.agent_attempt.c.model_task_id == enqueued.task_id
                )
            )
            == "SUCCESS"
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question_set)
                .where(s.node_question_set.c.model_task_id == enqueued.task_id)
            )
            == 2
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question)
                .join(
                    s.node_question_set,
                    s.node_question_set.c.question_set_id
                    == s.node_question.c.question_set_id,
                )
                .where(s.node_question_set.c.model_task_id == enqueued.task_id)
            )
            == 1
        )

    same = followup_case.ensure(now)
    assert same.task_id == enqueued.task_id
    assert same.status == "SUCCESS"
    rerun = run_followup(
        followup_case.engine,
        enqueued.task_id,
        "followup-one-year-worker-2",
        node_context_id=followup_case.context_id,
        as_of_at=now,
        prepare_provider=prepare_provider,
    )
    assert rerun.disposition == "NOT_CLAIMED"
    assert calls == 1
    with followup_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_question_set)
                .where(s.node_question_set.c.model_task_id == enqueued.task_id)
            )
            == 2
        )
