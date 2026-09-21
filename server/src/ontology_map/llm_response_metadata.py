"""Allowlisted provider metadata; never expose arbitrary headers or error text."""

import re
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

_INTEGER_HEADERS = frozenset(
    {
        "x-ratelimit-limit-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-limit-project-tokens",
        "x-ratelimit-remaining-project-tokens",
    }
)
_RESET_HEADERS = frozenset(
    {
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-tokens",
        "x-ratelimit-reset-project-tokens",
    }
)
_QUOTA_CODES = frozenset(
    {
        "insufficient_quota",
        "billing_hard_limit_reached",
        "billing_not_active",
    }
)
_RATE_CODES = frozenset({"rate_limit_exceeded", "rate_limit_reached", "slow_down"})


def _retry_after(raw: str) -> str | None:
    if re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,6})?", raw):
        return raw
    try:
        at = parsedate_to_datetime(raw)
        if at.utcoffset() is not None:
            # Canonical safe date, no provider prose is copied.
            return at.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    except TypeError, ValueError, OverflowError:
        pass
    return None


def safe_headers(headers: httpx.Headers) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in _INTEGER_HEADERS:
        value = headers.get(name, "")
        if re.fullmatch(r"[0-9]{1,16}", value):
            result[name] = value
    for name in _RESET_HEADERS:
        value = headers.get(name, "")
        if re.fullmatch(r"(?:[0-9]{1,10}(?:\.[0-9]{1,6})?(?:ms|s|m|h|d)){1,5}", value):
            result[name] = value
    retry = _retry_after(headers.get("retry-after", ""))
    if retry is not None:
        result["retry-after"] = retry
    return result


class _JsonResponse(Protocol):
    """Shared read-only surface of HTTPX and the installed SDK response."""

    @property
    def status_code(self) -> int: ...

    def json(self) -> Any: ...


def rate_limit_kind(response: _JsonResponse) -> str | None:
    if response.status_code != 429:
        return None
    try:
        payload = response.json()
    except ValueError:
        return "UNKNOWN_429"
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "UNKNOWN_429"
    code, kind = error.get("code"), error.get("type")
    tags = {value for value in (code, kind) if isinstance(value, str)}
    if tags & _QUOTA_CODES:
        return "QUOTA_OR_BILLING"
    if tags & _RATE_CODES:
        return "TEMPORARY_RATE_LIMIT"
    return "UNKNOWN_429"


def envelope_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    response_id = payload.get("id")
    if isinstance(response_id, str) and re.fullmatch(
        r"chatcmpl-[A-Za-z0-9_-]{1,160}", response_id
    ):
        result["provider_response_id"] = response_id
    model = payload.get("model")
    # Only approved response model IDs; rejected names may contain arbitrary text.
    from ontology_map.llm_config import ROLE_PROFILES

    if isinstance(model, str) and model in {
        profile[0] for profile in ROLE_PROFILES.values()
    }:
        result["response_model"] = model
    usage = payload.get("usage")
    if isinstance(usage, dict):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if type(value) is int and 0 <= value <= 10_000_000:
                result[key] = value
        for group, keys in (
            ("prompt_tokens_details", ("cached_tokens", "cache_write_tokens")),
            ("completion_tokens_details", ("reasoning_tokens",)),
        ):
            detail = usage.get(group)
            if isinstance(detail, dict):
                for key in keys:
                    value = detail.get(key)
                    if type(value) is int and 0 <= value <= 10_000_000:
                        result[key] = value
    return result
