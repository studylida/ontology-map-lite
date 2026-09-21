"""No-network regression for the permanent Kimi boundary, not a live quality gate."""

import json
import logging
import socket
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

import httpx
import pytest
from kimi_wire import wire_schema
from openai_cases import CASES
from pydantic import SecretStr, ValidationError

from ontology_map.extraction_contracts import (
    KnowledgeProposals,
    Mention,
    SourceDocument,
    SourceSpan,
    digest,
)
from ontology_map.extraction_provider import KimiGenerationAdapter
from ontology_map.kimi_transport import KimiStructuredTransport
from ontology_map.llm_config import (
    BASE_URL,
    BILLABLE_INPUT_CEILING,
    CONTEXT_CAP_TOKENS,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL_VERSION,
    json_messages,
    request_identity_settings,
    role_model,
)
from ontology_map.model_studio import (
    FLASH,
    PLUS,
    Budget,
    CallFailed,
    CallLimits,
    KimiModels,
    ModelStudio,
    token_cost,
)
from ontology_map.pilot_budget import PilotBudget, PilotBudgetError, request_digest
from ontology_map.structured_provider import ModelStudioStructuredTransport

LIMITS = CallLimits(2000, 1024, 100_000)
PRIVATE_KEY = "offline-private-key"
PRIVATE_TEXT = "공개 로그에 남기면 안 되는 시험 원문"


def reply(request, content='{"claims":[]}', **changes):
    data = {
        "model": json.loads(request.content)["model"],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    data.update(changes)
    return httpx.Response(200, request=request, json=data)


def prepare(transport, **changes):
    values = {
        "model": role_model("generation"),
        "messages": [
            {"role": "system", "content": "Return the selected sources."},
            {"role": "user", "content": PRIVATE_TEXT},
        ],
        "schema_name": "KnowledgeProposals",
        "schema": KnowledgeProposals.model_json_schema(),
        "limits": LIMITS,
    }
    values.update(changes)
    return transport.prepare(**values)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.mark.parametrize(
    "role",
    tuple(CASES),
)
def test_every_helper_role_uses_openai_and_local_validation(role, caplog):
    calls = []
    schema, value = CASES[role]

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        assert str(request.url) == BASE_URL + "/chat/completions"
        assert body["model"] == role_model(role)
        assert body["max_completion_tokens"] == 1024
        assert wire_schema(body) == schema.model_json_schema()
        assert json.loads(body["messages"][1]["content"]) == {"claims": []}
        return reply(request, json.dumps(value))

    budget = Budget(1, Decimal("1"))
    client = KimiModels(
        SecretStr(PRIVATE_KEY), budget, transport=httpx.MockTransport(handle)
    )
    try:
        result = client.call(
            role, "prompt", KnowledgeProposals(claims=[]), schema, LIMITS
        )
        assert result.model_dump(mode="json") == value
    finally:
        client.close()
    assert len(calls) == 1
    assert budget.records[0].model == role_model(role)
    assert budget.records[0].role == role
    assert budget.records[0].request_hash == request_digest(calls[0])
    assert budget.charged_upper_usd == token_cost(role_model(role), 100, 20)
    assert PRIVATE_KEY not in caplog.text
    assert PRIVATE_TEXT not in caplog.text


def test_compatibility_names_are_kimi_only():
    assert FLASH == PLUS == MODEL_VERSION
    assert ModelStudio is KimiModels
    assert ModelStudioStructuredTransport is KimiStructuredTransport


@pytest.mark.parametrize(
    "url",
    [
        "http://api.moonshot.ai/v1",
        "https://api.moonshot.cn/v1",
        "https://api.moonshot.ai.attacker.example/v1",
        "https://user@api.moonshot.ai/v1",
        BASE_URL + "?redirect=evil",
        BASE_URL + "/",
        "https://ws-old.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_no_alternate_endpoint_or_key_redirect(url):
    with pytest.raises(CallFailed, match="UNAPPROVED_ENDPOINT"):
        KimiStructuredTransport(SecretStr(PRIVATE_KEY), base_url=url)


@pytest.mark.parametrize("key", ["", " ", "invalid\nkey"])
def test_credentials_fail_locally(key):
    with pytest.raises(CallFailed, match="AUTHENTICATION_ERROR"):
        KimiStructuredTransport(SecretStr(key))


def test_options_and_schema_attachment_are_immutable_and_versioned():
    settings = request_identity_settings()
    baseline = deepcopy(settings)
    settings["role_profiles"]["generation"]["reasoning_effort"] = "high"
    assert request_identity_settings() == baseline
    assert baseline["provider"] == "openai"
    assert baseline["model"] == MODEL_VERSION
    assert baseline["response_format"] == "json_schema"
    schema = KnowledgeProposals.model_json_schema()
    original = deepcopy(schema)
    messages = [
        {"role": "system", "content": "task"},
        {"role": "user", "content": PRIVATE_TEXT},
    ]
    unchanged = deepcopy(messages)
    first = json_messages(messages, "KnowledgeProposals", schema)
    assert json_messages(messages, "KnowledgeProposals", schema) == first
    assert schema == original and messages == unchanged
    assert first[1] == messages[1]
    assert (
        sha256(json.dumps(baseline, sort_keys=True).encode()).digest()
        != sha256(json.dumps(settings, sort_keys=True).encode()).digest()
    )


def test_limits_fit_the_combined_context():
    assert MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS == CONTEXT_CAP_TOKENS
    assert BILLABLE_INPUT_CEILING >= CONTEXT_CAP_TOKENS
    with pytest.raises(ValueError, match="INVALID_INPUT_LIMIT"):
        CallLimits(MAX_INPUT_TOKENS + 1, 1, 1)
    with pytest.raises(ValueError, match="INVALID_INPUT_LIMIT"):
        CallLimits(True, 1, 1)
    with pytest.raises(ValueError, match="INVALID_REQUEST_LIMIT"):
        CallLimits(1, MAX_OUTPUT_TOKENS + 1, 1)


@pytest.mark.parametrize("kind", ["model", "bytes", "schema", "messages"])
def test_preflight_failure_reserves_and_sends_nothing(kind, tmp_path):
    calls = []
    client = KimiStructuredTransport(
        SecretStr(PRIVATE_KEY),
        transport=httpx.MockTransport(lambda req: calls.append(req) or reply(req)),
    )
    pilot = PilotBudget("preflight", 1, Decimal("1"), tmp_path / "pilot.jsonl")
    changes = {
        "model": {"model": "qwen3.7-flash-2026-07-15"},
        "bytes": {"limits": CallLimits(2000, 1024, 1)},
        "schema": {"schema": {"type": "array"}},
        "messages": {"messages": [{"role": "assistant", "content": "no"}]},
    }[kind]
    try:
        with pilot.activate(), pytest.raises(CallFailed):
            prepare(client, **changes)
        assert pilot.calls == 0
        assert calls == []
    finally:
        client.close()
        pilot.close()


def test_preparation_reserves_once_and_send_cannot_replay(tmp_path, caplog):
    calls = []
    path = tmp_path / "pilot.jsonl"
    pilot = PilotBudget("one-send", 1, Decimal("1"), path)

    def handle(request):
        calls.append(request)
        reserved = json.loads(path.read_text().splitlines()[-1])
        assert reserved["kind"] == "reserved"
        assert reserved["request_sha256"] == request_digest(request)
        return reply(request)

    client = KimiStructuredTransport(
        SecretStr(PRIVATE_KEY), transport=httpx.MockTransport(handle)
    )
    try:
        with pilot.activate():
            operation = prepare(client)
            assert not calls and pilot.calls == 1
            assert json.loads(operation()) == {"claims": []}
            with pytest.raises(CallFailed, match="UNEXPECTED_RETRY"):
                operation()
        assert len(calls) == 1
        assert operation.usage == (100, 20)
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert [event.get("kind") for event in events] == [
            None,
            "reserved",
            "confirmed",
        ]
        assert pilot.charged_upper_usd == token_cost(role_model("generation"), 100, 20)
        assert path.stat().st_mode & 0o777 == 0o600
        for hidden in (PRIVATE_KEY, PRIVATE_TEXT):
            assert hidden not in path.read_text() + caplog.text + repr(operation)
    finally:
        client.close()
        pilot.close()


def test_real_transport_needs_explicit_pilot_before_network():
    client = KimiStructuredTransport(SecretStr(PRIVATE_KEY))
    try:
        with pytest.raises(PilotBudgetError, match="PILOT_BUDGET_REQUIRED"):
            prepare(client)
    finally:
        client.close()


@pytest.mark.parametrize("status", [307, 400, 401, 429, 500])
def test_http_errors_do_not_retry_or_follow_redirects(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"Location": "https://attacker.example"}, request=request
        )

    client = KimiStructuredTransport(
        SecretStr(PRIVATE_KEY), transport=httpx.MockTransport(handle)
    )
    try:
        operation = prepare(client)
        with pytest.raises(httpx.HTTPStatusError):
            operation()
        with pytest.raises(CallFailed, match="UNEXPECTED_RETRY"):
            operation()
    finally:
        client.close()
    assert len(calls) == 1


def test_timeout_remains_typed_for_durable_unknown_fencing():
    def handle(request):
        raise httpx.ReadTimeout("private text", request=request)

    client = KimiStructuredTransport(
        SecretStr(PRIVATE_KEY), transport=httpx.MockTransport(handle)
    )
    try:
        operation = prepare(client)
        with pytest.raises(httpx.ReadTimeout):
            operation()
        assert operation.usage is None and operation.sent
    finally:
        client.close()


@pytest.mark.parametrize(
    "bad_usage",
    [
        None,
        {},
        {"prompt_tokens": True, "completion_tokens": 20, "total_tokens": 21},
        {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 121},
        {"prompt_tokens": 2001, "completion_tokens": 20, "total_tokens": 2021},
    ],
)
def test_unconfirmed_usage_keeps_reservation_and_stops(bad_usage, tmp_path):
    client = KimiStructuredTransport(
        SecretStr(PRIVATE_KEY),
        transport=httpx.MockTransport(lambda req: reply(req, usage=bad_usage)),
    )
    pilot = PilotBudget("unknown-usage", 1, Decimal("1"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate():
            operation = prepare(client)
            with pytest.raises(CallFailed, match="RESPONSE_UNKNOWN"):
                operation()
        assert pilot.stopped and operation.usage is None
        assert pilot.charged_upper_usd == token_cost(
            role_model("generation"), BILLABLE_INPUT_CEILING, 1024
        )
    finally:
        client.close()
        pilot.close()


@pytest.mark.parametrize("finish", ["length", "tool_calls", "content_filter"])
def test_confirmed_truncation_is_failure_not_success_but_usage_is_counted(finish):
    client = KimiStructuredTransport(
        SecretStr(PRIVATE_KEY),
        transport=httpx.MockTransport(
            lambda req: reply(
                req,
                choices=[
                    {
                        "finish_reason": finish,
                        "message": {"content": '{"claims":[]}'},
                    }
                ],
            )
        ),
    )
    try:
        operation = prepare(client)
        with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR"):
            operation()
        assert operation.usage == (100, 20)
    finally:
        client.close()


@pytest.mark.parametrize(
    "content", ["not JSON", '{"claims":[123]}', '{"claims":[],"extra":1}']
)
def test_json_mode_does_not_relax_helper_contract(content):
    client = KimiModels(
        SecretStr(PRIVATE_KEY),
        Budget(1, Decimal("1")),
        transport=httpx.MockTransport(lambda req: reply(req, content)),
    )
    try:
        with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR"):
            client.call(
                "generation",
                "prompt",
                KnowledgeProposals(claims=[]),
                KnowledgeProposals,
                LIMITS,
            )
        assert client.budget.records[0].status == "OUTPUT_CONTRACT_ERROR"
    finally:
        client.close()


@pytest.mark.parametrize("topic_name", [None, "incorrect topic", ""])
def test_generation_preserves_mention_validator(topic_name):
    mention = {
        "mention_id": "m1",
        "text": "한빛",
        "node_type": "COMPANY",
        "source_ids": ["s1"],
        "topic_name": topic_name,
    }
    output = {
        "claims": [
            {
                "candidate_id": "c1",
                "statement": "한빛이 발표했다.",
                "modality": "FACT",
                "source_ids": ["s1"],
                "mentions": [mention],
                "bindings": [],
            }
        ]
    }
    adapter = KimiGenerationAdapter(
        SecretStr(PRIVATE_KEY),
        transport=httpx.MockTransport(lambda req: reply(req, json.dumps(output))),
    )
    # Only this adapter boundary is under test; no task/DB executor is simulated.
    request = SimpleNamespace(
        model=MODEL_VERSION,
        output_schema=KnowledgeProposals,
        prompt="generation",
        payload=KnowledgeProposals(claims=[]),
        limits=LIMITS,
        execution=SimpleNamespace(corrective_input="Preserve every actor."),
    )
    try:
        send = adapter.prepare(request)
        if topic_name is None:
            assert send().claims[0].mentions[0].topic_name is None
        else:
            with pytest.raises(CallFailed, match="OUTPUT_CONTRACT_ERROR"):
                send()
    finally:
        adapter.close()


def test_topic_null_and_source_hash_guards_are_unchanged():
    with pytest.raises(ValidationError):
        Mention(
            mention_id="m1",
            text="AI",
            node_type="TOPIC",
            source_ids=["s1"],
            topic_name=None,
        )
    text = "원문 😀"
    span = SourceSpan(
        source_id="s1",
        start=0,
        end=len(text),
        quote=text,
        quote_hash=digest(text),
        paragraph_id=None,
    )
    source = SourceDocument(
        document_id="1", body=text, body_hash=digest(text), sources=(span,)
    )
    wrong = source.model_dump(mode="json")
    wrong["sources"][0]["quote_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="SOURCE_QUOTE_HASH"):
        SourceDocument.model_validate_json(json.dumps(wrong), strict=True)


def test_debug_guard_sends_nothing():
    logger = logging.getLogger("httpx")
    previous = logger.level
    client = KimiStructuredTransport(
        SecretStr(PRIVATE_KEY), transport=httpx.MockTransport(reply)
    )
    try:
        logger.setLevel(logging.DEBUG)
        with pytest.raises(CallFailed, match="UNSAFE_LOGGING_CONFIGURATION"):
            prepare(client)
    finally:
        logger.setLevel(previous)
        client.close()


def test_helper_local_preflight_failure_does_not_consume_or_poison_next_call(tmp_path):
    calls = []
    client = KimiModels(
        SecretStr(PRIVATE_KEY),
        Budget(1, Decimal("1")),
        transport=httpx.MockTransport(lambda req: calls.append(req) or reply(req)),
    )
    pilot = PilotBudget("helper-preflight", 1, Decimal("1"), tmp_path / "pilot.jsonl")
    try:
        with pilot.activate():
            with pytest.raises(CallFailed, match="REQUEST_SIZE_LIMIT"):
                client.call(
                    "generation",
                    "prompt",
                    KnowledgeProposals(claims=[]),
                    KnowledgeProposals,
                    CallLimits(2000, 1024, 1),
                )
            assert not client.budget.stopped
            assert client.budget.charged_upper_usd == 0
            assert pilot.calls == 0 and not calls
            assert (
                client.call(
                    "generation",
                    "prompt",
                    KnowledgeProposals(claims=[]),
                    KnowledgeProposals,
                    LIMITS,
                ).claims
                == []
            )
        assert len(calls) == 1 and pilot.calls == 1
    finally:
        client.close()
        pilot.close()
