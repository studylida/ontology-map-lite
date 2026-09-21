"""Durable provider execution for one #215 NODE_CONTEXT task.

The existing #215 product boundary owns generation identity, deterministic input,
validation, immutable artifact storage, and terminal product state. This runner
only composes that boundary with the shared #125/#127 lease/provider-call-slot
lifecycle.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import node_context_generation as product
from ontology_map.db import model_tasks as tasks
from ontology_map.db import schema
from ontology_map.db.initial_publication_context import (
    apply_node_context,
    prepare_node_context,
)
from ontology_map.db.initial_publication_contracts import InitialPublicationError
from ontology_map.durable_provider import (
    ConfirmedProviderFailure,
    UncertainProviderFailure,
    classify_provider_error,
    execute_call,
)
from ontology_map.node_context_generation_contracts import (
    NodeContextApplyResult,
    NodeContextProposal,
    PreparedNodeContext,
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
ProviderPreflight = Callable[[PreparedNodeContext], Callable[[], NodeContextProposal]]


@dataclass(frozen=True)
class RunnerResult:
    task_status: str
    disposition: Disposition
    apply_result: NodeContextApplyResult | None = field(default=None, repr=False)
    error_code: str | None = None


class InputChanged(ValueError):
    """The selected generation/search basis no longer matches the durable task."""


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
    return RunnerResult(status, "FAILED", error_code="NODE_CONTEXT_EXECUTION_FAILED")


def _current_prepared(
    engine: sa.Engine,
    lease: tasks.Lease,
    *,
    promotion_batch_id: int,
    node_id: int,
) -> PreparedNodeContext:
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with Session(connection) as session, session.begin():
            task = tasks.require_lease(session, lease)
            prepared = prepare_node_context(
                session,
                promotion_batch_id=promotion_batch_id,
                node_id=node_id,
            )
            contract = (
                session.execute(
                    sa.select(
                        schema.output_schema_definition.c.schema_json,
                        schema.output_schema_definition.c.is_active,
                    ).where(
                        schema.output_schema_definition.c.output_schema_definition_id
                        == task["output_schema_definition_id"]
                    )
                )
                .mappings()
                .one()
            )
            if (
                task["task_kind"] != "NODE_CONTEXT"
                or task["source_document_id"] is not None
                or bytes(task["input_hash"]) != prepared.input_hash
                or task["model_version"] != product.MODEL_VERSION
                or task["prompt_version"] != product.PROMPT_VERSION
                or not bool(contract["is_active"])
                or contract["schema_json"] != product.output_schema()
            ):
                raise InputChanged("NODE_CONTEXT_INPUT_CHANGED")
            return prepared


def _checked_operation(
    send: Callable[[], NodeContextProposal],
) -> NodeContextProposal:
    try:
        value = send()
        if not isinstance(value, NodeContextProposal):
            raise ConfirmedProviderFailure("OUTPUT_CONTRACT_ERROR")
        return product.parse_proposal(value.model_dump_json())
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
    prepared: PreparedNodeContext,
    proposal: NodeContextProposal,
) -> RunnerResult:
    with Session(engine) as session, session.begin():
        tasks.require_lease(session, lease)
        applied = apply_node_context(
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
    promotion_batch_id: int,
    node_id: int,
    prepare_provider: ProviderPreflight,
) -> RunnerResult:
    prepared = _current_prepared(
        engine,
        lease,
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
    )

    def preflight() -> Callable[[], NodeContextProposal]:
        send = prepare_provider(prepared)
        if not callable(send):
            raise ValueError("NODE_CONTEXT_OPERATION_MISSING")
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
    if isinstance(error, (InputChanged, InitialPublicationError)):
        return _fail(engine, lease, transient=False)
    if isinstance(error, sa.exc.DBAPIError):
        state = getattr(error.orig, "sqlstate", "") or ""
        transient = (
            error.connection_invalidated
            or state.startswith("08")
            or state in ("40001", "40P01")
        )
        return _fail(engine, lease, transient=transient)
    if _status(engine, lease.task_id) == "FINAL_FAILED":
        return RunnerResult("FINAL_FAILED", "FAILED", error_code="PREFLIGHT_FAILED")
    return _fail(engine, lease, transient=False)


def run_node_context(
    engine: sa.Engine,
    task_id: int,
    worker_name: str,
    *,
    promotion_batch_id: int,
    node_id: int,
    prepare_provider: ProviderPreflight,
) -> RunnerResult:
    """Claim, send once per slot, then atomically apply one NODE_CONTEXT result."""
    with Session(engine) as session, session.begin():
        lease = tasks.claim_task(
            session,
            task_id,
            worker_name,
            expected_task_kind="NODE_CONTEXT",
        )
    if lease is None:
        return RunnerResult(_status(engine, task_id), "NOT_CLAIMED")

    try:
        return _run_claimed(
            engine,
            lease,
            promotion_batch_id=promotion_batch_id,
            node_id=node_id,
            prepare_provider=prepare_provider,
        )
    except PilotBudgetError:
        raise
    except Exception as error:
        return _handle_error(engine, lease, error)
