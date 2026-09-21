"""Offline checks for the pilot-wide paid-send cap."""

import json
import os
import threading
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from ontology_map import application_execution as app
from ontology_map import durable_provider
from ontology_map.application_execution import dry_run
from ontology_map.extraction_contracts import KnowledgeProposals
from ontology_map.llm_config import BASE_URL, BILLABLE_INPUT_CEILING, role_model
from ontology_map.model_studio import (
    FLASH,
    Budget,
    CallFailed,
    CallLimits,
    ModelStudio,
    token_cost,
)
from ontology_map.pilot_budget import PilotBudget, PilotBudgetError, request_digest
from ontology_map.structured_provider import ModelStudioStructuredTransport

LIMITS = CallLimits(2_000, 1_024, 100_000)


def _response(request: httpx.Request, *, usage: bool = True) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(
        200,
        request=request,
        json={
            "model": body["model"],
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"claims":[]}'},
                }
            ],
            **(
                {
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "total_tokens": 110,
                    }
                }
                if usage
                else {}
            ),
        },
    )


def _prepare(transport: ModelStudioStructuredTransport):
    return transport.prepare(
        model=role_model("generation"),
        messages=[{"role": "system", "content": "private prompt"}],
        schema_name="KnowledgeProposals",
        schema=KnowledgeProposals.model_json_schema(),
        limits=LIMITS,
    )


def test_helper_and_two_durable_tasks_accumulate_before_each_send(tmp_path):
    path = tmp_path / "pilot.jsonl"
    observed = []

    def handle(request: httpx.Request) -> httpx.Response:
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert events[-1]["kind"] == "reserved"
        assert events[-1]["request_sha256"] == request_digest(request)
        observed.append(events[-1]["sequence"])
        return _response(request)

    pilot = PilotBudget("three-doc-pilot", 3, Decimal("1"), path)
    helper = ModelStudio(
        SecretStr("private-key"),
        Budget(1, Decimal("1")),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    durable = ModelStudioStructuredTransport(
        SecretStr("private-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        with pilot.activate():
            assert (
                helper.call(
                    "generation",
                    "private prompt",
                    KnowledgeProposals(claims=[]),
                    KnowledgeProposals,
                    LIMITS,
                ).claims
                == []
            )
            assert _prepare(durable)() == '{"claims":[]}'
            assert _prepare(durable)() == '{"claims":[]}'
            with pytest.raises(PilotBudgetError, match="PILOT_LIMIT_REACHED"):
                _prepare(durable)
    finally:
        helper.close()
        durable.close()
        pilot.close()
    assert observed == [1, 2, 3]
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["kind"] for event in events[1:]] == [
        "reserved",
        "confirmed",
        "reserved",
        "confirmed",
        "reserved",
        "confirmed",
    ]
    assert all(
        event["input_tokens"] == 100 and event["output_tokens"] == 10
        for event in events
        if event.get("kind") == "confirmed"
    )
    assert all(
        event["charged_upper_usd"] == str(token_cost(role_model("generation"), 100, 10))
        for event in events
        if event.get("kind") == "confirmed"
    )
    assert all(
        isinstance(event["elapsed_seconds"], float) and event["elapsed_seconds"] >= 0
        for event in events
        if event.get("kind") == "confirmed"
    )
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert "private-key" not in path.read_text()
    assert "private prompt" not in path.read_text()
    assert "source_ids" not in path.read_text()


def test_unknown_usage_stops_pilot_and_existing_file_refuses_resume(tmp_path):
    path = tmp_path / "unknown.jsonl"
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _response(request, usage=False)

    pilot = PilotBudget("unknown-pilot", 3, Decimal("1"), path)
    transport = ModelStudioStructuredTransport(
        SecretStr("offline"), base_url=BASE_URL, transport=httpx.MockTransport(handle)
    )
    try:
        with pytest.raises(CallFailed, match="RESPONSE_UNKNOWN"):
            with pilot.activate():
                _prepare(transport)()
        assert pilot.stopped
        with pytest.raises(PilotBudgetError, match="PILOT_STOPPED"):
            with pilot.activate():
                _prepare(transport)()
        pilot.close()
        with pytest.raises(PilotBudgetError, match="PILOT_FILE_UNAVAILABLE"):
            PilotBudget("unknown-pilot", 3, Decimal("1"), path)
    finally:
        transport.close()
        pilot.close()
    assert calls == [1]
    assert [json.loads(line).get("kind") for line in path.read_text().splitlines()] == [
        None,
        "reserved",
    ]


def test_usd_cap_uses_confirmed_cost_plus_next_worst_case(tmp_path):
    estimate = token_cost(FLASH, BILLABLE_INPUT_CEILING, LIMITS.max_output_tokens)
    actual = token_cost(FLASH, 100, 10)
    pilot = PilotBudget(
        "usd-pilot", 3, actual + estimate - Decimal("0.000001"), tmp_path / "usd.jsonl"
    )
    try:
        first = pilot.reserve(FLASH, LIMITS, "0" * 64)
        pilot.confirm(first, FLASH, 100, 10)
        with pytest.raises(PilotBudgetError, match="PILOT_LIMIT_REACHED"):
            pilot.reserve(FLASH, LIMITS, "0" * 64)
        assert pilot.calls == 1
    finally:
        pilot.close()


def test_same_pilot_rejects_overlapping_execution(tmp_path):
    pilot = PilotBudget("sequential", 1, Decimal("1"), tmp_path / "sequential.jsonl")
    try:
        with pilot.activate():
            with pytest.raises(PilotBudgetError, match="PILOT_CONCURRENT_RUN"):
                with pilot.activate():
                    pass
        assert pilot.stopped
    finally:
        pilot.close()


def test_explicit_demo_budget_has_no_cap_and_allows_overlapping_use(tmp_path):
    path = tmp_path / "uncapped.jsonl"
    pilot = PilotBudget("uncapped", None, None, path, demo_uncapped=True)
    helper = Budget(None, None, demo_uncapped=True)
    entered = threading.Barrier(3)
    failures = []

    def use_pilot(sequence):
        try:
            with pilot.activate():
                entered.wait(timeout=2)
                reserved = pilot.reserve(FLASH, LIMITS, f"{sequence:064x}")
                pilot.confirm(reserved, FLASH, 100, 10)
        except BaseException as error:
            failures.append(error)

    threads = [
        threading.Thread(target=use_pilot, args=(sequence,)) for sequence in range(1, 3)
    ]
    try:
        for thread in threads:
            thread.start()
        entered.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)
        for _ in range(3):
            helper.reserve(Decimal("1000000"))
        assert not failures and all(not thread.is_alive() for thread in threads)
        assert pilot.calls == 2 and not pilot.stopped
        assert helper.charged_upper_usd == Decimal("3000000")
        header = json.loads(path.read_text().splitlines()[0])
        assert header["mode"] == "DEMO_UNCAPPED"
        assert header["max_calls"] is None and header["max_usd"] is None
    finally:
        pilot.close()


def test_demo_budget_records_unpriced_provider_calls(tmp_path):
    path = tmp_path / "unpriced.jsonl"
    pilot = PilotBudget("unpriced", None, None, path, demo_uncapped=True)
    try:
        with pilot.activate():
            sequence = pilot.reserve_unpriced_call(
                provider="typesafe",
                model="jev-1.13.0",
                role="claim_review",
                request_sha256="0" * 64,
            )
            pilot.confirm_unpriced_call(
                sequence,
                status="SUCCESS",
                elapsed_seconds=0.25,
                input_tokens=10,
                output_tokens=2,
            )
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert lines[1]["kind"] == "unpriced_reserved"
        assert lines[1]["provider"] == "typesafe"
        event = lines[2]
        assert event["kind"] == "unpriced_confirmed"
        assert event["input_tokens"] == 10
    finally:
        pilot.close()


@pytest.mark.parametrize(
    "max_calls,max_usd,demo_uncapped",
    [(None, None, False), (1, Decimal("1"), True)],
)
def test_demo_budget_requires_explicit_none_caps(
    tmp_path, max_calls, max_usd, demo_uncapped
):
    with pytest.raises(PilotBudgetError, match="INVALID_PILOT_BUDGET"):
        PilotBudget(
            "invalid-demo",
            max_calls,
            max_usd,
            tmp_path / f"{demo_uncapped}.jsonl",
            demo_uncapped=demo_uncapped,
        )
    with pytest.raises(ValueError, match="INVALID_BUDGET"):
        Budget(max_calls, max_usd, demo_uncapped=demo_uncapped)


def test_failed_fsync_blocks_before_http(tmp_path, monkeypatch):
    path = tmp_path / "fsync.jsonl"
    pilot = PilotBudget("fsync-pilot", 1, Decimal("1"), path)
    calls = []
    transport = ModelStudioStructuredTransport(
        SecretStr("offline"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(
            lambda request: calls.append(1) or _response(request)
        ),
    )
    try:
        with pilot.activate():
            monkeypatch.setattr(
                os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("offline"))
            )
            with pytest.raises(PilotBudgetError, match="PILOT_FILE_WRITE_FAILED"):
                _prepare(transport)
        assert pilot.stopped
    finally:
        transport.close()
        pilot.close()
    assert calls == []


def test_dry_run_never_creates_pilot_file(tmp_path):
    assert dry_run()["provider_sends"] == 0
    assert list(tmp_path.iterdir()) == []


def test_budget_refusal_never_writes_a_provider_outcome(monkeypatch):
    monkeypatch.setattr(
        durable_provider,
        "Session",
        lambda _engine: (_ for _ in ()).throw(AssertionError("DB touched")),
    )

    def refused():
        raise PilotBudgetError("PILOT_LIMIT_REACHED")

    with pytest.raises(PilotBudgetError, match="PILOT_LIMIT_REACHED"):
        durable_provider.execute_call(object(), object(), refused)


def test_document_preflight_failure_allows_next_document_send(tmp_path, monkeypatch):
    class FakeSession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def begin(self):
            return self

    path = tmp_path / "two-documents.jsonl"
    pilot = PilotBudget("two-documents", 2, Decimal("1"), path)
    sends = []
    failures = []
    terminals = []

    def handle(request: httpx.Request) -> httpx.Response:
        sends.append(request_digest(request))
        return _response(request)

    transport = ModelStudioStructuredTransport(
        SecretStr("offline"), base_url=BASE_URL, transport=httpx.MockTransport(handle)
    )
    monkeypatch.setattr(app, "_require_ready", lambda *_args: None)
    monkeypatch.setattr(app, "Session", FakeSession)
    monkeypatch.setattr(durable_provider, "Session", FakeSession)
    monkeypatch.setattr(durable_provider.tasks, "require_lease", lambda *_args: None)
    monkeypatch.setattr(
        app.extraction_tasks,
        "enqueue_extraction",
        lambda _session, document_id, _execution: SimpleNamespace(task_id=document_id),
    )
    monkeypatch.setattr(
        durable_provider.tasks,
        "fail_execution",
        lambda _session, lease, *, transient: (
            failures.append((lease, transient)) or "FINAL_FAILED"
        ),
    )
    monkeypatch.setattr(
        durable_provider.tasks,
        "reserve_slot",
        lambda _session, lease: SimpleNamespace(lease=lease),
    )
    monkeypatch.setattr(
        durable_provider.tasks,
        "record_terminal",
        lambda _session, _slot, result: terminals.append(result.outcome) or "RUNNING",
    )

    def fake_extraction(engine, task_id, *_args):
        if task_id == 1:

            def too_large():
                return transport.prepare(
                    model=role_model("generation"),
                    messages=[{"role": "system", "content": "private prompt"}],
                    schema_name="KnowledgeProposals",
                    schema=KnowledgeProposals.model_json_schema(),
                    limits=CallLimits(2_000, 1_024, 1),
                )

            with pytest.raises(CallFailed, match="REQUEST_SIZE_LIMIT"):
                durable_provider.execute_call(engine, task_id, too_large)
            return SimpleNamespace(disposition="FAILED", task_status="FINAL_FAILED")
        result = durable_provider.execute_call(
            engine, task_id, lambda: _prepare(transport)
        )
        assert result.value == '{"claims":[]}'
        return SimpleNamespace(disposition="FAILED", task_status="RUNNING")

    monkeypatch.setattr(app, "run_extraction", fake_extraction)

    def run_document(document_id: int):
        runtime = SimpleNamespace(
            document=SimpleNamespace(document_id=str(document_id)),
            ontology=object(),
            identity_settings=lambda: {"document": document_id},
        )
        execution = SimpleNamespace(
            runtime_settings={"extraction_runner": runtime.identity_settings()}
        )
        return app.run_document(
            object(),
            document_id,
            "worker",
            execution,
            runtime,
            object(),
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args: None,
            pilot=pilot,
        )

    try:
        assert run_document(1).task_status == "FINAL_FAILED"
        assert pilot.calls == 0 and not pilot.stopped and sends == []
        assert run_document(2).task_status == "RUNNING"
    finally:
        transport.close()
        pilot.close()
    assert failures == [(1, False)]
    assert terminals == ["SUCCESS"]
    assert len(sends) == 1 and pilot.calls == 1
    assert [
        json.loads(line).get("request_sha256") for line in path.read_text().splitlines()
    ] == [None, sends[0], None]


@pytest.mark.parametrize("reserve_first", [False, True])
def test_other_preflight_errors_still_stop_pilot(tmp_path, monkeypatch, reserve_first):
    class FakeSession:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def begin(self):
            return self

    pilot = PilotBudget("failed-preflight", 2, Decimal("1"), tmp_path / "failed.jsonl")
    monkeypatch.setattr(durable_provider, "Session", FakeSession)
    monkeypatch.setattr(durable_provider.tasks, "require_lease", lambda *_args: None)
    monkeypatch.setattr(
        durable_provider.tasks,
        "fail_execution",
        lambda *_args, **_kwargs: "FINAL_FAILED",
    )

    def preflight():
        if reserve_first:
            pilot.reserve(FLASH, LIMITS, "0" * 64)
            raise CallFailed("REQUEST_SIZE_LIMIT", fatal=True)
        raise ValueError("unexpected preflight error")

    try:
        with pilot.activate():
            with pytest.raises((CallFailed, ValueError)):
                durable_provider.execute_call(object(), object(), preflight)
        assert pilot.stopped
        assert pilot.calls == int(reserve_first)
    finally:
        pilot.close()


@pytest.mark.parametrize("supplied_transport", [False, True])
def test_live_transport_requires_explicit_pilot(supplied_transport):
    transport = ModelStudioStructuredTransport(
        SecretStr("offline"),
        base_url=BASE_URL,
        transport=httpx.HTTPTransport(retries=0) if supplied_transport else None,
    )
    try:
        with pytest.raises(PilotBudgetError, match="PILOT_BUDGET_REQUIRED"):
            _prepare(transport)
    finally:
        transport.close()
