import importlib.util
import json
import runpy
import socket
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from kimi_wire import wire_schema
from pydantic import SecretStr, ValidationError

from ontology_map.extraction import (
    ExtractionLimits,
    ExtractionResult,
    _binding_valid,
    extract_knowledge,
)
from ontology_map.extraction_contracts import (
    AttributeProposal,
    AttributeRule,
    BodySelection,
    ClaimProposal,
    KnowledgeProposals,
    Ontology,
    PeriodValue,
    RelationRule,
    SourceDocument,
    SourceSpan,
    TemporalPoint,
    digest,
)
from ontology_map.extraction_metrics import CandidateReview, summarize
from ontology_map.llm_config import BASE_URL, SCHEMA_ROLES, role_model
from ontology_map.model_studio import (
    FLASH,
    Budget,
    CallFailed,
    CallLimits,
    ModelStudio,
    validate_base_url,
)


def source_document():
    quotes = ["한빛과 푸른은 공동 개발할 계획이다. 😀", "한빛은 AI 제품을 발표했다."]
    body = "\n".join(quotes)
    spans = []
    start = 0
    for i, quote in enumerate(quotes):
        spans.append(
            SourceSpan(
                source_id=f"s{i}",
                start=start,
                end=start + len(quote),
                quote=quote,
                quote_hash=digest(quote),
                paragraph_id=f"p{i}",
            )
        )
        start += len(quote) + 1
    return SourceDocument(
        document_id="d1", body=body, body_hash=digest(body), sources=tuple(spans)
    )


def ontology():
    # Synthetic runtime input, not an approved product ontology or DB fixture.
    return Ontology(
        node_types=("COMPANY", "TOPIC"),
        topics=(),
        attributes=(),
        relations=(
            RelationRule(
                code="COLLABORATES_WITH",
                version_no=1,
                revision_id=1,
                description="두 대상의 공동 행위",
                direction="SYMMETRIC",
                endpoints=(("COMPANY", "COMPANY"),),
            ),
        ),
    )


def candidate(candidate_id="c1", source="s0"):
    return {
        "candidate_id": candidate_id,
        "statement": "한빛과 푸른은 공동 개발할 계획이다.",
        "modality": "PLAN_OR_TARGET",
        "source_ids": [source],
        "mentions": [
            {
                "mention_id": "m1",
                "text": "한빛",
                "node_type": "COMPANY",
                "source_ids": [source],
                "topic_name": None,
            },
            {
                "mention_id": "m2",
                "text": "푸른",
                "node_type": "COMPANY",
                "source_ids": [source],
                "topic_name": None,
            },
        ],
        "bindings": [
            {
                "kind": "RELATION",
                "binding_id": "r1",
                "code": "COLLABORATES_WITH",
                "source_mention": "m1",
                "target_mention": "m2",
                "stance": "SUPPORT",
            }
        ],
    }


def provider_response(request, content, usage=True, finish="stop"):
    return httpx.Response(
        200,
        json={
            "id": "offline",
            "object": "chat.completion",
            "created": 0,
            "model": json.loads(request.content)["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content},
                }
            ],
            **(
                {
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    }
                }
                if usage
                else {}
            ),
        },
    )


def limits():
    call = CallLimits(
        max_input_tokens=2000, max_output_tokens=1024, max_request_bytes=100_000
    )
    return ExtractionLimits(body=call, generation=call, judgment=call, max_candidates=8)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append("unexpected network or tracing")
        raise AssertionError("NETWORK_IS_FORBIDDEN")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    if importlib.util.find_spec("langsmith") is not None:
        from langsmith import Client

        monkeypatch.setattr(Client, "request_with_retries", forbidden)
    yield
    assert not attempts


def test_fixed_pipeline_request_contract_and_own_evidence(monkeypatch, caplog, capsys):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    calls = []
    doc = source_document()

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url) == BASE_URL + "/chat/completions"
        assert payload["max_completion_tokens"] == 1024
        assert "max_tokens" not in payload
        assert not {"tools", "tool_choice", "stream_options"} & payload.keys()
        schema = wire_schema(payload)
        assert schema["additionalProperties"] is False
        name = schema["title"]
        data = json.loads(payload["messages"][1]["content"])
        if name == "KnowledgeProposals":
            assert payload["model"] == FLASH
            assert [s["source_id"] for s in data["sources"]] == ["s0", "s1"]
            answer = {"claims": [candidate(), candidate("c2")]}
        else:
            assert name == "ClaimReviewBatch"
            assert payload["model"] == role_model(SCHEMA_ROLES[name])
            assert len(data["candidates"]) == 1
            item = data["candidates"][0]
            assert [s["source_id"] for s in item["evidence"]] == ["s0"]
            assert doc.sources[1].quote not in payload["messages"][1]["content"]
            assert "gold" not in data
            answer = {
                "claims": [
                    {
                        "candidate_id": item["claim"]["candidate_id"],
                        "support_verdict": "TRUE",
                        "publication_verdict": "PUBLISH",
                        "bindings": [
                            {"binding_id": item["binding_id"], "verdict": "TRUE"}
                            for item in item["retained_bindings"]
                        ],
                    }
                ]
            }
        return provider_response(request, json.dumps(answer, ensure_ascii=False))

    budget = Budget(max_calls=10, max_usd=Decimal("3"))
    client = ModelStudio(
        SecretStr("offline-test-key"),
        budget,
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        result = extract_knowledge(
            doc, ontology(), client, limits(), include_structure=True
        )
    finally:
        client.close()
    assert result.status == "SUCCESS", result
    assert [c.candidate_id for c in result.verified] == ["c1"]
    assert result.duplicates == {"c2": "c1"}
    assert len(result.verified[0].mentions) == 2
    assert [r.role for r in budget.records] == [
        "generation",
        "claim_review",
    ]
    assert len(calls) == 2
    assert budget.charged_upper_usd == sum(
        record.charged_upper_usd for record in budget.records
    )
    assert "offline-test-key" not in caplog.text
    assert doc.body not in caplog.text + capsys.readouterr().out


@pytest.mark.parametrize(
    "field,value", [("body_hash", "0" * 64), ("body", "다른 본문")]
)
def test_source_integrity(field, value):
    data = source_document().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        SourceDocument.model_validate(data)


@pytest.mark.parametrize(
    "change",
    [
        "unknown_source",
        "unresolved",
        "bad_endpoint",
        "no_ontology",
        "lost_joint_actor",
    ],
)
def test_bad_candidates_do_not_remove_independent_valid_knowledge(change):
    bad, good = candidate("bad"), candidate("good")
    if change == "unknown_source":
        bad["source_ids"] = ["absent"]
    if change == "bad_endpoint":
        bad["bindings"][0]["target_mention"] = "absent"
    if change == "no_ontology":
        bad["bindings"] = []
    if change == "lost_joint_actor":
        bad["mentions"].append(
            {
                "mention_id": "topic",
                "text": "미승인 주제",
                "node_type": "TOPIC",
                "source_ids": ["s0"],
                "topic_name": "미승인 주제",
            }
        )
        bad["bindings"].append(
            {**bad["bindings"][0], "binding_id": "r2", "target_mention": "topic"}
        )
    good["statement"] += " 두 회사가 함께 추진한다."

    def handle(request):
        payload = json.loads(request.content)
        name = wire_schema(payload)["title"]
        data = json.loads(payload["messages"][1]["content"])
        if name == "KnowledgeProposals":
            answer = {"claims": [bad, good]}
        else:
            answer = {
                "claims": [
                    {
                        "candidate_id": item["claim"]["candidate_id"],
                        "support_verdict": (
                            "UNRESOLVED"
                            if item["claim"]["candidate_id"] == "bad"
                            and change == "unresolved"
                            else "TRUE"
                        ),
                        "publication_verdict": "PUBLISH",
                        "bindings": [
                            {
                                "binding_id": binding["binding_id"],
                                "verdict": (
                                    "FALSE"
                                    if item["claim"]["candidate_id"] == "bad"
                                    else "TRUE"
                                ),
                            }
                            for binding in item["retained_bindings"]
                        ],
                    }
                    for item in data["candidates"]
                ]
            }
        return provider_response(request, json.dumps(answer, ensure_ascii=False))

    client = ModelStudio(
        SecretStr("offline"),
        Budget(10, Decimal("3")),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        result = extract_knowledge(
            source_document(), ontology(), client, limits(), include_structure=False
        )
    finally:
        client.close()
    assert [c.candidate_id for c in result.verified] == ["good"]
    assert any(e.candidate_id == "bad" for e in result.exclusions)


def test_claim_review_keeps_each_supported_binding():
    proposal = candidate()
    proposal["bindings"].append({**proposal["bindings"][0], "binding_id": "r2"})

    def handle(request):
        payload = json.loads(request.content)
        name = wire_schema(payload)["title"]
        if name == "KnowledgeProposals":
            answer = {"claims": [proposal]}
        else:
            answer = {
                "claims": [
                    {
                        "candidate_id": "c1",
                        "support_verdict": "TRUE",
                        "publication_verdict": "PUBLISH",
                        "bindings": [
                            {"binding_id": "r1", "verdict": "TRUE"},
                            {"binding_id": "r2", "verdict": "FALSE"},
                        ],
                    }
                ]
            }
        return provider_response(request, json.dumps(answer, ensure_ascii=False))

    client = ModelStudio(
        SecretStr("offline"),
        Budget(8, Decimal("3")),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        result = extract_knowledge(
            source_document(), ontology(), client, limits(), include_structure=True
        )
    finally:
        client.close()
    assert [binding.binding_id for binding in result.verified[0].bindings] == ["r1"]
    assert result.binding_outcomes["c1"] == {
        "r1": "TRUE",
        "r2": "FALSE",
    }


@pytest.mark.parametrize("failure", ["http", "usage", "schema", "truncated"])
def test_no_retry_and_uncertain_cost_is_reserved(failure):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if failure == "http":
            return httpx.Response(
                503, json={"error": {"message": "private provider body"}}
            )
        content = '{"claims": []}' if failure != "schema" else '{"claims": [123]}'
        return provider_response(
            request,
            content,
            usage=failure != "usage",
            finish="length" if failure == "truncated" else "stop",
        )

    budget = Budget(2, Decimal("2"))
    client = ModelStudio(
        SecretStr("offline"),
        budget,
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        with pytest.raises(CallFailed) as error:
            client.call(
                "generation",
                "prompt",
                KnowledgeProposals(claims=[]),
                KnowledgeProposals,
                limits().generation,
            )
        assert "private provider body" not in str(error.value)
        assert calls == 1 and len(budget.records) == 1
        if failure in ("http", "usage"):
            assert budget.stopped
            assert budget.charged_upper_usd == budget.records[0].reserved_usd
        else:
            # Direct HTTP exposes confirmed usage even when output was truncated.
            assert error.value.code == "OUTPUT_CONTRACT_ERROR"
            assert budget.charged_upper_usd < budget.records[0].reserved_usd
    finally:
        client.close()


def test_zero_budget_blocks_before_network():
    def forbidden(request):
        pytest.fail("A zero budget must not reach even the fake provider")

    client = ModelStudio(
        SecretStr("offline"),
        Budget(0, Decimal(0)),
        base_url=BASE_URL,
        transport=httpx.MockTransport(forbidden),
    )
    try:
        with pytest.raises(CallFailed, match="CALL_LIMIT"):
            client.call(
                "generation",
                "prompt",
                KnowledgeProposals(claims=[]),
                KnowledgeProposals,
                limits().generation,
            )
    finally:
        client.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("start", 1),
        ("end", 999),
        ("quote", "틀린 인용"),
        ("quote_hash", "0" * 64),
        ("source_id", "s1"),
    ],
)
def test_span_offsets_quotes_hashes_and_ids(field, value):
    data = source_document().model_dump()
    data["sources"][0][field] = value
    with pytest.raises(ValidationError):
        SourceDocument.model_validate(data)


def test_metrics_preserve_missing_ontology_and_error_denominators():
    good = ClaimProposal.model_validate(candidate("good"))
    unsupported = ClaimProposal.model_validate(
        {**candidate("unsupported"), "bindings": []}
    )
    wrong = ClaimProposal.model_validate(candidate("wrong"))
    result = ExtractionResult(
        generated=[good, unsupported, wrong],
        verified=[good, wrong],
        support_verdicts={"good": "TRUE", "unsupported": "TRUE", "wrong": "TRUE"},
    )
    reviews = [
        CandidateReview("good", True, True, frozenset({"f1"}), "g1", frozenset()),
        CandidateReview(
            "unsupported", True, True, frozenset({"f2"}), "g2", frozenset()
        ),
        CandidateReview(
            "wrong", False, False, frozenset(), "g3", frozenset({"ACTOR", "PLAN"})
        ),
    ]
    score = summarize(
        result, frozenset({"f1", "f2"}), reviews, final_reviews=[reviews[0], reviews[2]]
    )
    assert score["generated_retention"] == 1
    assert score["final_retention"] == 0.5
    assert score["final_nonduplicate_claims"] == 2
    assert score["final_critical_claims"] == 1
    assert score["final_critical_error_rate"] == 0.5
    assert score["support_false_accept_rate"] == 1
    with pytest.raises(ValueError, match="REVIEW_COVERAGE"):
        summarize(result, frozenset({"f1", "f2"}), reviews, final_reviews=[])
    empty = summarize(ExtractionResult(), frozenset({"f1", "f2"}), [], final_reviews=[])
    assert empty["empty_final"] is True
    assert empty["final_retention"] == 0
    assert empty["final_critical_error_rate"] is None


def test_exact_reprocessing_cache_uses_contract_and_document_identity():
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return provider_response(request, '{"claims": []}')

    client = ModelStudio(
        SecretStr("offline"),
        Budget(3, Decimal("2")),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    completed = {}
    document = source_document()
    try:
        for _ in range(2):
            result = extract_knowledge(
                document,
                ontology(),
                client,
                limits(),
                include_structure=True,
                completed=completed,
            )
            assert result.status == "SUCCESS"
        assert calls == 1
        changed = document.model_copy(update={"document_id": "another-document"})
        extract_knowledge(
            changed,
            ontology(),
            client,
            limits(),
            include_structure=True,
            completed=completed,
        )
        assert calls == 2
    finally:
        client.close()


@pytest.mark.parametrize(
    "boundary", ["request_bytes", "cost", "input_tokens", "output_tokens"]
)
def test_request_and_token_cost_limits(boundary):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return provider_response(request, '{"claims": []}')

    budget = Budget(2, Decimal("0.01") if boundary == "cost" else Decimal("2"))
    call_limit = CallLimits(
        max_input_tokens=50 if boundary == "input_tokens" else 2000,
        max_output_tokens=10 if boundary == "output_tokens" else 1024,
        max_request_bytes=1 if boundary == "request_bytes" else 100_000,
    )
    client = ModelStudio(
        SecretStr("offline"),
        budget,
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        with pytest.raises(CallFailed):
            client.call(
                "generation",
                "prompt",
                KnowledgeProposals(claims=[]),
                KnowledgeProposals,
                call_limit,
            )
        if boundary in ("request_bytes", "cost"):
            assert calls == 0 and budget.charged_upper_usd == 0
        else:
            assert calls == 1 and budget.stopped
            assert budget.charged_upper_usd == budget.records[0].reserved_usd
    finally:
        client.close()


def test_attribute_units_types_and_precision_aware_time_bounds():
    rules = ontology().model_copy(
        update={
            "attributes": (
                AttributeRule(
                    code="COUNT",
                    version_no=1,
                    revision_id=2,
                    description="개수",
                    node_type="COMPANY",
                    value_kind="NUMBER",
                    units=("COUNT",),
                ),
            )
        }
    )
    claim = ClaimProposal.model_validate(candidate())
    binding = AttributeProposal.model_validate_json(
        json.dumps(
            {
                "kind": "ATTRIBUTE",
                "binding_id": "a1",
                "code": "COUNT",
                "target_mention": "m1",
                "value": {"kind": "NUMBER", "value": "2.5", "unit": "COUNT"},
            }
        )
    )
    assert _binding_valid(binding, claim, rules)
    wrong_unit = binding.model_copy(
        update={"value": binding.value.model_copy(update={"unit": "USD"})}
    )
    assert not _binding_valid(wrong_unit, claim, rules)
    assert not _binding_valid(
        binding.model_copy(update={"code": "UNKNOWN"}), claim, rules
    )
    period = {
        "kind": "PERIOD",
        "start": {"kind": "DATE", "value": "2026-06-01", "precision": "DAY"},
        "end": {"kind": "DATE", "value": "2026-01-01", "precision": "YEAR"},
    }
    PeriodValue.model_validate_json(json.dumps(period), strict=True)
    period["end"]["value"] = "2025-01-01"
    with pytest.raises(ValidationError):
        PeriodValue.model_validate_json(json.dumps(period), strict=True)
    with pytest.raises(ValidationError):
        TemporalPoint.model_validate_json(
            '{"value":"2026-06-02T00:00:00Z","precision":"MONTH"}', strict=True
        )


def test_calendar_date_temporal_point_uses_utc_anchor_only_for_calendar_precision():
    for raw, precision, expected in (
        ("2026-09-18", "DAY", datetime(2026, 9, 18, tzinfo=UTC)),
        ("2026-09-01", "MONTH", datetime(2026, 9, 1, tzinfo=UTC)),
        ("2026-01-01", "YEAR", datetime(2026, 1, 1, tzinfo=UTC)),
    ):
        point = TemporalPoint.model_validate_json(
            json.dumps({"value": raw, "precision": precision}), strict=True
        )
        assert point.value == expected
    for raw, precision in (
        ("2026-09-18", "INSTANT"),
        ("2026-09-18", "MONTH"),
        ("2026-13-18", "DAY"),
    ):
        with pytest.raises(ValidationError):
            TemporalPoint.model_validate_json(
                json.dumps({"value": raw, "precision": precision}), strict=True
            )


def test_rfc3339_temporal_point_is_parsed_before_calendar_anchor_validation():
    point = TemporalPoint.model_validate_json(
        '{"value":"2026-09-18T00:00:00Z","precision":"DAY"}', strict=True
    )
    assert point.value == datetime(2026, 9, 18, tzinfo=UTC)
    end_of_day = TemporalPoint.model_validate_json(
        '{"value":"2026-09-18T23:59:59Z","precision":"DAY"}', strict=True
    )
    assert end_of_day.value == datetime(2026, 9, 18, tzinfo=UTC)
    end_of_month = TemporalPoint.model_validate_json(
        '{"value":"2026-09-30T23:59:59Z","precision":"MONTH"}', strict=True
    )
    assert end_of_month.value == datetime(2026, 9, 1, tzinfo=UTC)
    end_of_year = TemporalPoint.model_validate_json(
        '{"value":"2026-12-31T23:59:59Z","precision":"YEAR"}', strict=True
    )
    assert end_of_year.value == datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="DATE_ANCHOR"):
        TemporalPoint.model_validate_json(
            '{"value":"2026-09-18T12:00:00Z","precision":"MONTH"}', strict=True
        )


def test_body_selection_limits_generation_without_changing_evidence_source_map():
    calls = []

    class Models:
        def call(self, role, prompt, payload, schema, limits):
            calls.append((role, [source.source_id for source in payload.sources]))
            return KnowledgeProposals(claims=[])

    selection = BodySelection(
        source_ids=["s1"],
        provider="typesafe",
        model="jev-1.13.0",
        policy_version="jev-source-safety-v2",
        drop_threshold=0.95,
    )
    result = extract_knowledge(
        source_document(),
        ontology(),
        Models(),
        limits(),
        include_structure=True,
        body_selection=selection,
    )
    assert result.status == "SUCCESS"
    assert calls == [("generation", ["s1"])]


def test_literal_topic_mention_requires_catalog_before_claim_review():
    rules = ontology().model_copy(
        update={
            "topics": ("인공지능",),
            "relations": (
                RelationRule(
                    code="HAS_TOPIC",
                    version_no=1,
                    revision_id=None,
                    description="자기 근거가 지원하는 Topic 연결",
                    direction="DIRECTED",
                    endpoints=(("COMPANY", "TOPIC"),),
                ),
            ),
        }
    )
    proposal = candidate(source="s1")
    proposal["statement"] = "한빛은 AI 제품을 발표했다."
    proposal["modality"] = "FACT"
    proposal["mentions"][1].update(text="AI", node_type="TOPIC", topic_name="인공지능")
    proposal["bindings"][0]["code"] = "HAS_TOPIC"
    review_calls = []

    def handle(request):
        payload = json.loads(request.content)
        name = wire_schema(payload)["title"]
        data = json.loads(payload["messages"][1]["content"])
        if name == "KnowledgeProposals":
            answer = {"claims": [proposal]}
        else:
            review_calls.append(data)
            item = data["candidates"][0]
            answer = {
                "claims": [
                    {
                        "candidate_id": "c1",
                        "support_verdict": "TRUE",
                        "publication_verdict": "PUBLISH",
                        "bindings": [
                            {"binding_id": binding["binding_id"], "verdict": "TRUE"}
                            for binding in item["retained_bindings"]
                        ],
                    }
                ]
            }
        return provider_response(request, json.dumps(answer, ensure_ascii=False))

    client = ModelStudio(
        SecretStr("offline"),
        Budget(8, Decimal("3")),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        accepted = extract_knowledge(
            source_document(), rules, client, limits(), include_structure=True
        )
        proposal["mentions"][1]["topic_name"] = "미승인 주제"
        rejected = extract_knowledge(
            source_document(), rules, client, limits(), include_structure=True
        )
    finally:
        client.close()
    assert len(accepted.verified) == 1 and not rejected.verified
    assert len(review_calls) == 1
    claim = review_calls[0]["candidates"][0]["claim"]
    assert claim["mentions"][1]["text"] == "AI"
    assert claim["mentions"][1]["topic_name"] == "인공지능"
    assert rejected.exclusions[0].code == "INVALID_BINDING_DEPENDENCY"


def test_endpoint_is_kimi_international_only():
    assert validate_base_url(BASE_URL) == BASE_URL
    for invalid in (
        BASE_URL.replace("https:", "http:"),
        BASE_URL.replace("openai.com", "moonshot.cn"),
        BASE_URL.replace(".com/", ".com.attacker.example/"),
        BASE_URL.replace("https://", "https://user@"),
        BASE_URL + "?redirect=elsewhere",
    ):
        with pytest.raises(CallFailed, match="UNAPPROVED_ENDPOINT"):
            ModelStudio(SecretStr("offline"), Budget(0, Decimal(0)), base_url=invalid)


def test_legacy_paid_cli_is_retired_before_credentials_or_network():
    path = Path(__file__).parents[1] / "run_role_harness_trial.py"
    with pytest.raises(SystemExit, match="LEGACY_TRIAL_RETIRED"):
        runpy.run_path(str(path), run_name="__main__")
