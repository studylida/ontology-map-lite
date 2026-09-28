# server/src/ontology_map/services/ingestion_agent.py
"""GPT-6 Luna 기반 문서 온톨로지 및 지식 추출 에이전트."""

import json
import os
import re
from typing import Any
import httpx

from ontology_map.schemas import IntakeClaim, IntakeEdge, IntakeNode, IntakePayload
from ontology_map.settings import get_settings

# 기본 설정 (환경변수로 오버라이드 가능)
DEFAULT_OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")

SYSTEM_PROMPT = """당신은 비정형 문서에서 핵심 지식 온톨로지를 추출하는 전문 지식 그래프 엔지니어입니다.
주어진 문서를 깊이 분석하여, 시스템의 온톨로지 규격에 맞는 노드(Entity), 관계(Edge), 원문 인용(Claim)을 추출하세요.

[추출 규칙]
1. nodes: 문서의 핵심 주체, 대상, 기술, 프로젝트 등을 추출합니다.
   - classification: "COMPANY", "AGENCY", "PROJECT", "PERSON", "TOPIC", "METRIC", "GENERAL" 중 가장 알맞은 대문자 코드를 부여합니다.
   - name: 고유명사나 명확한 엔티티명
   - description: 1~2문장의 핵심 설명
2. edges: 노드 간의 유의미한 관계를 방향성 있게 추출합니다.
   - relation: "INVESTS_IN", "ORGANIZES", "DEVELOPS", "SUPPORTS", "OPERATES", "RELATED_TO" 등 명확한 대문자 관계명
   - source_name과 target_name은 반드시 nodes의 name과 정확히 일치해야 합니다.
3. claims: 추출된 사실을 뒷받침하는 문서 내 실제 원문 문장(Quote)을 최소 1개 이상 발췌합니다.
4. insights: 전체 문서를 2~3줄로 종합 요약한 summary를 작성합니다.

반드시 다른 설명 없이 순수 JSON 객체만 응답하세요.
"""


def clean_json_markdown(raw_text: str) -> str:
    """LLM이 코드블록(```json ... ```)으로 응답했을 때 순수 JSON 문자열만 추출합니다."""
    text = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    return cleaned.strip()


def extract_ontology_from_text(
    title: str,
    content: str,
    source_type: str = "document",
    existing_entities: list[str] | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> IntakePayload:
    """문서 텍스트를 LLM에 전달하여 구조화된 IntakePayload로 추출합니다."""
    settings = get_settings()
    key = api_key or settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
    url = (base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/")
    target_model = model or DEFAULT_MODEL

    # 컨텍스트 과다 입력 방지 (최대 15,000자 제한)
    trimmed_content = content[:15000]

    cross_link_instruction = ""
    if existing_entities and len(existing_entities) > 0:
        entity_list_str = ", ".join(existing_entities[:50])
        cross_link_instruction = f"""
[지식 교차 연결 (Cross-Knowledge Linkage)]
현재 시스템 지식베이스에 다음 주요 엔티티들이 이미 등록되어 있습니다:
{entity_list_str}

문서 분석 시, 새로 발견된 엔티티가 기존 엔티티들과 관계(예: 투자, 파트너십, 공급, 인물 소속, 기술 적용 등)를 맺고 있거나 합리적 연계점(Cross-Link)이 있다면:
- "edges" 항목에 기존 엔티티와의 연결 관계를 적극적으로 포함하세요 (source_name 또는 target_name에 기존 엔티티명 지정).
- 단, 문서 맥락상 근거가 명확한 관계만 연결하세요.
- (참고: 향후 고도화 단계에서는 JEV(TypeSafe Jev)가 엄격한 교차 검증 및 환각 필터링을 수행할 예정입니다.)
"""

    user_prompt = f"""[문서 제목]: {title}
[문서 유형]: {source_type}

[문서 본문]:
{trimmed_content}
{cross_link_instruction}
위 문서에서 노드, 관계, 인용문, 요약을 추출하여 JSON으로 반환하세요.
형식:
{{
  "nodes": [{{"name": "...", "classification": "...", "description": "..."}}],
  "edges": [{{"source_name": "...", "target_name": "...", "relation": "..."}}],
  "claims": [{{"quote": "...", "claim_text": "..."}}],
  "insights": {{"summary": "..."}}
}}
"""

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    raw_content = data["choices"][0]["message"]["content"]
    cleaned_json_str = clean_json_markdown(raw_content)

    parsed: dict[str, Any] = json.loads(cleaned_json_str)

    # IntakePayload 객체로 변환하여 반환
    return IntakePayload(
        source_project="agent-ingestion",
        document_title=title,
        document_content=trimmed_content[:2000],  # DB 저장용 축약
        document_uri=None,
        nodes=[IntakeNode(**n) for n in parsed.get("nodes", [])],
        edges=[IntakeEdge(**e) for e in parsed.get("edges", [])],
        claims=[IntakeClaim(**c) for c in parsed.get("claims", [])],
        insights=parsed.get("insights", {}),
    )
