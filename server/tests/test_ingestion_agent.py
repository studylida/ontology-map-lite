# server/tests/test_ingestion_agent.py
"""온톨로지 추출 에이전트 단위 테스트 (Mock 기반)."""

import json
from unittest.mock import MagicMock, patch
from ontology_map.services.ingestion_agent import (
    clean_json_markdown,
    extract_ontology_from_text,
)


def test_clean_json_markdown():
    """코드블록 감싸임 및 순수 텍스트 정제 검증."""
    raw = "```json\n{\"nodes\": []}\n```"
    assert clean_json_markdown(raw) == "{\"nodes\": []}"

    raw_no_tag = "```\n{\"edges\": []}\n```"
    assert clean_json_markdown(raw_no_tag) == "{\"edges\": []}"

    raw_clean = "{\"claims\": []}"
    assert clean_json_markdown(raw_clean) == "{\"claims\": []}"


@patch("httpx.Client.post")
def test_extract_ontology_from_text_mocked(mock_post):
    """LLM 응답을 모의(Mock)하여 IntakePayload DTO 생성 검증."""
    mock_response_data = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "nodes": [
                            {"name": "한국전력", "classification": "COMPANY", "description": "전력 공기업"},
                            {"name": "스마트그리드 사업", "classification": "PROJECT", "description": "차세대 전력망"}
                        ],
                        "edges": [
                            {"source_name": "한국전력", "target_name": "스마트그리드 사업", "relation": "ORGANIZES"}
                        ],
                        "claims": [
                            {"quote": "한전은 차세대 전력망에 200억을 투자한다.", "claim_text": "스마트그리드 투자"}
                        ],
                        "insights": {"summary": "한전의 신규 에너지 사업 계획"}
                    })
                }
            }
        ]
    }
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: mock_response_data,
        raise_for_status=lambda: None,
    )

    payload = extract_ontology_from_text(
        title="한전 에너지 공고",
        content="한전은 차세대 전력망에 200억을 투자한다.",
        source_type="text",
        api_key="mock-key",
    )

    assert payload.document_title == "한전 에너지 공고"
    assert len(payload.nodes) == 2
    assert payload.nodes[0].name == "한국전력"
    assert payload.nodes[0].classification == "COMPANY"
    assert len(payload.edges) == 1
    assert payload.edges[0].relation == "ORGANIZES"
    assert len(payload.claims) == 1
    assert "200억" in payload.claims[0].quote
