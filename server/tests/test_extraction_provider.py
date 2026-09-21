import json
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest
from kimi_wire import task_prompt, wire_schema
from pydantic import SecretStr

from ontology_map.db.extraction_tasks import ExecutionInput
from ontology_map.extraction import GENERATION_PROMPT, GenerationInput
from ontology_map.extraction_contracts import (
    KnowledgeProposals,
    Ontology,
    SourceSpan,
    digest,
)
from ontology_map.extraction_provider import (
    CORRECTIVE_INPUT_SEPARATOR,
    ModelStudioGenerationAdapter,
)
from ontology_map.extraction_runner import GenerationRequest
from ontology_map.llm_config import (
    BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    GENERATION_READ_TIMEOUT_SECONDS,
)
from ontology_map.model_studio import FLASH, CallFailed, CallLimits
from ontology_map.pilot_budget import PilotBudget, request_digest


def request(*, max_request_bytes: int = 100_000) -> GenerationRequest:
    quote = "한빛은 AI 제품을 발표했다."
    span = SourceSpan(
        source_id="s0",
        start=0,
        end=len(quote),
        quote=quote,
        quote_hash=digest(quote),
        paragraph_id="p0",
    )
    payload = GenerationInput(
        sources=[span],
        ontology=Ontology(
            node_types=("COMPANY", "TECHNOLOGY"),
            relations=(),
            attributes=(),
            topics=(),
        ),
        max_candidates=8,
    )
    return GenerationRequest(
        payload=payload,
        prompt=GENERATION_PROMPT,
        limits=CallLimits(
            max_input_tokens=2_000,
            max_output_tokens=1_024,
            max_request_bytes=max_request_bytes,
        ),
        execution=ExecutionInput(validator_version="v1", runtime_settings={}),
    )


def response(req: httpx.Request, content: str = '{"claims":[]}') -> httpx.Response:
    return httpx.Response(
        200,
        request=req,
        json={
            "id": "offline",
            "object": "chat.completion",
            "created": 0,
            "model": FLASH,
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


def test_prepare_builds_exact_kimi_request_before_send(tmp_path) -> None:
    calls: list[dict[str, object]] = []
    path = tmp_path / "generation.jsonl"
    pilot = PilotBudget("generation-request", 1, Decimal("1"), path)

    def handle(req: httpx.Request) -> httpx.Response:
        assert req.extensions["timeout"]["read"] == GENERATION_READ_TIMEOUT_SECONDS
        assert req.extensions["timeout"]["connect"] == DEFAULT_TIMEOUT_SECONDS
        reserved = json.loads(path.read_text().splitlines()[-1])
        assert reserved["kind"] == "reserved"
        assert reserved["request_sha256"] == request_digest(req)
        assert str(req.url) == BASE_URL + "/chat/completions"
        payload = json.loads(req.content)
        calls.append(payload)
        assert payload["model"] == FLASH
        assert payload["max_completion_tokens"] == 1_024
        assert "max_tokens" not in payload
        assert not {"tools", "tool_choice", "stream_options"} & payload.keys()
        assert task_prompt(payload) == GENERATION_PROMPT
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        assert json.loads(payload["messages"][1]["content"])["max_candidates"] == 8
        assert wire_schema(payload) == KnowledgeProposals.model_json_schema()
        return response(req)

    adapter = ModelStudioGenerationAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        with pilot.activate():
            send = adapter.prepare(request())
            assert calls == []
            result = send()
    finally:
        adapter.close()
        pilot.close()
    assert isinstance(result, KnowledgeProposals)
    assert result.claims == []
    assert len(calls) == 1


def test_corrective_input_changes_prepared_request_before_send() -> None:
    serialized: list[bytes] = []

    def handle(req: httpx.Request) -> httpx.Response:
        serialized.append(req.content)
        return response(req)

    base = request()
    corrective = "이 재처리에서는 공동 발표의 두 주체를 모두 보존한다."
    corrected = replace(
        base,
        execution=base.execution.model_copy(update={"corrective_input": corrective}),
    )
    adapter = ModelStudioGenerationAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        base_send = adapter.prepare(base)
        corrected_send = adapter.prepare(corrected)
        assert serialized == []
        base_send()
        corrected_send()
    finally:
        adapter.close()
    assert len(serialized) == 2
    assert serialized[0] != serialized[1]
    base_body = json.loads(serialized[0])
    corrected_body = json.loads(serialized[1])
    assert task_prompt(base_body) == GENERATION_PROMPT
    assert task_prompt(corrected_body) == (
        GENERATION_PROMPT + CORRECTIVE_INPUT_SEPARATOR + corrective
    )
    assert wire_schema(base_body) == wire_schema(corrected_body)
    assert base_body["messages"][1] == corrected_body["messages"][1]


def test_deterministic_preflight_failure_sends_nothing() -> None:
    calls = 0

    def handle(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response(req)

    adapter = ModelStudioGenerationAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        with pytest.raises(CallFailed, match="REQUEST_SIZE_LIMIT"):
            adapter.prepare(request(max_request_bytes=16))
    finally:
        adapter.close()
    assert calls == 0


def test_prepared_operation_never_retries_or_reuses_send() -> None:
    calls = 0

    def handle(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response(req)

    adapter = ModelStudioGenerationAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        send = adapter.prepare(request())
        send()
        with pytest.raises(CallFailed, match="UNEXPECTED_RETRY"):
            send()
    finally:
        adapter.close()
    assert calls == 1


def test_read_timeout_is_exposed_once_for_durable_unknown_fencing() -> None:
    calls = 0

    def handle(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("lost reply", request=req)

    adapter = ModelStudioGenerationAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        send = adapter.prepare(request())
        with pytest.raises(httpx.ReadTimeout):
            send()
    finally:
        adapter.close()
    assert calls == 1


@pytest.mark.parametrize("content", ["not-json", '{"claims":[{"candidate_id":"bad"}]}'])
def test_confirmed_malformed_structured_output_is_contract_failure(
    content: str,
) -> None:
    adapter = ModelStudioGenerationAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda req: response(req, content)),
    )
    try:
        send = adapter.prepare(request())
        with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR"):
            send()
    finally:
        adapter.close()
