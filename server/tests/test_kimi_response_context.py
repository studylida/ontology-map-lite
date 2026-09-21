"""Actual adapters and identifier scopes; synthetic HTTP and in-memory DB fakes."""

import json
import socket
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from test_followup_provider import prepared as followup_input
from test_insight_provider import prepared as insight_input
from test_node_context_provider import prepared as context_input

from ontology_map import durable_provider
from ontology_map.followup_provider import ModelStudioFollowupAdapter
from ontology_map.insight_provider import ModelStudioInsightAdapter
from ontology_map.kimi_transport import KimiStructuredTransport
from ontology_map.llm_config import BASE_URL, role_model
from ontology_map.llm_contracts import CallFailed, CallLimits
from ontology_map.llm_diagnostics import failure_diagnostic
from ontology_map.node_context_provider import ModelStudioNodeContextAdapter


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_MAP_OPENAI_RESPONSE_DIR", str(tmp_path / "responses"))

    def forbidden(*args, **kwargs):
        pytest.fail("NETWORK_FORBIDDEN")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def response(request, content):
    return httpx.Response(
        200,
        request=request,
        json={
            "model": json.loads(request.content)["model"],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        },
    )


@pytest.mark.parametrize(
    "adapter_type,prepared,role,valid",
    [
        (
            ModelStudioNodeContextAdapter,
            context_input,
            "node_context",
            '{"context_text":"맥락"}',
        ),
        (
            ModelStudioFollowupAdapter,
            followup_input,
            "followup",
            '{"recent_90_days":{"questions":[]},"recent_1_year":{"questions":[]}}',
        ),
        (
            ModelStudioInsightAdapter,
            insight_input,
            "insight",
            '{"recent_90_days":{"report":null},"recent_1_year":{"report":null}}',
        ),
    ],
)
@pytest.mark.parametrize("success", [True, False])
def test_all_derived_adapters_keep_originals_and_schema_failures(
    adapter_type, prepared, role, valid, success, tmp_path
):
    adapter = adapter_type(
        SecretStr("synthetic-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(
            lambda req: response(req, valid if success else "{}")
        ),
    )
    try:
        send = adapter.prepare(prepared())
        if success:
            send()
        else:
            with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR") as caught:
                send()
            details = failure_diagnostic(caught.value)
            assert details["stage"] == "OUTPUT_SCHEMA"
            assert details["response_archive"] == "SAVED"
        meta_path = next((tmp_path / "responses").glob("*/metadata.json"))
        meta = json.loads(meta_path.read_text())
        assert meta["role"] == role
        assert (meta_path.parent / "response.body").is_file()
        if not success:
            assert details["response_id"] == meta["response_id"]
    finally:
        adapter.close()


def test_real_durable_send_boundary_supplies_task_and_slot(monkeypatch, tmp_path):
    class Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def begin(self):
            return self

    monkeypatch.setattr(durable_provider, "Session", Session)
    monkeypatch.setattr(
        durable_provider.tasks,
        "reserve_slot",
        lambda *_args: SimpleNamespace(slot_no=2),
    )
    outcomes = []
    monkeypatch.setattr(
        durable_provider.tasks,
        "record_terminal",
        lambda _session, _slot, result: outcomes.append(result.outcome),
    )
    client = KimiStructuredTransport(
        SecretStr("synthetic-key"),
        transport=httpx.MockTransport(lambda req: response(req, "{}")),
    )
    try:
        send = client.prepare(
            model=role_model("node_context"),
            messages=[{"role": "system", "content": "synthetic"}],
            schema_name="NodeContextProposal",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
            limits=CallLimits(2000, 2048, 10000),
        )
        result = durable_provider._execute_prepared_call(
            object(), SimpleNamespace(task_id=201), send
        )
        assert result.value == "{}" and outcomes == ["SUCCESS"]
        meta_path = next((tmp_path / "responses").glob("*/metadata.json"))
        meta = json.loads(meta_path.read_text())
        assert meta["model_task_id"] == 201 and meta["provider_slot_no"] == 2
    finally:
        client.close()
