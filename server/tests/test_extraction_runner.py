"""Runner-side error classification only; no provider traffic."""

from datetime import timedelta

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError

from ontology_map.extraction_runner import classify_provider_error
from ontology_map.model_studio import CallFailed


@pytest.mark.parametrize(
    "status,outcome,transient",
    [
        (401, "AUTHENTICATION_ERROR", False),
        (403, "AUTHENTICATION_ERROR", False),
        (400, "INVALID_REQUEST", False),
        (422, "INVALID_REQUEST", False),
        (429, "RATE_LIMITED", True),
        (408, "TIMEOUT", True),
        (503, "PROVIDER_ERROR", True),
    ],
)
def test_typed_http_failure_uses_safe_status(status, outcome, transient):
    request = httpx.Request(
        "POST", "https://example.invalid", headers={"Authorization": "secret"}
    )
    response = httpx.Response(
        status,
        request=request,
        headers={"Retry-After": "73"},
        text="private response",
    )
    error = httpx.HTTPStatusError(
        "private exception", request=request, response=response
    )
    classified = classify_provider_error(error)
    assert classified.outcome == outcome
    assert classified.transient is transient
    assert str(classified) == outcome
    assert classified.retry_after == (timedelta(seconds=73) if status == 429 else None)


@pytest.mark.parametrize(
    "error,outcome",
    [
        (httpx.ConnectError("private address"), "PROVIDER_ERROR"),
        (CallFailed("OUTPUT_CONTRACT_ERROR", fatal=False), "OUTPUT_CONTRACT_ERROR"),
    ],
)
def test_known_typed_failures_have_terminal_codes(error, outcome):
    assert classify_provider_error(error).outcome == outcome


def test_unknown_exception_is_not_invented_provider_outcome():
    assert classify_provider_error(RuntimeError("private unknown payload")) is None
    assert classify_provider_error(CallFailed("RESPONSE_UNKNOWN", fatal=True)) is None


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("private lost reply timeout"),
        APITimeoutError(request=httpx.Request("POST", "https://example.invalid")),
        httpx.NetworkError("private ambiguous transport state"),
        httpx.ReadError("private lost reply"),
        httpx.WriteError("private partial send"),
        APIConnectionError(
            request=httpx.Request("POST", "https://example.invalid"),
            message="private ambiguous SDK connection failure",
        ),
    ],
)
def test_ambiguous_network_failure_has_no_terminal_outcome(error):
    assert classify_provider_error(error) is None
