from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from test_insight_generation_db import _available_claims, _bundle, _report

from ontology_map import insight_execution
from ontology_map import insight_generation as product
from ontology_map.db import insight_tasks
from ontology_map.db import schema as s
from ontology_map.db.panel_fixture import load_panel_fixture
from ontology_map.db.session import get_engine
from ontology_map.durable_provider import ConfirmedProviderFailure
from ontology_map.insight_generation_contracts import (
    InsightBundleProposal,
    PreparedInsightBundle,
)
from ontology_map.insight_runner import run_insight
from ontology_map.model_studio import CallFailed, CallLimits


@dataclass
class InsightCase:
    engine: sa.Engine
    batch_id: int
    node_id: int
    document_id: int
    old_pointer: int | None

    def ensure(self, as_of_at: datetime) -> insight_tasks.EnqueuedInsight:
        with Session(self.engine) as session, session.begin():
            return insight_tasks.enqueue_insight(
                session,
                self.batch_id,
                self.node_id,
                as_of_at,
            )


@pytest.fixture
def insight_case() -> InsightCase:
    _, ids = load_panel_fixture()
    engine = get_engine()

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
        old_pointer = publication["node_insight_model_task_id"]
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

        active_contract = (
            connection.execute(
                sa.select(s.output_schema_definition).where(
                    s.output_schema_definition.c.task_kind == "NODE_INSIGHT",
                    s.output_schema_definition.c.is_active,
                )
            )
            .mappings()
            .one()
        )
        old_contract_id = int(active_contract["output_schema_definition_id"])
        next_version = (
            int(
                connection.scalar(
                    sa.select(
                        sa.func.max(s.output_schema_definition.c.version_no)
                    ).where(s.output_schema_definition.c.task_kind == "NODE_INSIGHT")
                )
                or 0
            )
            + 1
        )
        connection.execute(
            s.output_schema_definition.update()
            .where(
                s.output_schema_definition.c.output_schema_definition_id
                == old_contract_id
            )
            .values(is_active=False)
        )
        insight_contract_id = int(
            connection.scalar(
                s.output_schema_definition.insert()
                .values(
                    task_kind="NODE_INSIGHT",
                    version_no=next_version,
                    schema_json=product.output_schema(),
                    is_active=True,
                )
                .returning(s.output_schema_definition.c.output_schema_definition_id)
            )
        )
        connection.execute(
            s.promotion_batch.update()
            .where(s.promotion_batch.c.promotion_batch_id == batch_id)
            .values(publication_status="PREPARING", ready_at=None)
        )

    case = InsightCase(
        engine=engine,
        batch_id=batch_id,
        node_id=node_id,
        document_id=document_id,
        old_pointer=old_pointer,
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
                .values(node_insight_model_task_id=old_pointer)
            )
            connection.execute(
                s.promotion_batch.update()
                .where(s.promotion_batch.c.promotion_batch_id == batch_id)
                .values(
                    publication_status=old_publication_status,
                    ready_at=old_ready_at,
                    publication_failure_reason=None,
                )
            )

            task_ids = list(
                connection.scalars(
                    sa.select(s.model_task.c.model_task_id).where(
                        s.model_task.c.output_schema_definition_id
                        == insight_contract_id
                    )
                ).all()
            )
            if task_ids:
                report_ids = sa.select(s.node_insight.c.node_insight_id).where(
                    s.node_insight.c.model_task_id.in_(task_ids)
                )
                section_ids = sa.select(s.node_insight_section.c.section_id).where(
                    s.node_insight_section.c.node_insight_id.in_(report_ids)
                )
                connection.execute(
                    s.node_insight_section_claim.delete().where(
                        s.node_insight_section_claim.c.section_id.in_(section_ids)
                    )
                )
                connection.execute(
                    s.node_insight_section.delete().where(
                        s.node_insight_section.c.node_insight_id.in_(report_ids)
                    )
                )
                connection.execute(
                    s.node_insight_claim.delete().where(
                        s.node_insight_claim.c.node_insight_id.in_(report_ids)
                    )
                )
                connection.execute(
                    s.node_insight_window.delete().where(
                        s.node_insight_window.c.model_task_id.in_(task_ids)
                    )
                )
                connection.execute(
                    s.node_insight.delete().where(
                        s.node_insight.c.model_task_id.in_(task_ids)
                    )
                )
                connection.execute(
                    s.provider_call_slot.delete().where(
                        s.provider_call_slot.c.model_task_id.in_(task_ids)
                    )
                )
                connection.execute(
                    s.agent_attempt.delete().where(
                        s.agent_attempt.c.model_task_id.in_(task_ids)
                    )
                )
                connection.execute(
                    s.model_task.delete().where(
                        s.model_task.c.model_task_id.in_(task_ids)
                    )
                )

            connection.execute(
                s.output_schema_definition.delete().where(
                    s.output_schema_definition.c.output_schema_definition_id
                    == insight_contract_id
                )
            )
            connection.execute(
                s.output_schema_definition.update()
                .where(
                    s.output_schema_definition.c.output_schema_definition_id
                    == old_contract_id
                )
                .values(is_active=True)
            )


def _state(case: InsightCase, task_id: int) -> dict[str, object]:
    with case.engine.connect() as connection:
        return dict(
            connection.execute(
                sa.select(s.model_task).where(s.model_task.c.model_task_id == task_id)
            )
            .mappings()
            .one()
        )


def _expire_lease(case: InsightCase, task_id: int) -> None:
    with case.engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE model_task SET lease_expires_at="
                "clock_timestamp()-interval '1 second' WHERE model_task_id=:id"
            ),
            {"id": task_id},
        )


def _make_retry_due(case: InsightCase, task_id: int) -> None:
    with case.engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE model_task SET next_attempt_at="
                "clock_timestamp()-interval '1 second' WHERE model_task_id=:id"
            ),
            {"id": task_id},
        )


def _proposal(
    prepared: PreparedInsightBundle,
    *,
    ninety_report: bool,
    year_report: bool,
) -> InsightBundleProposal:
    ninety = (
        _report(
            _available_claims(prepared),
            question="최근 90일 흐름은 무엇인가요?",
        )
        if ninety_report
        else None
    )
    year = (
        _report(
            _available_claims(prepared, year=True),
            question="지난 1년 흐름은 무엇인가요?",
        )
        if year_report
        else None
    )
    return _bundle(ninety, year)


def _invalid_proposal(
    prepared: PreparedInsightBundle,
    invalid_window: str,
) -> InsightBundleProposal:
    ninety_ids = _available_claims(prepared)
    year_ids = _available_claims(prepared, year=True)
    ninety = _report(
        ninety_ids,
        question="최근 90일 흐름은 무엇인가요?",
        invalid_single_claim=invalid_window == "90d",
    )
    year = _report(
        year_ids,
        question="지난 1년 흐름은 무엇인가요?",
        invalid_single_claim=invalid_window == "1y",
    )
    return _bundle(ninety, year)


def _run(
    case: InsightCase,
    task_id: int,
    now: datetime,
    prepare_provider,
    worker: str = "insight-test-worker",
):
    return run_insight(
        case.engine,
        task_id,
        worker,
        promotion_batch_id=case.batch_id,
        node_id=case.node_id,
        as_of_at=now,
        prepare_provider=prepare_provider,
    )


def test_one_task_identity_is_idempotent_and_execution_settings_are_hashed(
    insight_case: InsightCase,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    first = insight_case.ensure(now)
    same = insight_case.ensure(now)

    assert first.created is True
    assert same.created is False
    assert same.task_id == first.task_id
    assert first.prepared.agent_input.recent_90_days.time_window == "RECENT_90_DAYS"
    assert first.prepared.agent_input.recent_1_year.time_window == "RECENT_1_YEAR"

    original = insight_execution.INSIGHT_LIMITS
    monkeypatch.setattr(
        insight_execution,
        "INSIGHT_LIMITS",
        CallLimits(
            max_input_tokens=original.max_input_tokens,
            max_output_tokens=original.max_output_tokens - 1,
            max_request_bytes=original.max_request_bytes,
        ),
    )
    changed = insight_case.ensure(now)
    assert first.prepared == changed.prepared
    assert first.identity.model_version == changed.identity.model_version
    assert first.identity.prompt_version == changed.identity.prompt_version
    assert (
        first.identity.output_schema_definition_id
        == changed.identity.output_schema_definition_id
    )
    assert first.identity.input_hash != changed.identity.input_hash
    assert first.identity.cache_key != changed.identity.cache_key
    assert changed.created is True
    assert changed.task_id != first.task_id


def test_structural_empty_finishes_without_provider_slot(
    insight_case: InsightCase,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    enqueued = insight_case.ensure(now)
    monkeypatch.setattr(
        "ontology_map.insight_runner.empty_bundle_if_structurally_impossible",
        lambda _prepared: _bundle(None, None),
    )

    result = _run(
        insight_case,
        enqueued.task_id,
        now,
        lambda _prepared: pytest.fail("provider preflight must be skipped"),
    )

    assert result.task_status == "SUCCESS"
    with insight_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.provider_call_slot)
                .where(s.provider_call_slot.c.model_task_id == enqueued.task_id)
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.agent_attempt)
                .where(s.agent_attempt.c.model_task_id == enqueued.task_id)
            )
            == 0
        )


@pytest.mark.parametrize(
    ("ninety_report", "year_report"),
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_runner_atomically_applies_all_report_empty_combinations(
    insight_case: InsightCase,
    ninety_report: bool,
    year_report: bool,
) -> None:
    now = datetime.now(UTC)
    enqueued = insight_case.ensure(now)
    proposal = _proposal(
        enqueued.prepared,
        ninety_report=ninety_report,
        year_report=year_report,
    )
    calls = 0

    def prepare_provider(_prepared: PreparedInsightBundle):
        def send() -> InsightBundleProposal:
            nonlocal calls
            calls += 1
            with insight_case.engine.connect() as connection:
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
            return proposal

        return send

    result = _run(insight_case, enqueued.task_id, now, prepare_provider)
    assert result.task_status == "SUCCESS"
    assert result.disposition == "APPLIED"
    assert result.apply_result is not None
    assert calls == 1

    expected_reports = int(ninety_report) + int(year_report)
    with insight_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(s.provider_call_slot.c.state).where(
                    s.provider_call_slot.c.model_task_id == enqueued.task_id
                )
            )
            == "COMPLETED"
        )
        assert tuple(
            connection.scalars(
                sa.select(s.agent_attempt.c.outcome).where(
                    s.agent_attempt.c.model_task_id == enqueued.task_id
                )
            ).all()
        ) == ("SUCCESS",)
        windows = (
            connection.execute(
                sa.select(s.node_insight_window).where(
                    s.node_insight_window.c.model_task_id == enqueued.task_id
                )
            )
            .mappings()
            .all()
        )
        assert len(windows) == 2
        assert (
            sum(row["node_insight_id"] is not None for row in windows)
            == expected_reports
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight)
                .where(s.node_insight.c.model_task_id == enqueued.task_id)
            )
            == expected_reports
        )
        assert (
            connection.scalar(
                sa.select(
                    s.publication_affected_node.c.node_insight_model_task_id
                ).where(
                    s.publication_affected_node.c.promotion_batch_id
                    == insight_case.batch_id,
                    s.publication_affected_node.c.node_id == insight_case.node_id,
                )
            )
            == enqueued.task_id
        )

    task = _state(insight_case, enqueued.task_id)
    assert task["lease_owner"] is None
    assert task["lease_expires_at"] is None
    again = insight_case.ensure(now)
    assert again.task_id == enqueued.task_id
    assert again.status == "SUCCESS"
    rerun = _run(
        insight_case,
        enqueued.task_id,
        now,
        prepare_provider,
        worker="insight-second-worker",
    )
    assert rerun.disposition == "NOT_CLAIMED"
    assert calls == 1


@pytest.mark.parametrize("invalid_window", ["90d", "1y"])
def test_invalid_generated_report_blocks_whole_bundle_without_partial_artifact(
    insight_case: InsightCase,
    invalid_window: str,
) -> None:
    now = datetime.now(UTC)
    enqueued = insight_case.ensure(now)
    proposal = _invalid_proposal(enqueued.prepared, invalid_window)

    result = _run(
        insight_case,
        enqueued.task_id,
        now,
        lambda _prepared: lambda: proposal,
    )
    assert result.task_status == "VALIDATION_BLOCKED"
    assert result.disposition == "VALIDATION_BLOCKED"
    assert result.apply_result is not None
    assert result.apply_result.report_ids == (None, None)
    with insight_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == enqueued.task_id)
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight)
                .where(s.node_insight.c.model_task_id == enqueued.task_id)
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.select(
                    s.publication_affected_node.c.node_insight_model_task_id
                ).where(
                    s.publication_affected_node.c.promotion_batch_id
                    == insight_case.batch_id,
                    s.publication_affected_node.c.node_id == insight_case.node_id,
                )
            )
            == insight_case.old_pointer
        )


def test_confirmed_transient_provider_failure_retries_then_applies(
    insight_case: InsightCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = insight_case.ensure(now)

    def rate_limited(_prepared: PreparedInsightBundle):
        def send() -> InsightBundleProposal:
            raise ConfirmedProviderFailure("RATE_LIMITED", transient=True)

        return send

    first = _run(insight_case, enqueued.task_id, now, rate_limited)
    assert first.task_status == "RETRY_WAIT"
    _make_retry_due(insight_case, enqueued.task_id)
    second = _run(
        insight_case,
        enqueued.task_id,
        now,
        lambda _prepared: lambda: _bundle(None, None),
        worker="insight-rate-retry-worker",
    )
    assert second.task_status == "SUCCESS"
    with insight_case.engine.connect() as connection:
        assert tuple(
            connection.scalars(
                sa.select(s.agent_attempt.c.outcome)
                .where(s.agent_attempt.c.model_task_id == enqueued.task_id)
                .order_by(s.agent_attempt.c.attempt_no)
            ).all()
        ) == ("RATE_LIMITED", "SUCCESS")


def test_output_contract_error_retries_then_final_failure(
    insight_case: InsightCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = insight_case.ensure(now)

    def malformed(_prepared: PreparedInsightBundle):
        def send() -> InsightBundleProposal:
            raise CallFailed("OUTPUT_CONTRACT_ERROR", fatal=False)

        return send

    first = _run(insight_case, enqueued.task_id, now, malformed)
    assert first.task_status == "RETRY_WAIT"
    _make_retry_due(insight_case, enqueued.task_id)
    second = _run(
        insight_case,
        enqueued.task_id,
        now,
        malformed,
        worker="insight-contract-retry-worker",
    )
    assert second.task_status == "FINAL_FAILED"
    with insight_case.engine.connect() as connection:
        assert tuple(
            connection.scalars(
                sa.select(s.agent_attempt.c.outcome)
                .where(s.agent_attempt.c.model_task_id == enqueued.task_id)
                .order_by(s.agent_attempt.c.attempt_no)
            ).all()
        ) == ("OUTPUT_CONTRACT_ERROR", "OUTPUT_CONTRACT_ERROR")


def test_ambiguous_reply_reclaims_unknown_then_applies(
    insight_case: InsightCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = insight_case.ensure(now)

    def ambiguous(_prepared: PreparedInsightBundle):
        def send() -> InsightBundleProposal:
            request = httpx.Request("POST", "https://provider.invalid/test")
            raise httpx.ReadTimeout("lost reply", request=request)

        return send

    first = _run(insight_case, enqueued.task_id, now, ambiguous)
    assert first.disposition == "AWAITING_RECLAIM"
    with insight_case.engine.connect() as connection:
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

    _expire_lease(insight_case, enqueued.task_id)
    second = _run(
        insight_case,
        enqueued.task_id,
        now,
        lambda _prepared: lambda: _bundle(None, None),
        worker="insight-reclaim-worker",
    )
    assert second.task_status == "SUCCESS"
    with insight_case.engine.connect() as connection:
        assert tuple(
            connection.scalars(
                sa.select(s.provider_call_slot.c.state)
                .where(s.provider_call_slot.c.model_task_id == enqueued.task_id)
                .order_by(s.provider_call_slot.c.slot_no)
            ).all()
        ) == ("UNKNOWN", "COMPLETED")
        assert tuple(
            connection.scalars(
                sa.select(s.agent_attempt.c.attempt_no)
                .where(s.agent_attempt.c.model_task_id == enqueued.task_id)
                .order_by(s.agent_attempt.c.attempt_no)
            ).all()
        ) == (2,)


def test_lease_loss_discards_bundle_without_partial_product_apply(
    insight_case: InsightCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = insight_case.ensure(now)

    def expire_during_send(_prepared: PreparedInsightBundle):
        def send() -> InsightBundleProposal:
            _expire_lease(insight_case, enqueued.task_id)
            return _bundle(None, None)

        return send

    result = _run(insight_case, enqueued.task_id, now, expire_during_send)
    assert result.disposition == "LEASE_LOST"
    with insight_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == enqueued.task_id)
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


def test_stale_publication_after_provider_success_blocks_bundle(
    insight_case: InsightCase,
) -> None:
    now = datetime.now(UTC)
    enqueued = insight_case.ensure(now)

    def stale_publication(_prepared: PreparedInsightBundle):
        def send() -> InsightBundleProposal:
            with insight_case.engine.begin() as connection:
                connection.execute(
                    s.promotion_batch.update()
                    .where(
                        s.promotion_batch.c.promotion_batch_id == insight_case.batch_id
                    )
                    .values(
                        publication_status="FAILED",
                        publication_failure_reason="stale during provider call",
                    )
                )
            return _bundle(None, None)

        return send

    result = _run(insight_case, enqueued.task_id, now, stale_publication)
    assert result.task_status == "VALIDATION_BLOCKED"
    assert result.disposition == "VALIDATION_BLOCKED"
    assert result.apply_result is not None
    assert result.apply_result.reason == "STALE_PUBLICATION"
    with insight_case.engine.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == enqueued.task_id)
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.select(
                    s.publication_affected_node.c.node_insight_model_task_id
                ).where(
                    s.publication_affected_node.c.promotion_batch_id
                    == insight_case.batch_id,
                    s.publication_affected_node.c.node_id == insight_case.node_id,
                )
            )
            == insight_case.old_pointer
        )
