"""Explicit process-wide pacing and caller-owned lease fences.

No account limits are inferred. No DB is imported, no reservations/retries are
created. A turn spans preflight/reservation/send; waiting precedes reservations.
Callers must acquire turns OUTSIDE transactions and supply short lease checks.
"""

import math
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic, sleep

from ontology_map.llm_contracts import CallFailed

_guard: ContextVar[Callable[[], None] | None] = ContextVar("llm_lease", default=None)
_pacer: ContextVar["ProcessPacer | None"] = ContextVar("llm_pacer", default=None)
_start_lock = threading.Lock()
_last_send: float | None = None


class ProcessPacer:
    """One interpreter only. Other processes/projects require operator coordination."""

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        max_concurrency: int = 1,
        clock: Callable[[], float] = monotonic,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        if (
            not math.isfinite(min_interval_seconds)
            or min_interval_seconds < 0
            or type(max_concurrency) is not int
            or not 1 <= max_concurrency <= 4
        ):
            raise ValueError("INVALID_PACING_INTERVAL")
        self.interval = min_interval_seconds
        self.max_concurrency = max_concurrency
        self.clock = clock
        self.wait = wait
        self._slots = threading.Semaphore(max_concurrency)
        self._local = threading.local()
        self._last: float | None = None

    def _previous_send(self) -> float | None:
        return _last_send if self.clock is monotonic else self._last

    @contextmanager
    def turn(self) -> Iterator[None]:
        depth = getattr(self._local, "depth", 0)
        if depth > 0:
            check_lease()
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth = depth
            return

        check_lease()
        self._slots.acquire()
        gate_held = False
        try:
            check_lease()
            _start_lock.acquire()
            gate_held = True
            last = self._previous_send()
            if last is not None:
                remaining = last + self.interval - self.clock()
                while remaining > 0:
                    self.wait(remaining)
                    remaining = last + self.interval - self.clock()
            check_lease()
            self._local.depth = 1
            self._local.gate_held = True
            try:
                yield
            finally:
                self._local.depth = 0
                gate_held = getattr(self._local, "gate_held", False)
                self._local.gate_held = False
        finally:
            if gate_held:
                _start_lock.release()
            self._slots.release()

    def mark_send(self) -> None:
        global _last_send
        owned_gate = getattr(self._local, "gate_held", False)
        if not owned_gate:
            _start_lock.acquire()
        try:
            check_lease()
            self._last = self.clock()
            if self.clock is monotonic:
                _last_send = self._last
        finally:
            if owned_gate:
                self._local.gate_held = False
            _start_lock.release()


@contextmanager
def pacing_scope(pacer: ProcessPacer | None) -> Iterator[None]:
    """Application wiring owns the explicit interval; nested callers reuse it."""
    token = _pacer.set(pacer if pacer is not None else _pacer.get())
    try:
        yield
    finally:
        _pacer.reset(token)


@contextmanager
def provider_lease(check: Callable[[], None]) -> Iterator[None]:
    token = _guard.set(check)
    try:
        yield
    finally:
        _guard.reset(token)


def check_lease() -> None:
    guard = _guard.get()
    if guard is not None:
        guard()


def require_pacer(*, live: bool) -> None:
    pacer = _pacer.get()
    if live and (pacer is None or pacer.interval <= 0):
        raise CallFailed("PACING_REQUIRED", fatal=True)


@contextmanager
def provider_turn() -> Iterator[None]:
    pacer = _pacer.get()
    if pacer is None:
        yield
    else:
        with pacer.turn():
            yield


def mark_send() -> None:
    check_lease()
    pacer = _pacer.get()
    if pacer is not None:
        pacer.mark_send()
