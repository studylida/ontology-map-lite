"""Nested exception diagnostics only; no model, database or network execution."""

import errno
import json

import httpx
import pytest

from ontology_map.llm_diagnostics import (
    carry_failure,
    failure_diagnostic,
    record_failure,
)

PRIVATE = "private-key-source-path"


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
    ],
)
@pytest.mark.parametrize("snapshot", [False, True])
def test_transport_type_survives_lower_level_cause(error_type, snapshot):
    # Real transport errors can wrap OS failures through intermediate exceptions.
    # The diagnostic must retain the HTTPX distinction as well as the OS errno.
    cause = OSError(errno.ETIMEDOUT, PRIVATE, PRIVATE)
    intermediate = RuntimeError(PRIVATE)
    intermediate.__cause__ = cause
    original = error_type(PRIVATE)
    original.__cause__ = intermediate
    if snapshot:
        record_failure(
            original,
            stage="HTTP_SEND",
            request_hash="a" * 64,
            send_entered=True,
            usage_confirmed=False,
        )
    helper = RuntimeError("RESPONSE_UNKNOWN")
    helper.__context__ = original
    stopped = RuntimeError("PILOT_STOPPED")
    stopped.__context__ = helper
    carry_failure(stopped, helper, role="binding_projection_support")
    details = failure_diagnostic(stopped)
    assert details["exception_type"] == error_type.__name__
    assert details["io_errno"] == errno.ETIMEDOUT
    assert details["role"] == "binding_projection_support"
    assert details["stage"] == ("HTTP_SEND" if snapshot else "UNKNOWN")
    assert details["send_entered"] is (True if snapshot else None)
    assert details["usage_confirmed"] is (False if snapshot else None)
    assert details["http_status"] is None
    assert PRIVATE not in json.dumps(details)
    # Export must not alter the original chain or reclassify the public stop.
    assert str(stopped) == "PILOT_STOPPED"
    assert original.__cause__ is intermediate
    assert intermediate.__cause__ is cause


def test_http_status_type_is_not_replaced_by_generic_cause():
    request = httpx.Request("POST", "https://example.invalid/" + PRIVATE)
    response = httpx.Response(429, request=request, text=PRIVATE)
    original = httpx.HTTPStatusError(PRIVATE, request=request, response=response)
    original.__cause__ = ValueError(PRIVATE)
    stopped = RuntimeError("PILOT_STOPPED")
    stopped.__context__ = original
    details = failure_diagnostic(stopped)
    assert details["exception_type"] == "HTTPStatusError"
    assert details["http_status"] == 429
    assert details["send_entered"] is None
    assert details["usage_confirmed"] is None
    assert PRIVATE not in json.dumps(details)


def test_ledger_failure_without_httpx_keeps_os_cause():
    cause = OSError(errno.ENOSPC, PRIVATE, PRIVATE)
    error = RuntimeError("PILOT_FILE_WRITE_FAILED")
    error.__cause__ = cause
    details = failure_diagnostic(error)
    assert details["exception_type"] == "OSError"
    assert details["error_code"] == "PILOT_FILE_WRITE_FAILED"
    assert details["io_errno"] == errno.ENOSPC
    assert details["http_status"] is None
    assert PRIVATE not in json.dumps(details)
