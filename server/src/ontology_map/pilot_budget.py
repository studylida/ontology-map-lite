"""Private, single-process paid-send ledger for one explicitly approved pilot."""

import fcntl
import json
import math
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Iterator

import httpx

from ontology_map.llm_config import BILLABLE_INPUT_CEILING, MAX_INPUT_TOKENS
from ontology_map.llm_diagnostics import carry_failure
from ontology_map.model_studio import RATES, CallLimits, token_cost


class PilotBudgetError(RuntimeError):
    """Stop application sequencing without recording a provider outcome."""


_active: ContextVar["PilotBudget | None"] = ContextVar("pilot_budget", default=None)


def current_pilot(*, required: bool) -> "PilotBudget | None":
    pilot = _active.get()
    if pilot is None and required:
        raise PilotBudgetError("PILOT_BUDGET_REQUIRED")
    return pilot


def request_digest(request: httpx.Request) -> str:
    """Identify method, endpoint, and body without retaining their contents."""
    return sha256(
        request.method.encode("ascii")
        + b"\n"
        + str(request.url).encode("utf-8")
        + b"\n"
        + request.content
    ).hexdigest()


class PilotBudget:
    """One new 0600 file per pilot; an existing file forbids automatic resume."""

    def __init__(
        self,
        pilot_id: str,
        max_calls: int | None,
        max_usd: Decimal | None,
        path: Path,
        *,
        demo_uncapped: bool = False,
    ) -> None:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", pilot_id)
            or not path.is_absolute()
            or type(demo_uncapped) is not bool
            or (demo_uncapped and (max_calls is not None or max_usd is not None))
            or (
                not demo_uncapped
                and (
                    type(max_calls) is not int
                    or max_calls <= 0
                    or not isinstance(max_usd, Decimal)
                    or not max_usd.is_finite()
                    or max_usd <= 0
                )
            )
        ):
            raise PilotBudgetError("INVALID_PILOT_BUDGET")
        self.pilot_id = pilot_id
        self.max_calls = max_calls
        self.max_usd = max_usd
        self.demo_uncapped = demo_uncapped
        self.path = path
        self.stopped = False
        self._failure: PilotBudgetError | None = None
        self.calls = 0
        self.external_calls = 0
        self.charged_upper_usd = Decimal(0)
        self._estimates: dict[int, tuple[str, int, Decimal]] = {}
        self._external_pending: set[int] = set()
        self._lock = threading.Lock()
        self._running = 0
        self._fd = -1
        try:
            self._fd = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
            )
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._append(
                {
                    "version": 1,
                    "pilot_id": pilot_id,
                    "max_calls": max_calls,
                    "max_usd": str(max_usd) if max_usd is not None else None,
                    "mode": "DEMO_UNCAPPED" if demo_uncapped else "CAPPED",
                }
            )
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except (OSError, PilotBudgetError) as error:
            self.stopped = True
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
            raise PilotBudgetError("PILOT_FILE_UNAVAILABLE") from error

    def _append(self, event: dict[str, object]) -> None:
        data = (
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        try:
            if os.write(self._fd, data) != len(data):
                raise OSError("short pilot ledger write")
            os.fsync(self._fd)
        except OSError as error:
            self.stopped = True
            raise PilotBudgetError("PILOT_FILE_WRITE_FAILED") from error

    @contextmanager
    def activate(self) -> Iterator[None]:
        with self._lock:
            if self.stopped or self._fd < 0:
                raise PilotBudgetError("PILOT_STOPPED")
            if self._running and not self.demo_uncapped:
                self.stopped = True
                raise PilotBudgetError("PILOT_CONCURRENT_RUN")
            self._running += 1
        token = _active.set(self)
        try:
            yield
        except BaseException:
            with self._lock:
                self.stopped = True
            raise
        finally:
            _active.reset(token)
            with self._lock:
                self._running -= 1

    def reserve(self, model: str, limits: CallLimits, request_sha256: str) -> int:
        with self._lock:
            if self.stopped or self._fd < 0:
                raise PilotBudgetError("PILOT_STOPPED")
            if not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
                self.stopped = True
                raise PilotBudgetError("PILOT_REQUEST_ID_INVALID")
            if model not in RATES:
                self.stopped = True
                raise PilotBudgetError("PILOT_MODEL_UNPRICED")
            estimate = token_cost(
                model, BILLABLE_INPUT_CEILING, limits.max_output_tokens
            )
            if not self.demo_uncapped and (
                self.calls >= self.max_calls  # type: ignore[operator]
                or self.charged_upper_usd + estimate > self.max_usd  # type: ignore[operator]
            ):
                self.stopped = True
                raise PilotBudgetError("PILOT_LIMIT_REACHED")
            sequence = self.calls + 1
            self._append(
                {
                    "kind": "reserved",
                    "sequence": sequence,
                    "model": model,
                    "request_sha256": request_sha256,
                    "estimated_upper_usd": str(estimate),
                }
            )
            self.calls = sequence
            self.charged_upper_usd += estimate
            self._estimates[sequence] = (model, limits.max_output_tokens, estimate)
            return sequence

    def confirm(
        self,
        sequence: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        elapsed_seconds: float | None = None,
    ) -> None:
        with self._lock:
            if self.stopped or sequence not in self._estimates:
                raise PilotBudgetError("PILOT_STOPPED")
            reserved_model, max_output, estimate = self._estimates[sequence]
            if (
                model != reserved_model
                or type(input_tokens) is not int
                or type(output_tokens) is not int
                or not 0 < input_tokens <= MAX_INPUT_TOKENS
                or not 0 <= output_tokens <= max_output
                or (
                    elapsed_seconds is not None
                    and (
                        type(elapsed_seconds) is not float
                        or not math.isfinite(elapsed_seconds)
                        or elapsed_seconds < 0
                    )
                )
            ):
                self.stopped = True
                raise PilotBudgetError("PILOT_USAGE_INVALID")
            actual = token_cost(model, input_tokens, output_tokens)
            if actual > estimate:
                self.stopped = True
                raise PilotBudgetError("PILOT_USAGE_EXCEEDS_RESERVATION")
            self._append(
                {
                    "kind": "confirmed",
                    "sequence": sequence,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "charged_upper_usd": str(actual),
                    "elapsed_seconds": elapsed_seconds,
                }
            )
            self.charged_upper_usd += actual - estimate
            del self._estimates[sequence]

    def reserve_unpriced_call(
        self,
        *,
        provider: str,
        model: str,
        role: str,
        request_sha256: str,
    ) -> int:
        with self._lock:
            if (
                not self.demo_uncapped
                or self.stopped
                or self._fd < 0
                or not all(
                    re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value)
                    for value in (provider, model, role)
                )
                or not re.fullmatch(r"[0-9a-f]{64}", request_sha256)
            ):
                raise PilotBudgetError("PILOT_EXTERNAL_CALL_INVALID")
            self.external_calls += 1
            sequence = self.external_calls
            self._append(
                {
                    "kind": "unpriced_reserved",
                    "sequence": sequence,
                    "provider": provider,
                    "model": model,
                    "role": role,
                    "request_sha256": request_sha256,
                }
            )
            self._external_pending.add(sequence)
            return sequence

    def confirm_unpriced_call(
        self,
        sequence: int,
        *,
        status: str,
        elapsed_seconds: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        with self._lock:
            if (
                self.stopped
                or sequence not in self._external_pending
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", status)
                or not math.isfinite(elapsed_seconds)
                or elapsed_seconds < 0
                or (input_tokens is not None and input_tokens < 0)
                or (output_tokens is not None and output_tokens < 0)
            ):
                raise PilotBudgetError("PILOT_EXTERNAL_CALL_INVALID")
            self._append(
                {
                    "kind": "unpriced_confirmed",
                    "sequence": sequence,
                    "status": status,
                    "elapsed_seconds": elapsed_seconds,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            )
            self._external_pending.remove(sequence)

    def stop(self, error: BaseException | None = None) -> None:
        with self._lock:
            self.stopped = True
            if error is not None and self._failure is None:
                safe = PilotBudgetError("PILOT_STOPPED")
                carry_failure(safe, error, role="")
                self._failure = safe

    def require_active(self) -> None:
        with self._lock:
            if self.stopped or self._fd < 0:
                stopped = PilotBudgetError("PILOT_STOPPED")
                if self._failure is not None:
                    carry_failure(stopped, self._failure, role="")
                raise stopped

    def close(self) -> None:
        with self._lock:
            self.stopped = True
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
