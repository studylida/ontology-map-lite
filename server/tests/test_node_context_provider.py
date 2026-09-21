import json
from hashlib import sha256

import httpx
import pytest
from kimi_wire import task_prompt, wire_schema
from pydantic import SecretStr

from ontology_map import node_context_execution
from ontology_map import node_context_generation as product
from ontology_map.llm_config import BASE_URL
from ontology_map.model_studio import CallFailed, CallLimits
from ontology_map.node_context_generation_contracts import (
    NodeContextAgentInput,
    NodeContextProposal,
    PreparedNodeContext,
)
from ontology_map.node_context_provider import ModelStudioNodeContextAdapter
from ontology_map.structured_provider import request_identity_settings


def prepared() -> PreparedNodeContext:
    return PreparedNodeContext(
        promotion_batch_id=1,
        node_search_document_id=2,
        search_document_input_hash=sha256(b"search").digest(),
        input_hash=sha256(b"context").digest(),
        agent_input=NodeContextAgentInput(
            node_id=3,
            node_type="COMPANY",
            preferred_alias="가상 노드",
            identity_text="가상 노드",
            knowledge_text="유형: 기업",
            basis_ids=(3,),
        ),
    )


def response(
    req: httpx.Request,
    content: str = '{"context_text":"짧은 공개 맥락입니다."}',
) -> httpx.Response:
    return httpx.Response(
        200,
        request=req,
        json={
            "id": "offline-node-context",
            "object": "chat.completion",
            "created": 0,
            "model": product.MODEL_VERSION,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
            },
        },
    )


def test_prepare_uses_exact_node_context_contract_and_sends_once() -> None:
    calls: list[dict[str, object]] = []
    snapshot = prepared()

    def handle(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        calls.append(payload)
        assert str(req.url) == BASE_URL + "/chat/completions"
        assert payload["model"] == product.MODEL_VERSION
        settings = node_context_execution.identity_settings()
        structured = settings["structured_request"]
        limits = settings["limits"]
        assert structured == request_identity_settings("node_context")
        for key, value in structured["role_profiles"]["node_context"].items():
            assert payload[key] == value
        assert structured["local_schema_strict"] is True
        assert payload["max_completion_tokens"] == limits["max_output_tokens"]
        assert not {"tools", "tool_choice", "stream_options"} & payload.keys()
        expected_messages = [
            {
                "role": "system" if role == "system" else "user",
                "content": content,
            }
            for role, content in product.build_messages(snapshot)
        ]
        assert task_prompt(payload) == expected_messages[0]["content"]
        assert payload["messages"][1:] == expected_messages[1:]
        assert wire_schema(payload) == product.output_schema()
        return response(req)

    adapter = ModelStudioNodeContextAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        send = adapter.prepare(snapshot)
        assert calls == []
        result = send()
        with pytest.raises(CallFailed, match="UNEXPECTED_RETRY"):
            send()
    finally:
        adapter.close()
    assert isinstance(result, NodeContextProposal)
    assert result.context_text == "짧은 공개 맥락입니다."
    assert len(calls) == 1


def test_provider_and_identity_share_mutated_execution_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = node_context_execution.NODE_CONTEXT_LIMITS
    changed = CallLimits(
        max_input_tokens=original.max_input_tokens,
        max_output_tokens=original.max_output_tokens - 1,
        max_request_bytes=original.max_request_bytes,
    )
    monkeypatch.setattr(node_context_execution, "NODE_CONTEXT_LIMITS", changed)
    seen: list[dict[str, object]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        seen.append(payload)
        return response(req)

    settings = node_context_execution.identity_settings()
    assert settings["limits"] == {
        "max_input_tokens": changed.max_input_tokens,
        "max_output_tokens": changed.max_output_tokens,
        "max_request_bytes": changed.max_request_bytes,
    }
    assert settings["structured_request"] == request_identity_settings("node_context")
    adapter = ModelStudioNodeContextAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        adapter.prepare(prepared())()
    finally:
        adapter.close()
    assert seen[0]["max_completion_tokens"] == changed.max_output_tokens
    assert "max_tokens" not in seen[0]


@pytest.mark.parametrize(
    "content", ["not-json", '{"context_text":""}', '{"context_text":"ok","extra":1}']
)
def test_malformed_context_structured_output_is_contract_error(content: str) -> None:
    adapter = ModelStudioNodeContextAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda req: response(req, content)),
    )
    try:
        send = adapter.prepare(prepared())
        with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR"):
            send()
    finally:
        adapter.close()


def test_read_timeout_is_exposed_without_hidden_retry() -> None:
    calls = 0

    def handle(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("lost reply", request=req)

    adapter = ModelStudioNodeContextAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        send = adapter.prepare(prepared())
        with pytest.raises(httpx.ReadTimeout):
            send()
    finally:
        adapter.close()
    assert calls == 1
