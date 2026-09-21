"""Short caller-owned transactions for #127's approved durable call ledger.

No provider IO, payload persistence, hidden commit, or runtime-helper accounting.
Callers commit claim/reservation before transmission and use a fresh transaction
for the terminal result. A new opaque lease token fences each acquisition.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

MAX_SLOTS = 3
LEASE_DURATION = timedelta(minutes=10)
DurableTaskKind = Literal[
    "KNOWLEDGE_EXTRACTION",
    "FOLLOWUP_QUESTIONS",
    "NODE_INSIGHT",
    "NODE_CONTEXT",
]
DURABLE_TASK_KINDS = frozenset(
    ("KNOWLEDGE_EXTRACTION", "FOLLOWUP_QUESTIONS", "NODE_INSIGHT", "NODE_CONTEXT")
)
Outcome = Literal[
    "SUCCESS",
    "TIMEOUT",
    "RATE_LIMITED",
    "PROVIDER_ERROR",
    "AUTHENTICATION_ERROR",
    "INVALID_REQUEST",
    "OUTPUT_CONTRACT_ERROR",
]
OUTCOMES = frozenset(
    (
        "SUCCESS",
        "TIMEOUT",
        "RATE_LIMITED",
        "PROVIDER_ERROR",
        "AUTHENTICATION_ERROR",
        "INVALID_REQUEST",
        "OUTPUT_CONTRACT_ERROR",
    )
)
TERMINAL = frozenset(("SUCCESS", "VALIDATION_BLOCKED", "FINAL_FAILED"))


class LeaseLost(RuntimeError):
    "The caller must discard its runtime result and must not write knowledge."


class SlotBusy(RuntimeError):
    """Another transmission is already reserved under this task's lease."""


@dataclass(frozen=True)
class Lease:
    task_id: int
    owner: str
    expires_at: datetime


@dataclass(frozen=True)
class CallSlot:
    lease: Lease
    slot_no: int
    reserved_at: datetime


@dataclass(frozen=True)
class TerminalResult:
    outcome: Outcome
    attempted_at: datetime
    failure_reason: str | None = None
    transient: bool = False
    retry_after: timedelta | None = None

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError("INVALID_TERMINAL_OUTCOME")
        if self.attempted_at.utcoffset() is None:
            raise ValueError("ATTEMPT_TIME_REQUIRES_TIMEZONE")
        if (self.outcome == "SUCCESS") != (self.failure_reason is None):
            raise ValueError("INVALID_TERMINAL_REASON")
        if self.failure_reason is not None and not re.fullmatch(
            r"[A-Z][A-Z0-9_:.-]{0,127}", self.failure_reason
        ):
            raise ValueError("UNSAFE_TERMINAL_REASON")
        if self.retry_after is not None and self.retry_after < timedelta(0):
            raise ValueError("NEGATIVE_RETRY_AFTER")


def _locked(session: Session, task_id: int) -> RowMapping:
    return (
        session.execute(
            sa.text("SELECT * FROM model_task WHERE model_task_id=:id FOR UPDATE"),
            {"id": task_id},
        )
        .mappings()
        .one()
    )


def _now(session: Session) -> datetime:
    # Called AFTER acquiring the task lock, not at transaction start.
    return cast(
        datetime, session.execute(sa.text("SELECT clock_timestamp()")).scalar_one()
    )


def require_lease(session: Session, lease: Lease) -> RowMapping:
    task = _locked(session, lease.task_id)
    now = _now(session)
    if (
        task["status"] != "RUNNING"
        or task["lease_owner"] != lease.owner
        or task["lease_expires_at"] != lease.expires_at
        or lease.expires_at <= now
    ):
        raise LeaseLost("LEASE_LOST")
    return task


def _ledger(session: Session, task: RowMapping) -> list[RowMapping]:
    slots = list(
        session.execute(
            sa.text(
                (
                    "SELECT * FROM provider_call_slot WHERE model_task_id=:id "
                    "ORDER BY slot_no"
                )
            ),
            {"id": task["model_task_id"]},
        ).mappings()
    )
    numbers = [int(s["slot_no"]) for s in slots]
    attempts = set(
        session.execute(
            sa.text("SELECT attempt_no FROM agent_attempt WHERE model_task_id=:id"),
            {"id": task["model_task_id"]},
        ).scalars()
    )
    completed = {int(s["slot_no"]) for s in slots if s["state"] == "COMPLETED"}
    if (
        numbers != list(range(1, len(slots) + 1))
        or len(slots) > MAX_SLOTS
        or attempts != completed
        or len(attempts) != int(task["attempt_count"])
        or sum(s["state"] == "RESERVED" for s in slots) > 1
    ):
        # Do not fabricate historical reservations for legacy unfinished tasks.
        raise ValueError("INCONSISTENT_CALL_LEDGER")
    return slots


def _set_state(
    session: Session,
    task_id: int,
    state: str,
    now: datetime,
    next_attempt_at: datetime | None = None,
) -> None:
    session.execute(
        sa.text("""
        UPDATE model_task SET status=:state, finished_at=:finished,
          next_attempt_at=:next_at, lease_owner=NULL, lease_expires_at=NULL
        WHERE model_task_id=:id
    """),
        {
            "id": task_id,
            "state": state,
            "finished": now if state in TERMINAL else None,
            "next_at": next_attempt_at,
        },
    )


def _require_task_kind(task: RowMapping, expected_task_kind: DurableTaskKind) -> None:
    if expected_task_kind not in DURABLE_TASK_KINDS:
        raise ValueError("UNSUPPORTED_DURABLE_TASK_KIND")
    if task["task_kind"] == expected_task_kind:
        return
    if expected_task_kind == "KNOWLEDGE_EXTRACTION":
        raise ValueError("NOT_AN_EXTRACTION_TASK")
    raise ValueError("UNEXPECTED_TASK_KIND")


def claim_task(
    session: Session,
    task_id: int,
    worker_name: str,
    *,
    expected_task_kind: DurableTaskKind = "KNOWLEDGE_EXTRACTION",
) -> Lease | None:
    """Claim one approved durable provider task without changing ledger semantics."""
    if not worker_name.strip():
        raise ValueError("EMPTY_WORKER_NAME")
    task = _locked(session, task_id)
    _require_task_kind(task, expected_task_kind)
    now = _now(session)
    state = task["status"]
    if state in TERMINAL:
        return None
    if state == "RUNNING" and task["lease_expires_at"] > now:
        return None
    if state == "RETRY_WAIT" and task["next_attempt_at"] > now:
        return None
    slots = _ledger(session, task)
    if state == "RUNNING":
        session.execute(
            sa.text("""
            UPDATE provider_call_slot SET state='UNKNOWN', resolved_at=:now
            WHERE model_task_id=:id AND state='RESERVED'
        """),
            {"id": task_id, "now": now},
        )
    elif any(s["state"] == "RESERVED" for s in slots):
        raise ValueError("RESERVATION_WITHOUT_RUNNING_TASK")
    if len(slots) >= MAX_SLOTS:
        _set_state(session, task_id, "FINAL_FAILED", now)
        return None
    lease = Lease(task_id, f"{worker_name}:{uuid4().hex}", now + LEASE_DURATION)
    session.execute(
        sa.text("""
        UPDATE model_task SET status='RUNNING', next_attempt_at=NULL,
          finished_at=NULL, lease_owner=:owner, lease_expires_at=:expires
        WHERE model_task_id=:id
    """),
        {"id": task_id, "owner": lease.owner, "expires": lease.expires_at},
    )
    return lease


def reserve_slot(session: Session, lease: Lease) -> CallSlot | None:
    "Commit this transaction before sending an already-preflighted request."
    task = require_lease(session, lease)
    slots = _ledger(session, task)
    if any(s["state"] == "RESERVED" for s in slots):
        raise SlotBusy("SLOT_ALREADY_RESERVED")
    now = _now(session)
    if len(slots) >= MAX_SLOTS:
        _set_state(session, lease.task_id, "FINAL_FAILED", now)
        return None
    # NOT attempt_count + 1: UNKNOWN slots create legitimate attempt gaps.
    number = max((int(s["slot_no"]) for s in slots), default=0) + 1
    session.execute(
        sa.text("""
        INSERT INTO provider_call_slot
          (model_task_id, slot_no, state, reserved_at, resolved_at)
        VALUES (:id, :number, 'RESERVED', :now, NULL)
    """),
        {"id": lease.task_id, "number": number, "now": now},
    )
    return CallSlot(lease, number, now)


def _retryable(session: Session, task_id: int, result: TerminalResult) -> bool:
    if result.outcome in ("TIMEOUT", "RATE_LIMITED"):
        return True
    if result.outcome == "PROVIDER_ERROR":
        return result.transient
    if result.outcome == "OUTPUT_CONTRACT_ERROR":
        count = session.execute(
            sa.text("""
            SELECT count(*) FROM agent_attempt
            WHERE model_task_id=:id AND outcome='OUTPUT_CONTRACT_ERROR'
        """),
            {"id": task_id},
        ).scalar_one()
        return int(count) < 2
    return False


def _retry_or_finish(
    session: Session,
    task: RowMapping,
    now: datetime,
    retryable: bool,
    retry_after: timedelta | None,
) -> str:
    slots = _ledger(session, task)
    state = "FINAL_FAILED"
    next_at = None
    if retryable and len(slots) < MAX_SLOTS:
        state = "RETRY_WAIT"
        delay = retry_after
        if delay is None:
            delay = timedelta(seconds=30 if int(task["attempt_count"]) <= 1 else 120)
        next_at = now + delay
    _set_state(session, int(task["model_task_id"]), state, now, next_at)
    return state


def record_terminal(session: Session, slot: CallSlot, result: TerminalResult) -> str:
    "Atomic terminal attempt/count/COMPLETED; SUCCESS still awaits product apply."
    task = require_lease(session, slot.lease)
    slots = _ledger(session, task)
    selected = next((s for s in slots if s["slot_no"] == slot.slot_no), None)
    if (
        selected is None
        or selected["state"] != "RESERVED"
        or selected["reserved_at"] != slot.reserved_at
    ):
        raise LeaseLost("SLOT_NO_LONGER_RESERVED")
    now = _now(session)
    session.execute(
        sa.text("""
        INSERT INTO agent_attempt
          (model_task_id, attempt_no, outcome, failure_reason, attempted_at)
        VALUES (:id, :number, :outcome, :reason, :attempted)
    """),
        {
            "id": slot.lease.task_id,
            "number": slot.slot_no,
            "outcome": result.outcome,
            "reason": result.failure_reason,
            "attempted": result.attempted_at,
        },
    )
    session.execute(
        sa.text("""
        UPDATE model_task SET attempt_count=attempt_count+1
        WHERE model_task_id=:id
    """),
        {"id": slot.lease.task_id},
    )
    session.execute(
        sa.text("""
        UPDATE provider_call_slot SET state='COMPLETED', resolved_at=:now
        WHERE model_task_id=:id AND slot_no=:number AND state='RESERVED'
    """),
        {"id": slot.lease.task_id, "number": slot.slot_no, "now": now},
    )
    if result.outcome == "SUCCESS":
        return "RUNNING"
    task = _locked(session, slot.lease.task_id)
    return _retry_or_finish(
        session,
        task,
        now,
        _retryable(session, slot.lease.task_id, result),
        result.retry_after,
    )


def finish_product(session: Session, lease: Lease, *, valid: bool) -> str:
    """Call in the SAME transaction as verified knowledge and batch COMMITTED.

    valid=True also covers a genuinely normal zero-result (without promotion).
    valid=False is all-candidates-blocked; slot exhaustion takes precedence.
    """
    task = require_lease(session, lease)
    slots = _ledger(session, task)
    if any(s["state"] == "RESERVED" for s in slots):
        raise SlotBusy("PROVIDER_RESULT_NOT_RECORDED")
    state = "SUCCESS" if valid else "VALIDATION_BLOCKED"
    if not valid and len(slots) >= MAX_SLOTS:
        state = "FINAL_FAILED"
    _set_state(session, lease.task_id, state, _now(session))
    return state


def fail_execution(session: Session, lease: Lease, *, transient: bool) -> str:
    "Preflight/helper/promotion failure; never invent a provider attempt."
    task = require_lease(session, lease)
    if any(s["state"] == "RESERVED" for s in _ledger(session, task)):
        # An uncertain transmission stays RESERVED until lease expiry/reclaim.
        raise SlotBusy("UNRESOLVED_TRANSMISSION")
    return _retry_or_finish(session, task, _now(session), transient, None)
