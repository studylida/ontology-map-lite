import json
from datetime import UTC, datetime

import httpx
import pytest
from kimi_wire import task_prompt, wire_schema
from pydantic import SecretStr

from ontology_map import followup_generation as product
from ontology_map.followup_generation_contracts import (
    FollowupAgentInput,
    FollowupQuestionsProposal,
    FollowupWindowInput,
    PreparedFollowup,
)
from ontology_map.followup_provider import ModelStudioFollowupAdapter
from ontology_map.llm_config import BASE_URL
from ontology_map.model_studio import CallFailed


def prepared() -> PreparedFollowup:
    return PreparedFollowup(
        promotion_batch_id=1,
        node_context_id=2,
        node_search_document_id=3,
        basis_ids=(4,),
        agent_input=FollowupAgentInput(
            node_id=4,
            node_type="COMPANY",
            preferred_alias="가상 노드",
            as_of_at=datetime(2026, 9, 16, tzinfo=UTC),
            recent_90_days=FollowupWindowInput(
                time_window="RECENT_90_DAYS", claims=(), conflict_pairs=()
            ),
            recent_1_year=FollowupWindowInput(
                time_window="RECENT_1_YEAR", claims=(), conflict_pairs=()
            ),
        ),
    )


def response(
    req: httpx.Request,
    content: str = (
        '{"recent_90_days":{"questions":[]},"recent_1_year":{"questions":[]}}'
    ),
) -> httpx.Response:
    return httpx.Response(
        200,
        request=req,
        json={
            "id": "offline-followup",
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


def test_prepare_uses_exact_followup_contract_and_sends_once() -> None:
    calls: list[dict[str, object]] = []
    snapshot = prepared()

    def handle(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        calls.append(payload)
        assert str(req.url) == BASE_URL + "/chat/completions"
        assert payload["model"] == product.MODEL_VERSION
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

    adapter = ModelStudioFollowupAdapter(
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
    assert isinstance(result, FollowupQuestionsProposal)
    assert result.recent_90_days.questions == ()
    assert result.recent_1_year.questions == ()
    assert len(calls) == 1


@pytest.mark.parametrize("content", ["not-json", '{"questions":[{"display_order":1}]}'])
def test_malformed_followup_structured_output_is_contract_error(content: str) -> None:
    adapter = ModelStudioFollowupAdapter(
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

    adapter = ModelStudioFollowupAdapter(
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
