"""Allowlisted, in-memory failure details. No IO, exception text or retry policy.

The private caller may serialize failure_diagnostic(error). Never serialize the
exception, its request/response, traceback locals, or arbitrary __dict__ instead.
"""

import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace

import httpx
from pydantic import ValidationError

_STAGES = frozenset(
    {
        "UNKNOWN",
        "PILOT_RESERVE",
        "PILOT_ACTIVE",
        "HTTP_SEND",
        "HTTP_STATUS",
        "RESPONSE_JSON",
        "RESPONSE_MODEL",
        "USAGE",
        "LEDGER_CONFIRM",
        "OUTPUT",
        "OUTPUT_JSON",
        "OUTPUT_SCHEMA",
    }
)
_REASONS = frozenset(
    {
        "UNKNOWN",
        "JSON_INVALID",
        "ENVELOPE_NOT_OBJECT",
        "MODEL_MISMATCH",
        "USAGE_MISSING",
        "USAGE_TYPE",
        "USAGE_TOTAL",
        "INPUT_LIMIT",
        "OUTPUT_LIMIT",
        "CHOICES_SHAPE",
        "FINISH_REASON",
        "MESSAGE_SHAPE",
        "CONTENT_SHAPE",
        "REFUSAL",
        "USAGE_DETAIL",
    }
)
_CODES = frozenset(
    {
        "RESPONSE_UNKNOWN",
        "OUTPUT_CONTRACT_ERROR",
        "UNEXPECTED_RETRY",
        "CALL_LIMIT",
        "COST_LIMIT",
        "PACING_REQUIRED",
        "INVALID_REQUEST",
        "REQUEST_SIZE_LIMIT",
        "PILOT_STOPPED",
        "PILOT_BUDGET_REQUIRED",
        "PILOT_CONCURRENT_RUN",
        "PILOT_REQUEST_ID_INVALID",
        "PILOT_MODEL_UNPRICED",
        "PILOT_LIMIT_REACHED",
        "PILOT_USAGE_INVALID",
        "PILOT_USAGE_EXCEEDS_RESERVATION",
        "PILOT_FILE_WRITE_FAILED",
        "PILOT_FILE_UNAVAILABLE",
    }
)
_ROLES = frozenset(
    {
        "body",
        "generation",
        "claim_review",
        "claim_support",
        "binding_projection_support",
        "entity_resolution",
        "claim_duplicate",
        "node_context",
        "followup",
        "insight",
    }
)
_ERROR_TYPES = (
    httpx.HTTPStatusError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.LocalProtocolError,
    httpx.RemoteProtocolError,
    httpx.DecodingError,
    httpx.TimeoutException,
    httpx.TransportError,
    OSError,
    ValueError,
    RuntimeError,
)


@dataclass(frozen=True)
class _Failure:
    stage: str = "UNKNOWN"
    reason: str = "UNKNOWN"
    exception_type: str = "OTHER_ERROR"
    error_code: str | None = None
    request_sha256: str | None = None
    send_entered: bool | None = None
    http_status: int | None = None
    usage_confirmed: bool | None = None
    role: str | None = None
    io_errno: int | None = None
    response_id: str | None = None
    response_archive: str | None = None
    validation_paths: tuple[str, ...] = ()
    validation_error_count: int | None = None
    rate_limit_headers: tuple[tuple[str, str], ...] = ()
    rate_limit_kind: str | None = None


def _chain(error: BaseException) -> Iterator[BaseException]:
    """Bounded traversal also works for old exceptions hidden by from None."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(seen) < 16:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _code(error: BaseException) -> str | None:
    value = error.args[0] if error.args else None
    return value if type(value) is str and value in _CODES else None


def _safe_status(value: object) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


def _details(error: BaseException) -> _Failure:
    chain = tuple(_chain(error))
    for item in chain:
        detail = item.__dict__.get("_llm_failure")
        if isinstance(detail, _Failure):
            return detail
    # Legacy exceptions have no wire markers: unknown is not false/no-send.
    # Prefer a specific HTTPX type over an underlying OS/generic cause. The
    # latter still supplies errno below; it must not erase timeout distinctions.
    kind = next(
        (
            cls.__name__
            for cls in _ERROR_TYPES
            for item in reversed(chain)
            if isinstance(item, cls)
        ),
        "OTHER_ERROR",
    )
    code = next((_code(item) for item in reversed(chain) if _code(item)), None)
    status = next(
        (
            _safe_status(item.response.status_code)
            for item in chain
            if isinstance(item, httpx.HTTPStatusError)
        ),
        None,
    )
    errno = next(
        (item.errno for item in reversed(chain) if isinstance(item, OSError)), None
    )
    return _Failure(
        exception_type=kind,
        error_code=code,
        http_status=status,
        io_errno=errno if type(errno) is int and 0 <= errno <= 65535 else None,
    )


def _tag(current: str, value: str, allowed: frozenset[str]) -> str:
    if current != "UNKNOWN":
        return current
    return value if value in allowed else "UNKNOWN"


def record_failure(
    error: BaseException,
    *,
    stage: str,
    reason: str = "UNKNOWN",
    request_hash: str | None = None,
    send_entered: bool | None = None,
    http_status: int | None = None,
    usage_confirmed: bool | None = None,
) -> None:
    """Add safe facts, preserving a more specific inner parsing failure stage."""
    detail = _details(error)
    safe_hash = (
        request_hash
        if request_hash and re.fullmatch(r"[0-9a-f]{64}", request_hash)
        else detail.request_sha256
    )
    error.__dict__["_llm_failure"] = replace(
        detail,
        stage=_tag(detail.stage, stage, _STAGES),
        reason=_tag(detail.reason, reason, _REASONS),
        request_sha256=safe_hash,
        send_entered=(
            send_entered if type(send_entered) is bool else detail.send_entered
        ),
        http_status=_safe_status(http_status) or detail.http_status,
        usage_confirmed=(
            usage_confirmed if type(usage_confirmed) is bool else detail.usage_confirmed
        ),
    )


def carry_failure(target: BaseException, source: BaseException, *, role: str) -> None:
    """Copy only the immutable safe snapshot across the helper stop boundary."""
    target.__dict__["_llm_failure"] = replace(
        _details(source), role=role if role in _ROLES else None
    )


def failure_diagnostic(error: BaseException) -> dict[str, object]:
    """Safe export only. Does not claim provider receipt, billing or DB success."""
    return {"version": 1, **asdict(_details(error))}


def attach_response(error: BaseException, response_id: str | None, status: str) -> None:
    error.__dict__["_llm_failure"] = replace(
        _details(error),
        response_id=(
            response_id
            if response_id and re.fullmatch(r"[0-9a-f]{32}", response_id)
            else None
        ),
        response_archive=(
            status if status in {"SAVED", "FAILED", "NO_RESPONSE"} else None
        ),
    )


def _schema_names(schema: object) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        for key in ("properties", "$defs"):
            value = schema.get(key)
            if isinstance(value, dict):
                names.update(value)
        for value in schema.values():
            names.update(_schema_names(value))
    elif isinstance(schema, list):
        for value in schema:
            names.update(_schema_names(value))
    return names


def record_validation(error: ValidationError, schema: object) -> None:
    """Paths only: extra-key names, input, ctx and custom messages may be secret."""
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    names = _schema_names(schema)
    paths = []
    for item in errors[:64]:
        path = "$"
        for part in item["loc"]:
            if type(part) is int:
                path += f"[{part}]"
            else:
                path += "." + (part if part in names else "<unknown>")
        paths.append(path)
    stage = (
        "OUTPUT_JSON"
        if any(e["type"] == "json_invalid" for e in errors)
        else "OUTPUT_SCHEMA"
    )
    record_failure(error, stage=stage)
    error.__dict__["_llm_failure"] = replace(
        _details(error),
        error_code="OUTPUT_CONTRACT_ERROR",
        validation_paths=tuple(paths),
        validation_error_count=len(errors),
    )


def record_response_metadata(
    error: BaseException, headers: dict[str, str], kind: str | None
) -> None:
    from ontology_map.llm_response_metadata import safe_headers

    cleaned = safe_headers(httpx.Headers(headers))
    error.__dict__["_llm_failure"] = replace(
        _details(error),
        rate_limit_headers=tuple(sorted(cleaned.items())),
        rate_limit_kind=(
            kind
            if kind in {"QUOTA_OR_BILLING", "TEMPORARY_RATE_LIMIT", "UNKNOWN_429"}
            else None
        ),
    )
