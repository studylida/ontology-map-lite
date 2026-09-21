import json

import httpx
import pytest
from pydantic import SecretStr

from ontology_map.jev_judgment import (
    BindingCase,
    ClaimCase,
    PublicationCase,
    PublicationSource,
    evaluate_cases,
    evaluate_publication_cases,
    minimal_state,
    publication_state,
    publication_verdict,
)


def _transport(requests: list[dict[str, object]]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        answers = {}
        for key in payload["questions"]:
            score = 0.9
            if key.endswith("support_status") and len(payload["state"]["claims"]) == 1:
                score = 0.2
            answers[key] = {"type": "noul", "noul": score}
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        )

    return httpx.MockTransport(handle)


def _case(case_id: str) -> ClaimCase:
    return ClaimCase(
        case_id,
        "한빛전자는 새 기술을 개발할 계획이다.",
        "PLAN_OR_TARGET",
        ("한빛전자는 새 기술 개발 계획을 밝혔다.",),
        (
            BindingCase(
                f"{case_id}-b",
                {"kind": "RELATION", "code": "DEVELOPS", "stance": "SUPPORT"},
                (
                    {"text": "한빛전자", "node_type": "COMPANY"},
                    {"text": "새 기술", "node_type": "TECHNOLOGY"},
                ),
                {"code": "DEVELOPS", "endpoints": [["COMPANY", "TECHNOLOGY"]]},
            ),
        ),
    )


def test_minimal_state_excludes_internal_metadata() -> None:
    encoded = json.dumps(minimal_state(_case("secret-id")), ensure_ascii=False)
    assert "secret-id" not in encoded
    assert "source_id" not in encoded
    assert "offset" not in encoded
    assert "DEVELOPS" in encoded


def test_per_claim_and_packed_batching_and_thresholds() -> None:
    cases = (_case("c1"), _case("c2"))
    per_claim_requests: list[dict[str, object]] = []
    per_claim = evaluate_cases(
        cases,
        api_key=SecretStr("private"),
        transport=_transport(per_claim_requests),
    )
    packed_requests: list[dict[str, object]] = []
    packed = evaluate_cases(
        cases,
        api_key=SecretStr("private"),
        mode="packed",
        transport=_transport(packed_requests),
    )
    assert per_claim.calls == 2
    assert packed.calls == 1
    assert all(item.support_verdict == "FALSE" for item in per_claim.assessments)
    assert all(item.support_verdict == "TRUE" for item in packed.assessments)
    assert all(item.bindings[0].verdict == "TRUE" for item in packed.assessments)
    instructions = packed_requests[0]["questions"]["c0_support_core"]["instructions"]
    assert instructions["compare"] == ["claims[0].claim", "claims[0].evidence"]


def test_publication_policy_distinguishes_interpretation_and_attribution() -> None:
    assert publication_verdict(0.2, 0.1, None) == "PUBLISH"
    assert publication_verdict(0.9, 0.9, None) == "PUBLISH"
    assert publication_verdict(0.9, 0.1, None) == "EDITORIAL_INTERPRETATION"
    assert publication_verdict(0.9, 0.1, 0.9) == "PUBLISH"
    assert publication_verdict(0.9, 0.1, 0.1) == "EDITORIAL_INTERPRETATION"
    assert publication_verdict(0.5, 0.9, None) == "UNRESOLVED"
    with pytest.raises(ValueError, match="JEV_PUBLICATION_SOURCE"):
        PublicationSource("NEWS", "한빛전자")


def test_publication_state_is_minimal_and_official_question_is_conditional() -> None:
    news = PublicationCase(
        "news-id",
        "한빛전자의 시장 확대 전략이다.",
        ("한빛전자가 신규 투자를 발표했다.",),
        PublicationSource("NEWS"),
    )
    official = PublicationCase(
        "official-id",
        "한빛전자의 시장 확대 전략이다.",
        ("당사는 시장 확대 전략을 추진한다.",),
        PublicationSource("OFFICIAL_COMPANY", "한빛전자"),
    )
    encoded = json.dumps(publication_state(official), ensure_ascii=False)
    assert "official-id" not in encoded
    assert "modality" not in encoded
    assert "binding" not in encoded
    requests: list[dict[str, object]] = []
    result = evaluate_publication_cases(
        (news, official),
        api_key=SecretStr("private"),
        mode="packed",
        transport=_transport(requests),
    )
    assert result.calls == 1
    assert "c0_official_self" not in requests[0]["questions"]
    assert "c1_official_self" in requests[0]["questions"]
