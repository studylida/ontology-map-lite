"""One durable product call: preflight, reservation, IO, terminal record.

Internal validators and Entity Resolution do not use this boundary. The caller
owns model_task creation, product validation and the final promotion transaction.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from openai import APIStatusError, APITimeoutError
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from ontology_map.db import model_tasks as tasks
from ontology_map.kimi_response_archive import response_task
from ontology_map.llm_pacing import provider_lease, provider_turn
from ontology_map.llm_response_metadata import rate_limit_kind
from ontology_map.model_studio import CallFailed
from ontology_map.pilot_budget import PilotBudget, PilotBudgetError, current_pilot


class ConfirmedProviderFailure(Exception):
    """A classified provider outcome; never pass provider exception text."""

    def __init__(
        self,
        outcome: tasks.Outcome,
        *,
        transient: bool = False,
        retry_after: timedelta | None = None,
    ) -> None:
        if outcome == "SUCCESS" or outcome not in tasks.OUTCOMES:
            raise ValueError("INVALID_PROVIDER_FAILURE")
        super().__init__(outcome)
        self.outcome = outcome
        self.transient = transient
        self.retry_after = retry_after


class UncertainProviderFailure(RuntimeError):
    """Leave RESERVED intact; reclaim will close it UNKNOWN, with no fake attempt."""


def _retry_after(value: str | None) -> timedelta | None:
    if value is None:
        return None
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return timedelta(seconds=seconds)
        return None
    except ValueError, OverflowError:
        try:
            at = parsedate_to_datetime(value)
            if at.utcoffset() is None:
                return None
            return max(at - datetime.now(UTC), timedelta(0))
        except ValueError, TypeError, OverflowError:
            return None


def _http_failure(
    status: int, retry_after: str | None
) -> ConfirmedProviderFailure | None:
    if status in (401, 403):
        return ConfirmedProviderFailure("AUTHENTICATION_ERROR")
    if status == 429:
        return ConfirmedProviderFailure(
            "RATE_LIMITED",
            transient=True,
            retry_after=_retry_after(retry_after),
        )
    if status in (408, 504):
        return ConfirmedProviderFailure("TIMEOUT", transient=True)
    if 500 <= status < 600:
        return ConfirmedProviderFailure("PROVIDER_ERROR", transient=True)
    if 400 <= status < 500:
        return ConfirmedProviderFailure("INVALID_REQUEST")
    return None


def classify_provider_error(error: Exception) -> ConfirmedProviderFailure | None:
    """Classify only typed/allowlisted provider signals without storing bodies.

    None means an unconfirmed transmission/result, not an invented provider error.
    This common durable transport rule is independent of task-specific DTOs.
    """
    if isinstance(error, ConfirmedProviderFailure):
        return error
    if isinstance(error, (httpx.TimeoutException, APITimeoutError)):
        # A local timeout can follow a partial write or a lost reply. Only an
        # explicit provider 408/504 response is a confirmed timeout outcome.
        return None
    # Broad network/SDK connection errors may follow a partial send or lost reply.
    # Only ConnectError identifies failure to establish the HTTP connection.
    if isinstance(error, httpx.ConnectError):
        return ConfirmedProviderFailure("PROVIDER_ERROR", transient=True)
    if isinstance(error, (httpx.HTTPStatusError, APIStatusError)):
        if rate_limit_kind(error.response) == "QUOTA_OR_BILLING":
            return ConfirmedProviderFailure("RATE_LIMITED", transient=False)
        return _http_failure(
            error.response.status_code, error.response.headers.get("Retry-After")
        )
    if isinstance(error, ValidationError):
        return ConfirmedProviderFailure("OUTPUT_CONTRACT_ERROR")
    if isinstance(error, CallFailed) and error.code == "OUTPUT_CONTRACT_ERROR":
        return ConfirmedProviderFailure("OUTPUT_CONTRACT_ERROR")
    return None


@dataclass(frozen=True)
class CallResult[T]:
    status: str
    value: T | None = None


def _execute_prepared_call[T](
    engine: Engine,
    lease: tasks.Lease,
    send: Callable[[], T],
) -> CallResult[T]:
    with Session(engine) as session, session.begin():
        slot = tasks.reserve_slot(session, lease)
    if slot is None:
        return CallResult("FINAL_FAILED")
    attempted_at = datetime.now(UTC)
    try:
        with response_task(
            getattr(lease, "task_id", None), getattr(slot, "slot_no", None)
        ):
            value = send()
    except PilotBudgetError:
        raise
    except ConfirmedProviderFailure as error:
        result = tasks.TerminalResult(
            error.outcome,
            attempted_at,
            error.outcome,
            error.transient,
            error.retry_after,
        )
        with Session(engine) as session, session.begin():
            status = tasks.record_terminal(session, slot, result)
        return CallResult(status)
    except Exception:
        # Unknown exceptions are not automatically invented PROVIDER_ERRORs.
        raise UncertainProviderFailure("UNCONFIRMED_PROVIDER_RESULT") from None
    with Session(engine) as session, session.begin():
        tasks.record_terminal(
            session, slot, tasks.TerminalResult("SUCCESS", attempted_at)
        )
    return CallResult("RUNNING", value)


def _safe_document_preflight_failure(
    pilot: PilotBudget, calls_before: int, error: Exception
) -> bool:
    return (
        not pilot.stopped
        and pilot.calls == calls_before
        and isinstance(error, CallFailed)
        and error.code in {"REQUEST_SIZE_LIMIT", "INVALID_REQUEST"}
    )


def _record_preflight_failure(
    engine: Engine,
    lease: tasks.Lease,
    pilot: PilotBudget | None,
    calls_before: int,
    error: Exception,
) -> None:
    try:
        with Session(engine) as session, session.begin():
            tasks.fail_execution(session, lease, transient=False)
    except BaseException:
        if pilot is not None:
            pilot.stop()
        raise
    if pilot is not None and not _safe_document_preflight_failure(
        pilot, calls_before, error
    ):
        pilot.stop()


def execute_call[T](
    engine: Engine,
    lease: tasks.Lease,
    preflight: Callable[[], Callable[[], T]],
) -> CallResult[T]:
    """Allow only recorded, no-send document preflight failures to continue."""

    def check() -> None:
        with Session(engine) as session, session.begin():
            tasks.require_lease(session, lease)

    with provider_lease(check), provider_turn():
        return _execute_call(engine, lease, preflight)


def _execute_call[T](
    engine: Engine,
    lease: tasks.Lease,
    preflight: Callable[[], Callable[[], T]],
) -> CallResult[T]:
    pilot = current_pilot(required=False)
    calls_before = pilot.calls if pilot is not None else 0
    try:
        send = preflight()
    except PilotBudgetError:
        if pilot is not None:
            pilot.stop()
        raise
    except Exception as error:
        _record_preflight_failure(engine, lease, pilot, calls_before, error)
        raise
    except BaseException:
        if pilot is not None:
            pilot.stop()
        raise
    try:
        return _execute_prepared_call(engine, lease, send)
    except BaseException:
        if pilot is not None:
            pilot.stop()
        raise
