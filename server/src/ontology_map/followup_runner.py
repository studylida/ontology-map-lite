"""Durable provider execution for one approved #129 FOLLOWUP_QUESTIONS task.

This runner composes the existing product prepare/validate/apply code with the
#125/#127 lease and provider-call-slot boundary. It does not orchestrate
publication READY and never persists a raw provider payload.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import followup_generation as product_db
from ontology_map.db import followup_tasks, schema
from ontology_map.db import model_tasks as tasks
from ontology_map.durable_provider import (
    ConfirmedProviderFailure,
    UncertainProviderFailure,
    classify_provider_error,
    execute_call,
)
from ontology_map.followup_generation import has_in_window_claims, parse_proposal
from ontology_map.followup_generation_contracts import (
    FollowupApplyResult,
    FollowupQuestionsProposal,
    FollowupWindowProposal,
    PreparedFollowup,
)
from ontology_map.pilot_budget import PilotBudgetError

Disposition = Literal[
    "NOT_CLAIMED",
    "APPLIED",
    "VALIDATION_BLOCKED",
    "FAILED",
    "AWAITING_RECLAIM",
    "LEASE_LOST",
]
ProviderPreflight = Callable[
    [PreparedFollowup], Callable[[], FollowupQuestionsProposal]
]


@dataclass(frozen=True)
class RunnerResult:
    task_status: str
    disposition: Disposition
    apply_result: FollowupApplyResult | None = field(default=None, repr=False)
    error_code: str | None = None


def _status(engine: sa.Engine, task_id: int) -> str:
    with Session(engine) as session:
        return str(
            session.execute(
                sa.select(schema.model_task.c.status).where(
                    schema.model_task.c.model_task_id == task_id
                )
            ).scalar_one()
        )


def _fail(engine: sa.Engine, lease: tasks.Lease, *, transient: bool) -> RunnerResult:
    try:
        with Session(engine) as session, session.begin():
            status = tasks.fail_execution(session, lease, transient=transient)
    except tasks.LeaseLost:
        return RunnerResult(
            _status(engine, lease.task_id), "LEASE_LOST", error_code="LEASE_LOST"
        )
    except tasks.SlotBusy:
        return RunnerResult(
            _status(engine, lease.task_id),
            "AWAITING_RECLAIM",
            error_code="RESULT_UNKNOWN",
        )
    except sa.exc.DBAPIError:
        raise RuntimeError("RUNNER_STATE_WRITE_UNAVAILABLE") from None
    return RunnerResult(status, "FAILED", error_code="FOLLOWUP_EXECUTION_FAILED")


def _current_prepared(
    engine: sa.Engine,
    lease: tasks.Lease,
    *,
    node_context_id: int,
    as_of_at: datetime,
) -> PreparedFollowup:
    # Release the consistent DB snapshot before provider preflight/send.
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with Session(connection) as session, session.begin():
            tasks.require_lease(session, lease)
            return followup_tasks.require_current_input(
                session,
                lease.task_id,
                node_context_id,
                as_of_at,
            )


def _checked_operation(
    send: Callable[[], FollowupQuestionsProposal],
) -> FollowupQuestionsProposal:
    try:
        value = send()
        if not isinstance(value, FollowupQuestionsProposal):
            raise ConfirmedProviderFailure("OUTPUT_CONTRACT_ERROR")
        return parse_proposal(value.model_dump_json())
    except PilotBudgetError:
        raise
    except Exception as error:
        confirmed = classify_provider_error(error)
        if confirmed is not None:
            raise confirmed from None
        raise UncertainProviderFailure("UNCONFIRMED_PROVIDER_RESULT") from None


def _finalize(
    engine: sa.Engine,
    lease: tasks.Lease,
    prepared: PreparedFollowup,
    proposal: FollowupQuestionsProposal,
) -> RunnerResult:
    with Session(engine) as session, session.begin():
        # This row lock is the exact lease fence for the complete product apply
        # transaction. apply_followup rechecks publication/context/basis, writes
        # artifacts, records terminal product state and clears the lease here.
        tasks.require_lease(session, lease)
        applied = product_db.apply_followup(
            session,
            model_task_id=lease.task_id,
            prepared=prepared,
            proposal=proposal,
            finished_at=datetime.now(UTC),
        )
    disposition: Disposition = (
        "APPLIED" if applied.status == "SUCCESS" else "VALIDATION_BLOCKED"
    )
    return RunnerResult(applied.status, disposition, applied)


def _run_claimed(
    engine: sa.Engine,
    lease: tasks.Lease,
    *,
    node_context_id: int,
    as_of_at: datetime,
    prepare_provider: ProviderPreflight,
) -> RunnerResult:
    prepared = _current_prepared(
        engine,
        lease,
        node_context_id=node_context_id,
        as_of_at=as_of_at,
    )

    if not has_in_window_claims(prepared):
        return _finalize(
            engine,
            lease,
            prepared,
            FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
        )

    def preflight() -> Callable[[], FollowupQuestionsProposal]:
        send = prepare_provider(prepared)
        if not callable(send):
            raise ValueError("FOLLOWUP_OPERATION_MISSING")
        return lambda: _checked_operation(send)

    call = execute_call(engine, lease, preflight)
    if call.value is None:
        return RunnerResult(call.status, "FAILED", error_code="PROVIDER_FAILED")
    return _finalize(engine, lease, prepared, call.value)


def _handle_error(
    engine: sa.Engine,
    lease: tasks.Lease,
    error: Exception,
) -> RunnerResult:
    if isinstance(error, UncertainProviderFailure):
        return RunnerResult(
            _status(engine, lease.task_id),
            "AWAITING_RECLAIM",
            error_code="RESULT_UNKNOWN",
        )
    if isinstance(error, tasks.LeaseLost):
        return RunnerResult(
            _status(engine, lease.task_id), "LEASE_LOST", error_code="LEASE_LOST"
        )
    if isinstance(
        error,
        (
            followup_tasks.InputChanged,
            followup_tasks.ReferenceNotReady,
            product_db.FollowupPreparationError,
        ),
    ):
        return _fail(engine, lease, transient=False)
    if isinstance(error, sa.exc.DBAPIError):
        state = getattr(error.orig, "sqlstate", "") or ""
        transient = (
            error.connection_invalidated
            or state.startswith("08")
            or state in ("40001", "40P01")
        )
        return _fail(engine, lease, transient=transient)
    # execute_call already records deterministic preflight failure. Preserve
    # that terminal state instead of trying to write through a cleared lease.
    if _status(engine, lease.task_id) == "FINAL_FAILED":
        return RunnerResult("FINAL_FAILED", "FAILED", error_code="PREFLIGHT_FAILED")
    return _fail(engine, lease, transient=False)


def run_followup(
    engine: sa.Engine,
    task_id: int,
    worker_name: str,
    *,
    node_context_id: int,
    as_of_at: datetime,
    prepare_provider: ProviderPreflight,
) -> RunnerResult:
    """Claim, send at most once, then atomically apply both #129 windows."""
    with Session(engine) as session, session.begin():
        lease = tasks.claim_task(
            session,
            task_id,
            worker_name,
            expected_task_kind="FOLLOWUP_QUESTIONS",
        )
    if lease is None:
        return RunnerResult(_status(engine, task_id), "NOT_CLAIMED")

    try:
        return _run_claimed(
            engine,
            lease,
            node_context_id=node_context_id,
            as_of_at=as_of_at,
            prepare_provider=prepare_provider,
        )
    except PilotBudgetError:
        raise
    except Exception as error:
        return _handle_error(engine, lease, error)
