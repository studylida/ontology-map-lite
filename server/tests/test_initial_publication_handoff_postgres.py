"""Post-commit #127 -> #215 production handoff regressions."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from test_extraction_promotion_postgres import (
    _new,
    _runner,
    _seed,
    _truncate,
)
from test_initial_publication_phase_b_postgres import _activate_context_contract

from ontology_map import initial_publication_handoff as handoff
from ontology_map.db import extraction_tasks as extraction_inputs
from ontology_map.db import model_tasks as tasks
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema as s
from ontology_map.durable_provider import ConfirmedProviderFailure
from ontology_map.entity_resolution_contracts import ResolutionBatchInput
from ontology_map.extraction import ExtractionResult
from ontology_map.extraction_promotion import finalize_extraction
from ontology_map.extraction_runner import RunnerResult as ExtractionRunnerResult
from ontology_map.followup_runner import ProviderPreflight as FollowupProviderPreflight
from ontology_map.initial_publication_coordinator import (
    InitialPublicationRunResult,
)
from ontology_map.initial_publication_coordinator import (
    run_initial_publication as real_run_initial_publication,
)
from ontology_map.insight_runner import ProviderPreflight as InsightProviderPreflight
from ontology_map.node_context_runner import (
    ProviderPreflight as NodeContextProviderPreflight,
)

DATABASE_URL = os.environ.get("ONTOLOGY_MAP_INITIAL_PUBLICATION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="isolated migrated PostgreSQL URL was not supplied",
)


def _engine() -> sa.Engine:
    assert DATABASE_URL is not None
    url = sa.engine.make_url(DATABASE_URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_publication215_test")
    return sa.create_engine(url)


@pytest.fixture(autouse=True)
def _isolate_database() -> None:
    if not DATABASE_URL:
        yield
        return
    engine = _engine()
    try:
        _truncate(engine)
        yield
    finally:
        try:
            _truncate(engine)
        finally:
            engine.dispose()


def _context_failure(_prepared: object):
    def send():
        raise ConfirmedProviderFailure("INVALID_REQUEST")

    return send


def _unexpected_provider(_prepared: object):
    raise AssertionError("derived provider must not run without NODE_CONTEXT")


def _batch_state(engine: sa.Engine, batch_id: int) -> tuple[str, str]:
    with Session(engine) as session:
        row = session.execute(
            sa.select(
                s.promotion_batch.c.promotion_status,
                s.promotion_batch.c.publication_status,
            ).where(s.promotion_batch.c.promotion_batch_id == batch_id)
        ).one()
    return str(row.promotion_status), str(row.publication_status)


def _membership(engine: sa.Engine, batch_id: int) -> tuple[int, ...]:
    with Session(engine) as session:
        return tuple(
            int(value)
            for value in session.scalars(
                sa.select(s.publication_affected_node.c.node_id)
                .where(s.publication_affected_node.c.promotion_batch_id == batch_id)
                .order_by(s.publication_affected_node.c.node_id)
            )
        )


def _promotion_counts(engine: sa.Engine, batch_id: int) -> tuple[int, int]:
    with Session(engine) as session:
        knowledge = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.knowledge_item)
                .where(s.knowledge_item.c.promotion_batch_id == batch_id)
            )
            or 0
        )
        changes = len(provenance.changes_for_batch(session, batch_id))
    return knowledge, changes


def _insert_previous_ready(engine: sa.Engine) -> int:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        policy_id = session.scalar(
            sa.select(s.lint_policy_version.c.lint_policy_version_id)
            .where(s.lint_policy_version.c.is_active)
            .limit(1)
        )
        assert policy_id is not None
        batch_id = session.scalar(
            s.promotion_batch.insert()
            .values(
                lint_policy_version_id=int(policy_id),
                promotion_status="COMMITTED",
                publication_status="READY",
                started_at=now - timedelta(seconds=2),
                committed_at=now - timedelta(seconds=1),
                ready_at=now,
            )
            .returning(s.promotion_batch.c.promotion_batch_id)
        )
        assert batch_id is not None
        return int(batch_id)


def test_actual_finalizer_commits_before_publication_handoff_and_failure_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    case = _seed(engine)
    with Session(engine) as session, session.begin():
        _activate_context_contract(session)
    previous_ready_id = _insert_previous_ready(engine)
    previous_ready_state = _batch_state(engine, previous_ready_id)
    observed_before_start: dict[str, object] = {}

    def observe_then_start(
        called_engine: sa.Engine,
        batch_id: int,
        worker_name: str,
        *,
        prepare_node_context_provider: NodeContextProviderPreflight,
        prepare_followup_provider: FollowupProviderPreflight,
        prepare_insight_provider: InsightProviderPreflight,
    ) -> InitialPublicationRunResult:
        assert called_engine is engine
        observed_before_start["state"] = _batch_state(engine, batch_id)
        observed_before_start["membership"] = _membership(engine, batch_id)
        observed_before_start["counts"] = _promotion_counts(engine, batch_id)
        return real_run_initial_publication(
            called_engine,
            batch_id,
            worker_name,
            prepare_node_context_provider=prepare_node_context_provider,
            prepare_followup_provider=prepare_followup_provider,
            prepare_insight_provider=prepare_insight_provider,
        )

    monkeypatch.setattr(handoff, "run_initial_publication", observe_then_start)
    result = handoff.finalize_extraction_with_initial_publication(
        engine,
        _runner(case),
        case.execution,
        case.runtime,
        _new,
        "handoff-post-commit",
        prepare_node_context_provider=_context_failure,
        prepare_followup_provider=_unexpected_provider,
        prepare_insight_provider=_unexpected_provider,
    )
    batch_id = result.product.promotion_batch_id
    assert result.product.disposition == result.product.task_status == "SUCCESS"
    assert batch_id is not None
    assert observed_before_start["state"] == ("COMMITTED", "NOT_STARTED")
    assert observed_before_start["membership"] == ()
    knowledge_before, changes_before = observed_before_start["counts"]
    assert knowledge_before > 0
    assert changes_before > 0

    assert result.publication is not None
    assert not result.publication.ready
    assert _batch_state(engine, batch_id) == ("COMMITTED", "PREPARING")
    assert _membership(engine, batch_id)
    assert _promotion_counts(engine, batch_id) == (knowledge_before, changes_before)
    assert (
        _batch_state(engine, previous_ready_id)
        == previous_ready_state
        == (
            "COMMITTED",
            "READY",
        )
    )
    engine.dispose()


def _reprocess_runner(case, execution: extraction_inputs.ExecutionInput):
    engine = case.engine
    with Session(engine) as session, session.begin():
        task_id = extraction_inputs.enqueue_extraction(
            session,
            int(case.runtime.document.document_id),
            execution,
        ).task_id
    with Session(engine) as session, session.begin():
        lease = tasks.claim_task(session, task_id, "handoff-noop-reprocess")
        assert lease is not None
    with Session(engine) as session, session.begin():
        slot = tasks.reserve_slot(session, lease)
        assert slot is not None
    with Session(engine) as session, session.begin():
        tasks.record_terminal(
            session,
            slot,
            tasks.TerminalResult("SUCCESS", datetime.now(UTC)),
        )

    def same(payload: ResolutionBatchInput) -> object:
        return {
            "resolutions": [
                {
                    "mention_id": item.mention_id,
                    "decision": "SAME",
                    "node_id": item.input.candidates[0].node_id,
                }
                for item in payload.mentions
            ]
        }

    runner = ExtractionRunnerResult(
        "RUNNING",
        "VERIFIED_RUNTIME",
        lease,
        ExtractionResult(generated=list(case.claims), verified=list(case.claims)),
    )
    return runner, same


def test_actual_noop_reprocess_does_not_call_publication_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    case = _seed(engine)
    first = finalize_extraction(
        engine,
        _runner(case),
        case.execution,
        case.runtime,
        _new,
    )
    assert first.disposition == "SUCCESS"
    assert first.promotion_batch_id is not None
    before_batches = _batch_state(engine, first.promotion_batch_id)
    with Session(engine) as session:
        publication_count_before = int(
            session.scalar(
                sa.select(sa.func.count()).select_from(s.publication_affected_node)
            )
            or 0
        )
        batch_count_before = int(
            session.scalar(sa.select(sa.func.count()).select_from(s.promotion_batch))
            or 0
        )

    execution = case.execution.model_copy(
        update={"execution_generation": "handoff-noop-reprocess-2"}
    )
    rerun, same = _reprocess_runner(case, execution)
    calls = 0

    def must_not_start(*_args: object, **_kwargs: object):
        nonlocal calls
        calls += 1
        raise AssertionError("no-op promotion must not start publication")

    monkeypatch.setattr(handoff, "run_initial_publication", must_not_start)
    second = handoff.finalize_extraction_with_initial_publication(
        engine,
        rerun,
        execution,
        case.runtime,
        same,
        "handoff-noop",
        prepare_node_context_provider=_context_failure,
        prepare_followup_provider=_unexpected_provider,
        prepare_insight_provider=_unexpected_provider,
    )
    assert second.product.disposition == second.product.task_status == "SUCCESS"
    assert second.product.promotion_batch_id is None
    assert second.publication is None
    assert calls == 0
    assert _batch_state(engine, first.promotion_batch_id) == before_batches
    with Session(engine) as session:
        assert (
            int(
                session.scalar(
                    sa.select(sa.func.count()).select_from(s.publication_affected_node)
                )
                or 0
            )
            == publication_count_before
        )
        assert (
            int(
                session.scalar(
                    sa.select(sa.func.count()).select_from(s.promotion_batch)
                )
                or 0
            )
            == batch_count_before
        )
    engine.dispose()


def test_committed_not_started_reenters_same_coordinator() -> None:
    engine = _engine()
    case = _seed(engine)
    with Session(engine) as session, session.begin():
        _activate_context_contract(session)
    product = finalize_extraction(
        engine,
        _runner(case),
        case.execution,
        case.runtime,
        _new,
    )
    batch_id = product.promotion_batch_id
    assert product.disposition == "SUCCESS"
    assert batch_id is not None
    assert _batch_state(engine, batch_id) == ("COMMITTED", "NOT_STARTED")
    assert _membership(engine, batch_id) == ()
    committed_counts = _promotion_counts(engine, batch_id)

    first = real_run_initial_publication(
        engine,
        batch_id,
        "handoff-restart",
        prepare_node_context_provider=_context_failure,
        prepare_followup_provider=_unexpected_provider,
        prepare_insight_provider=_unexpected_provider,
    )
    first_membership = _membership(engine, batch_id)
    assert first.publication_status == "PREPARING"
    assert first_membership
    with Session(engine) as session:
        task_count = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.model_task)
                .where(s.model_task.c.task_kind == "NODE_CONTEXT")
            )
            or 0
        )

    second = real_run_initial_publication(
        engine,
        batch_id,
        "handoff-restart",
        prepare_node_context_provider=_context_failure,
        prepare_followup_provider=_unexpected_provider,
        prepare_insight_provider=_unexpected_provider,
    )
    assert second.publication_status == "PREPARING"
    assert _membership(engine, batch_id) == first_membership
    assert _promotion_counts(engine, batch_id) == committed_counts
    with Session(engine) as session:
        assert (
            int(
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(s.model_task)
                    .where(s.model_task.c.task_kind == "NODE_CONTEXT")
                )
                or 0
            )
            == task_count
        )
    engine.dispose()
