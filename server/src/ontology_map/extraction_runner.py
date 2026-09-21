"""One claimed extraction run, ending BEFORE Entity Resolution or product apply.

Only generation is a durable product call. Claim review is one document-level
runtime helper call. The generation adapter supplies a fully prepared one-send
operation; this runner deliberately does not wrap an unprepared helper call in a
reservation. No raw payload is persisted, and no product success is written.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Literal

import httpx
import sqlalchemy as sa
from openai import APITimeoutError
from pydantic import BaseModel, JsonValue
from sqlalchemy.orm import Session

from ontology_map import extraction as harness
from ontology_map.db import extraction_tasks as inputs
from ontology_map.db import model_tasks as tasks
from ontology_map.db import schema as db_schema
from ontology_map.durable_provider import (
    ConfirmedProviderFailure,
    UncertainProviderFailure,
    classify_provider_error,
    execute_call,
)
from ontology_map.extraction_contracts import (
    BodySelection,
    KnowledgeProposals,
    Ontology,
    SourceDocument,
)
from ontology_map.kimi_response_archive import response_task
from ontology_map.llm_diagnostics import carry_failure
from ontology_map.llm_pacing import provider_lease
from ontology_map.model_studio import FLASH, CallFailed, CallLimits, Role
from ontology_map.pilot_budget import PilotBudgetError, current_pilot

Disposition = Literal[
    "NOT_CLAIMED",
    "VERIFIED_RUNTIME",
    "ZERO_RESULT",
    "ALL_BLOCKED",
    "FAILED",
    "AWAITING_RECLAIM",
    "LEASE_LOST",
]


@dataclass(frozen=True)
class RuntimeInput:
    document: SourceDocument = field(repr=False)
    ontology: Ontology
    limits: harness.ExtractionLimits
    include_structure: bool = False
    body_selection: BodySelection | None = None

    def identity_settings(self) -> dict[str, JsonValue]:
        """Include under execution.runtime_settings['extraction_runner'] at enqueue.

        Binds the actual projection, ontology and limits used by this runner.
        Additional adapter settings may be included in ExecutionInput; never
        include credentials there.
        """
        return {
            "source_projection": [
                s.model_dump(mode="json", exclude={"quote"})
                for s in self.document.sources
            ],
            "ontology": self.ontology.model_dump(mode="json"),
            "limits": asdict(self.limits),
            "include_structure": self.include_structure,
            "body_selection": (
                self.body_selection.model_dump(mode="json")
                if self.body_selection is not None
                else None
            ),
        }


@dataclass(frozen=True)
class GenerationRequest:
    """In-memory adapter input. preflight must finish ALL local preparation.

    The returned operation sends once with SDK/transport retries disabled and
    returns KnowledgeProposals (or raises a typed error). Limits, model, prompt,
    schema and corrective input must be used as supplied. Never construct or
    validate credentials/request data for the first time inside that operation.
    """

    payload: harness.GenerationInput = field(repr=False)
    prompt: str = field(repr=False)
    limits: CallLimits
    execution: inputs.ExecutionInput = field(repr=False)
    model: str = FLASH
    output_schema: type[KnowledgeProposals] = KnowledgeProposals


GenerationPreflight = Callable[[GenerationRequest], Callable[[], KnowledgeProposals]]


@dataclass(frozen=True)
class RunnerResult:
    task_status: str
    disposition: Disposition
    lease: tasks.Lease | None = field(default=None, repr=False)
    extraction: harness.ExtractionResult | None = field(default=None, repr=False)
    error_code: str | None = None


class _Stopped(Exception):
    def __init__(self, status: str) -> None:
        self.status = status


class _HelperFailed(Exception):
    def __init__(self, *, transient: bool) -> None:
        self.transient = transient


def _checked_operation(send: Callable[[], KnowledgeProposals]) -> KnowledgeProposals:
    try:
        value = send()
        # Do not record SUCCESS for model_construct/model_copy bypasses or a
        # wrong return type. Terminal attempt recording follows this validation.
        if not isinstance(value, KnowledgeProposals):
            raise ConfirmedProviderFailure("OUTPUT_CONTRACT_ERROR")
        return KnowledgeProposals.model_validate_json(value.model_dump_json())
    except PilotBudgetError:
        raise
    except Exception as error:
        confirmed = classify_provider_error(error)
        if confirmed is not None:
            raise confirmed from None
        raise UncertainProviderFailure("UNCONFIRMED_PROVIDER_RESULT") from None


class _RunModels:
    def __init__(
        self,
        engine: sa.Engine,
        lease: tasks.Lease,
        execution: inputs.ExecutionInput,
        helpers: harness.ExtractionModels,
        preflight: GenerationPreflight,
    ) -> None:
        self.engine = engine
        self.lease = lease
        self.execution = execution
        self.helpers = helpers
        self.preflight = preflight

    def _check_lease(self) -> None:
        with Session(self.engine) as s, s.begin():
            tasks.require_lease(s, self.lease)

    def call[T: BaseModel](
        self,
        role: Role,
        prompt: str,
        payload: BaseModel,
        schema: type[T],
        limits: CallLimits,
    ) -> T:
        self._check_lease()
        if role != "generation":
            with response_task(self.lease.task_id), provider_lease(self._check_lease):
                return self._helper(role, prompt, payload, schema, limits)
        if (
            not isinstance(payload, harness.GenerationInput)
            or schema is not KnowledgeProposals
        ):
            raise ValueError("GENERATION_CALL_SHAPE")
        request = GenerationRequest(payload, prompt, limits, self.execution)

        def prepare() -> Callable[[], KnowledgeProposals]:
            send = self.preflight(request)
            if not callable(send):
                raise ValueError("GENERATION_OPERATION_MISSING")
            return lambda: _checked_operation(send)

        result = execute_call(self.engine, self.lease, prepare)
        if result.value is None:
            raise _Stopped(result.status)
        return schema.model_validate_json(result.value.model_dump_json())

    def _helper[T: BaseModel](
        self,
        role: Role,
        prompt: str,
        payload: BaseModel,
        schema: type[T],
        limits: CallLimits,
    ) -> T:
        try:
            return self.helpers.call(role, prompt, payload, schema, limits)
        except Exception as error:
            pilot = current_pilot(required=False)
            if isinstance(error, PilotBudgetError) or (
                pilot is not None
                and (
                    pilot.stopped
                    or (
                        isinstance(error, CallFailed)
                        and error.code in {"CALL_LIMIT", "COST_LIMIT"}
                    )
                )
            ):
                stopped = PilotBudgetError("PILOT_STOPPED")
                carry_failure(stopped, error, role=role)
                raise stopped from None
            confirmed = classify_provider_error(error)
            if confirmed is not None and confirmed.outcome == "OUTPUT_CONTRACT_ERROR":
                # Preserve candidate-level malformed-validator exclusion, without
                # inventing a durable provider attempt for the helper.
                raise CallFailed("OUTPUT_CONTRACT_ERROR", fatal=False) from None
            # Helper calls are runtime-only and have no durable provider slot.
            # A local timeout therefore remains a task-level transient failure;
            # only the durable generation send uses UNKNOWN/reclaim fencing.
            transient = (
                isinstance(error, (httpx.TimeoutException, APITimeoutError))
                or bool(getattr(error, "transient", False))
                or (
                    confirmed is not None
                    and (confirmed.outcome == "TIMEOUT" or confirmed.transient)
                )
            )
            raise _HelperFailed(transient=transient) from None


def _validate_input(
    engine: sa.Engine,
    lease: tasks.Lease,
    execution: inputs.ExecutionInput,
    runtime: RuntimeInput,
) -> RuntimeInput:
    document = SourceDocument.model_validate_json(runtime.document.model_dump_json())
    ontology = Ontology.model_validate_json(runtime.ontology.model_dump_json())
    validated = RuntimeInput(
        document,
        ontology,
        runtime.limits,
        runtime.include_structure,
        runtime.body_selection,
    )

    def canonical(value: object) -> str:
        return json.dumps(value, sort_keys=True, allow_nan=False)

    if canonical(execution.runtime_settings.get("extraction_runner")) != canonical(
        validated.identity_settings()
    ):
        raise inputs.InputChanged("RUNNER_SETTINGS_MISMATCH")
    # Fresh consistent snapshot, released BEFORE helper/provider IO.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
        with Session(c) as s, s.begin():
            tasks.require_lease(s, lease)
            identity = inputs.require_current_input(s, lease.task_id, execution)
            body = s.execute(
                sa.select(db_schema.source_document.c.normalized_body).where(
                    db_schema.source_document.c.source_document_id
                    == identity.source_document_id
                )
            ).scalar_one()
            if (
                document.document_id != str(identity.source_document_id)
                or document.body != body
            ):
                raise inputs.InputChanged("RUNNER_SOURCE_MISMATCH")
    return validated


def _status(engine: sa.Engine, task_id: int) -> str:
    with Session(engine) as s:
        return str(
            s.execute(
                sa.select(db_schema.model_task.c.status).where(
                    db_schema.model_task.c.model_task_id == task_id
                )
            ).scalar_one()
        )


def _fail(engine: sa.Engine, lease: tasks.Lease, *, transient: bool) -> RunnerResult:
    try:
        with Session(engine) as s, s.begin():
            status = tasks.fail_execution(s, lease, transient=transient)
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
    return RunnerResult(status, "FAILED", error_code="EXTRACTION_EXECUTION_FAILED")


def _run_claimed(
    engine: sa.Engine,
    lease: tasks.Lease,
    execution: inputs.ExecutionInput,
    runtime: RuntimeInput,
    helpers: harness.ExtractionModels,
    prepare_generation: GenerationPreflight,
) -> RunnerResult:
    execution = inputs.ExecutionInput.model_validate_json(execution.model_dump_json())
    runtime = _validate_input(engine, lease, execution, runtime)
    models = _RunModels(engine, lease, execution, helpers, prepare_generation)
    result = harness.extract_knowledge(
        runtime.document,
        runtime.ontology,
        models,
        runtime.limits,
        include_structure=runtime.include_structure,
        body_selection=runtime.body_selection,
        completed=None,
    )
    if result.status == "FAILED":
        # The harness can catch a local preflight ValueError after execute_call
        # has already finalized it. Do not misreport that as a lost lease.
        if _status(engine, lease.task_id) == "FINAL_FAILED":
            return RunnerResult("FINAL_FAILED", "FAILED", error_code="PREFLIGHT_FAILED")
        return _fail(engine, lease, transient=False)
    with Session(engine) as s, s.begin():
        tasks.require_lease(s, lease)
    disposition: Disposition = "VERIFIED_RUNTIME"
    if not result.verified:
        disposition = "ALL_BLOCKED" if result.generated else "ZERO_RESULT"
    # Even normal zero/all-blocked awaits the later product-finalization boundary.
    return RunnerResult("RUNNING", disposition, lease, result)


def run_extraction(
    engine: sa.Engine,
    task_id: int,
    worker_name: str,
    execution: inputs.ExecutionInput,
    runtime: RuntimeInput,
    helpers: harness.ExtractionModels,
    prepare_generation: GenerationPreflight,
) -> RunnerResult:
    """Acquire one task and run once. No polling, sleep, replay, or knowledge writes.

    Runtime results retain RUNNING and their lease. The caller must later perform
    input/lease checks and product finalization, or allow reclaim after a crash.
    VERIFIED_RUNTIME/ZERO_RESULT/ALL_BLOCKED are in-memory
    dispositions, never new DB task states or a claim that product apply succeeded.
    """
    with Session(engine) as s, s.begin():
        lease = tasks.claim_task(s, task_id, worker_name)
    if lease is None:
        return RunnerResult(_status(engine, task_id), "NOT_CLAIMED")
    try:
        return _run_claimed(
            engine, lease, execution, runtime, helpers, prepare_generation
        )
    except _Stopped as stopped:
        return RunnerResult(stopped.status, "FAILED", error_code="GENERATION_FAILED")
    except PilotBudgetError:
        raise
    except UncertainProviderFailure:
        return RunnerResult(
            _status(engine, task_id), "AWAITING_RECLAIM", error_code="RESULT_UNKNOWN"
        )
    except tasks.LeaseLost:
        return RunnerResult(
            _status(engine, task_id), "LEASE_LOST", error_code="LEASE_LOST"
        )
    except _HelperFailed as error:
        return _fail(engine, lease, transient=error.transient)
    except sa.exc.DBAPIError as error:
        state = getattr(error.orig, "sqlstate", "") or ""
        transient = (
            error.connection_invalidated
            or state.startswith("08")
            or state in ("40001", "40P01")
        )
        return _fail(engine, lease, transient=transient)
    except Exception:
        # execute_call already finalizes deterministic preflight failure. Do not
        # reset that task or write through a lease acquired by another worker.
        if _status(engine, task_id) == "FINAL_FAILED":
            return RunnerResult("FINAL_FAILED", "FAILED", error_code="PREFLIGHT_FAILED")
        return _fail(engine, lease, transient=False)
