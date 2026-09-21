import json

import httpx
import pytest
from pydantic import SecretStr

from ontology_map.jev_judgment import BindingCase, PublicationSource
from ontology_map.jev_product import (
    ClaimSupportCase,
    ExistingCandidateCase,
    ProductClaimCase,
    evaluate_claim_support,
    evaluate_existing_candidate_identity,
    evaluate_publication_bindings,
)
from ontology_map.jev_selection import JevSelectionError


def _transport(
    requests: list[dict[str, object]], scores: dict[str, float] | None = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        answers = {
            key: {"type": "noul", "noul": (scores or {}).get(key, 0.1)}
            for key in payload["questions"]
        }
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": answers,
                "usage": {"input_tokens": 8, "output_tokens": 2},
            },
        )

    return httpx.MockTransport(handle)


def _binding(index: int) -> BindingCase:
    return BindingCase(
        f"binding-{index}",
        {"kind": "RELATION", "code": "USES"},
        (
            {"text": "한빛전자", "node_type": "COMPANY"},
            {"text": "기술", "node_type": "TECHNOLOGY"},
        ),
        {"code": "USES", "endpoints": [["COMPANY", "TECHNOLOGY"]]},
    )


def test_publication_and_bindings_share_one_strict_batch() -> None:
    requests: list[dict[str, object]] = []
    result = evaluate_publication_bindings(
        (
            ProductClaimCase(
                "claim-1",
                "한빛전자는 기술을 사용한다.",
                ("한빛전자가 기술 사용을 발표했다.",),
                PublicationSource("NEWS"),
                (_binding(1),),
            ),
        ),
        api_key=SecretStr("private"),
        transport=_transport(
            requests,
            {
                "c0_interpretation": 0.1,
                "c0_attribution": 0.1,
                "c0_binding_0_participant": 0.9,
                "c0_binding_0_meaning": 0.9,
            },
        ),
    )
    assessment = result.assessments[0]
    assert assessment.publication.verdict == "PUBLISH"
    assert assessment.bindings[0].verdict == "TRUE"
    assert len(requests[0]["questions"]) == 4
    assert "claim-1" not in json.dumps(requests[0]["state"], ensure_ascii=False)


def test_claim_support_uses_violation_scores_and_splits_at_64() -> None:
    requests: list[dict[str, object]] = []
    cases = tuple(
        ClaimSupportCase(
            f"claim-{index}",
            "한빛전자는 공장 건설 계획을 부인했다.",
            "FACT",
            ("한빛전자는 공장 건설 계획이 결정되지 않았다고 밝혔다.",),
        )
        for index in range(17)
    )
    result = evaluate_claim_support(
        cases,
        api_key=SecretStr("private"),
        transport=_transport(requests, {"c0_status_change": 0.9}),
    )
    assert result.calls == 2
    assert [len(request["questions"]) for request in requests] == [64, 4]
    assert result.assessments[0].verdict == "FALSE"
    assert result.assessments[1].verdict == "TRUE"


def test_existing_candidate_identity_hides_ids_and_splits_at_64() -> None:
    requests: list[dict[str, object]] = []
    cases = tuple(
        ExistingCandidateCase(
            f"pair-{index}",
            "한빛전자",
            "COMPANY",
            ("한빛전자가 기술을 발표했다.",),
            "한빛전자 주식회사",
            ("한빛전자",),
            ("business:123",),
        )
        for index in range(65)
    )
    result = evaluate_existing_candidate_identity(
        cases,
        api_key=SecretStr("private"),
        transport=_transport(requests, {"pair_0": 0.9}),
    )
    assert result.calls == 2
    assert [len(request["questions"]) for request in requests] == [64, 1]
    assert result.assessments[0].verdict == "SAME"
    assert result.assessments[1].verdict == "DIFFERENT"
    assert "pair-0" not in json.dumps(requests[0]["state"], ensure_ascii=False)


def test_oversized_claim_and_duplicate_ids_raise_typed_input_errors() -> None:
    oversized = ProductClaimCase(
        "claim",
        "한빛전자는 기술을 사용한다.",
        ("한빛전자가 기술 사용을 발표했다.",),
        PublicationSource("NEWS"),
        tuple(_binding(index) for index in range(32)),
    )
    with pytest.raises(JevSelectionError, match="JEV_INPUT_LIMIT"):
        evaluate_publication_bindings((oversized,), api_key=SecretStr("private"))
    duplicate = ExistingCandidateCase("pair", "한빛", "COMPANY", (), "한빛")
    with pytest.raises(JevSelectionError, match="JEV_JUDGMENT_INPUT"):
        evaluate_existing_candidate_identity(
            (duplicate, duplicate), api_key=SecretStr("private")
        )
