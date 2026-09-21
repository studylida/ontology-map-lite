"""Synthetic failures only; no product DB, provider calls or raw diagnostics."""

import errno
import json
import os
import socket
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from ontology_map.extraction_contracts import ClaimReviewBatch as Verdict
from ontology_map.kimi_transport import KimiStructuredTransport
from ontology_map.llm_config import MODEL_VERSION
from ontology_map.llm_contracts import Budget, CallFailed, CallLimits, token_cost
from ontology_map.llm_diagnostics import (
    carry_failure,
    failure_diagnostic,
    record_failure,
)
from ontology_map.model_studio import KimiModels
from ontology_map.pilot_budget import PilotBudget, PilotBudgetError

PRIVATE = "SECRET-key-source-response-path"
LIMITS = CallLimits(2_000, 256, 100_000)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def response(request, **changes):
    payload = {
        "model": MODEL_VERSION,
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": '{"claims":[]}'},
            }
        ],
    }
    payload.update(changes)
    return httpx.Response(200, request=request, json=payload)


def prepare(client):
    return client.prepare(
        model=MODEL_VERSION,
        messages=[{"role": "system", "content": PRIVATE}],
        schema_name="ClaimReviewBatch",
        schema=Verdict.model_json_schema(),
        limits=LIMITS,
    )


def export(error):
    details = failure_diagnostic(error)
    serialized = json.dumps(details)
    assert PRIVATE not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized
    return details


@pytest.mark.parametrize(
    "error_type",
    [httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout],
)
def test_helper_keeps_typed_timeout_in_safe_context(error_type, tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        raise error_type(PRIVATE, request=request)

    pilot = PilotBudget("timeout", 4, Decimal("3"), tmp_path / "pilot.jsonl")
    client = KimiModels(
        SecretStr(PRIVATE),
        Budget(4, Decimal("3")),
        transport=httpx.MockTransport(handle),
    )
    try:
        with pilot.activate():
            with pytest.raises(CallFailed, match="RESPONSE_UNKNOWN") as caught:
                client.call(
                    "claim_review",
                    PRIVATE,
                    Verdict(claims=[]),
                    Verdict,
                    LIMITS,
                )
            details = export(caught.value)
            assert details["exception_type"] == error_type.__name__
            assert details["stage"] == "HTTP_SEND"
            assert details["send_entered"] is True
            assert details["http_status"] is None
            assert details["usage_confirmed"] is False
            assert pilot.stopped
            assert len(calls) == 1 and len(client.budget.records) == 1
            assert details["request_sha256"] == client.budget.records[0].request_hash
            assert client.budget.records[0].input_tokens is None
    finally:
        client.close()
        pilot.close()


@pytest.mark.parametrize("status", [307, 400, 401, 403, 429, 500, 504])
def test_http_failure_keeps_original_type_status_and_reservation(status, tmp_path):
    client = KimiStructuredTransport(
        SecretStr(PRIVATE),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(status, request=req, text=PRIVATE)
        ),
    )
    pilot = PilotBudget("http", 2, Decimal("2"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate():
            operation = prepare(client)
            with pytest.raises(httpx.HTTPStatusError) as caught:
                operation()
            details = export(caught.value)
            assert details["stage"] == "HTTP_STATUS"
            assert details["http_status"] == status
            assert details["usage_confirmed"] is False
            assert operation.usage is None and pilot.stopped
            with pytest.raises(CallFailed, match="UNEXPECTED_RETRY"):
                operation()
        events = [json.loads(line) for line in pilot.path.read_text().splitlines()]
        assert [event.get("kind") for event in events] == [None, "reserved"]
    finally:
        client.close()
        pilot.close()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"usage": None}, "USAGE_MISSING"),
        ({"usage": {}}, "USAGE_TYPE"),
        ({"usage": {"prompt_tokens": True}}, "USAGE_TYPE"),
        (
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 119,
                }
            },
            "USAGE_TOTAL",
        ),
        (
            {
                "usage": {
                    "prompt_tokens": 2001,
                    "completion_tokens": 20,
                    "total_tokens": 2021,
                }
            },
            "INPUT_LIMIT",
        ),
        (
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 257,
                    "total_tokens": 357,
                }
            },
            "OUTPUT_LIMIT",
        ),
        ({"model": PRIVATE}, "MODEL_MISMATCH"),
    ],
)
def test_response_failures_preserve_specific_reason(changes, reason, tmp_path):
    client = KimiStructuredTransport(
        SecretStr(PRIVATE),
        transport=httpx.MockTransport(lambda req: response(req, **changes)),
    )
    pilot = PilotBudget("response", 1, Decimal("1"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate():
            with pytest.raises(CallFailed, match="RESPONSE_UNKNOWN") as caught:
                prepare(client)()
            details = export(caught.value)
            assert details["reason"] == reason
            assert details["http_status"] == 200
            assert details["usage_confirmed"] is False
            assert details["error_code"] == "RESPONSE_UNKNOWN"
            assert pilot.stopped
    finally:
        client.close()
        pilot.close()


@pytest.mark.parametrize(
    "body,reason", [(PRIVATE, "JSON_INVALID"), ("[]", "ENVELOPE_NOT_OBJECT")]
)
def test_invalid_envelope_is_not_reported_as_model_output(body, reason, tmp_path):
    client = KimiStructuredTransport(
        SecretStr(PRIVATE),
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, request=req, text=body)
        ),
    )
    pilot = PilotBudget("envelope", 1, Decimal("1"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate():
            with pytest.raises(CallFailed) as caught:
                prepare(client)()
            details = export(caught.value)
            assert details["reason"] == reason
            assert details["stage"] == "RESPONSE_JSON"
            assert pilot.stopped
    finally:
        client.close()
        pilot.close()


@pytest.mark.parametrize("when", ["reserve", "confirm"])
def test_ledger_io_failure_keeps_stage_errno_and_no_usage_confirmation(
    when, tmp_path, monkeypatch
):
    calls = []
    pilot = PilotBudget("io", 1, Decimal("1"), tmp_path / "pilot.jsonl")

    def fail(_fd):
        raise OSError(errno.ENOSPC, PRIVATE, PRIVATE)

    def handle(request):
        calls.append(request)
        monkeypatch.setattr(os, "fsync", fail)
        return response(request)

    client = KimiStructuredTransport(
        SecretStr(PRIVATE), transport=httpx.MockTransport(handle)
    )
    try:
        if when == "reserve":
            monkeypatch.setattr(os, "fsync", fail)
        with pilot.activate():
            with pytest.raises(PilotBudgetError) as caught:
                prepare(client)()
            details = export(caught.value)
            expected = "PILOT_RESERVE" if when == "reserve" else "LEDGER_CONFIRM"
            assert details["stage"] == expected
            assert details["error_code"] == "PILOT_FILE_WRITE_FAILED"
            assert details["io_errno"] == errno.ENOSPC
            assert details["send_entered"] is (when == "confirm")
            assert details["usage_confirmed"] is False
            assert pilot.stopped and len(calls) == int(when == "confirm")
    finally:
        client.close()
        pilot.close()


def test_stop_between_prepare_and_send_is_not_a_transmission(tmp_path):
    def forbidden(request):
        pytest.fail("send should not be entered")

    client = KimiStructuredTransport(
        SecretStr(PRIVATE), transport=httpx.MockTransport(forbidden)
    )
    pilot = PilotBudget("stopped", 1, Decimal("1"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate():
            operation = prepare(client)
            pilot.stop()
            with pytest.raises(PilotBudgetError) as caught:
                operation()
            details = export(caught.value)
            assert details["stage"] == "PILOT_ACTIVE"
            assert details["send_entered"] is False
            assert details["http_status"] is None
    finally:
        client.close()
        pilot.close()


def test_valid_usage_then_bad_output_does_not_become_pilot_stop(tmp_path):
    client = KimiStructuredTransport(
        SecretStr(PRIVATE),
        transport=httpx.MockTransport(lambda req: response(req, choices=[])),
    )
    pilot = PilotBudget("output", 1, Decimal("1"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate():
            operation = prepare(client)
            with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR") as caught:
                operation()
            details = export(caught.value)
            assert details["stage"] == "OUTPUT"
            assert details["usage_confirmed"] is True
            assert operation.usage == (100, 20) and not pilot.stopped
            assert pilot.charged_upper_usd == token_cost(MODEL_VERSION, 100, 20)
    finally:
        client.close()
        pilot.close()


def test_legacy_context_is_bounded_and_does_not_invent_wire_facts():
    try:
        try:
            raise httpx.ReadTimeout(PRIVATE)
        except Exception:
            raise CallFailed("RESPONSE_UNKNOWN", fatal=True) from None
    except Exception as source:
        stopped = PilotBudgetError("PILOT_STOPPED")
        stopped.__context__ = source
        details = export(stopped)
        assert details["exception_type"] == "ReadTimeout"
        assert details["error_code"] == "RESPONSE_UNKNOWN"
        assert details["stage"] == "UNKNOWN"
        assert details["send_entered"] is None
        assert details["usage_confirmed"] is None
        source.__context__.__context__ = stopped  # Deliberate exception cycle.
        export(stopped)


def test_unknown_exception_text_names_notes_and_attributes_are_never_exported():
    error = type(PRIVATE, (Exception,), {})(PRIVATE)
    error.add_note(PRIVATE)
    error.__dict__[PRIVATE] = PRIVATE
    record_failure(error, stage=PRIVATE, reason=PRIVATE, request_hash=PRIVATE)
    details = export(error)
    assert details["stage"] == details["reason"] == "UNKNOWN"
    assert details["exception_type"] == "OTHER_ERROR"
    assert details["request_sha256"] is None
    target = PilotBudgetError("PILOT_STOPPED")
    carry_failure(target, error, role=PRIVATE)
    assert export(target)["role"] is None


def test_runner_helper_stop_keeps_diagnostic_and_never_uses_durable_slot(tmp_path):
    from ontology_map.extraction_runner import _RunModels

    def handle(request):
        raise httpx.ReadTimeout(PRIVATE, request=request)

    pilot = PilotBudget("fourth", 4, Decimal("3"), tmp_path / "pilot.jsonl")
    helpers = KimiModels(
        SecretStr(PRIVATE),
        Budget(4, Decimal("3")),
        transport=httpx.MockTransport(handle),
    )
    # Exercise the real helper boundary only. No engine/lease is provided.
    runner = object.__new__(_RunModels)
    runner.helpers = helpers
    try:
        with pilot.activate():
            for index in range(3):
                slot = pilot.reserve(MODEL_VERSION, LIMITS, f"{index:064x}")
                pilot.confirm(slot, MODEL_VERSION, 100, 20)
            with pytest.raises(PilotBudgetError, match="PILOT_STOPPED") as caught:
                runner._helper(
                    "claim_review",
                    PRIVATE,
                    Verdict(claims=[]),
                    Verdict,
                    LIMITS,
                )
            details = export(caught.value)
            assert details["exception_type"] == "ReadTimeout"
            assert details["role"] == "claim_review"
            assert details["stage"] == "HTTP_SEND"
            assert pilot.calls == 4 and pilot.stopped
            caught.value.__context__ = None
            assert export(caught.value) == details  # Snapshot survives wrapping.
        events = [json.loads(line) for line in pilot.path.read_text().splitlines()]
        assert sum(item.get("kind") == "reserved" for item in events) == 4
        assert sum(item.get("kind") == "confirmed" for item in events) == 3
    finally:
        helpers.close()
        pilot.close()


def test_durable_failure_survives_later_pilot_stop_check(tmp_path):
    def handle(request):
        return httpx.Response(429, request=request)

    pilot = PilotBudget("durable", 4, Decimal("3"), tmp_path / "pilot.jsonl")
    transport = KimiStructuredTransport(
        SecretStr(PRIVATE), transport=httpx.MockTransport(handle)
    )
    try:
        with pilot.activate():
            operation = prepare(transport)
            with pytest.raises(httpx.HTTPStatusError):
                operation()
            with pytest.raises(PilotBudgetError, match="PILOT_STOPPED") as caught:
                pilot.require_active()
            details = export(caught.value)
            assert details["exception_type"] == "HTTPStatusError"
            assert details["stage"] == "HTTP_STATUS"
            assert details["http_status"] == 429
            assert details["request_sha256"] == operation.request_hash
            assert details["send_entered"] is True
            assert details["usage_confirmed"] is False
    finally:
        transport.close()
        pilot.close()
