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

SYSTEM_PROMPT = """당신은 비정형 문서에서 고품질 핵심 지식 온톨로지를 추출하는 전문 지식 그래프 엔지니어입니다.
주어진 문서를 깊이 분석하여, 시스템의 온톨로지 규격에 맞는 노드(Entity), 관계(Edge), 원문 인용(Claim)을 정밀하게 추출하세요.

[추출 및 품질 엄격 규칙]
1. nodes (핵심 고유 엔티티 7~10개 내외 엄격 압축):
   - 문서 전체에서 가장 핵심적이고 구체적인 주체(기업, 기관, 인물, 핵심 고유기술/제품명) 위주로 7~10개 내외만 선별 추출합니다.
   - [중요 금지]: 추상적 개념어, 광범위한 일반명사(예: "AI 컴퓨팅 플랫폼", "AI 인프라", "차세대 시장", "기술 협력", "생태계" 등)는 절대로 독립 노드로 생성하지 마십시오.
   - [100% 근거 강제]: 모든 노드는 반드시 문서 내 실제 원문 문장(claims의 quote)에 이름이 직접 언급되어야 합니다. 원문에 직접적 인용 근거가 없는 엔티티는 추출하지 마십시오.
   - classification: "COMPANY", "AGENCY", "PROJECT", "PERSON", "TOPIC", "METRIC", "GENERAL" 중 가장 알맞은 대문자 코드
   - name: 정확하고 정제된 고유명사 엔티티명
   - description: 문서 맥락에 기반한 1~2문장의 핵심 설명

2. claims (원천 사실 근거 인용문):
   - 추출된 모든 노드와 주요 관계를 완벽히 뒷받침할 수 있도록, 문서 내 실제 원문 문장(Quote)을 충분하고 정밀하게 발췌하세요.
   - 추출된 각 노드의 이름이 최소 1개 이상의 claim quote 또는 claim_text에 반드시 포함되어야 합니다.
   - quote는 문서 본문에 글자 하나 틀리지 않고 그대로 존재하는 실제 문장이어야 합니다.

3. edges (핵심 관계 위주 선별):
   - 선별된 7~10개의 핵심 노드 간의 유의미한 관계만 명확하게 추출합니다.
   - relation: "INVESTS_IN", "ORGANIZES", "DEVELOPS", "SUPPORTS", "OPERATES", "SUPPLIES_TO", "PARTNERS_WITH", "LEADS", "RELATED_TO" 등 명확한 대문자 관계명
   - source_name과 target_name은 반드시 nodes의 name과 정확히 일치해야 합니다.

4. insights: 전체 문서를 2~3줄로 종합 요약한 summary를 작성합니다.

반드시 다른 설명 없이 순수 JSON 객체만 응답하세요.
"""


def clean_json_markdown(raw_text: str) -> str:
    """LLM이 코드블록(```json ... ```)으로 응답했을 때 순수 JSON 문자열만 추출합니다."""
    text = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    return cleaned.strip()


STOP_CONCEPT_TOKENS = {
    "ai", "기반", "분야", "관련", "사업", "기술", "플랫폼", "인프라",
    "솔루션", "시스템", "시장", "센터", "그룹", "전략", "협력", "산업", "혁신"
}


def is_node_grounded_in_claims(node_name: str, claims: list[IntakeClaim]) -> tuple[bool, list[str]]:
    """노드가 최소 1개 이상의 Claim에 명확한 근거(언급)를 두고 있는지 다각도로 검증합니다."""
    if not claims:
        return True, []

    name_clean = node_name.strip()
    name_lower = name_clean.lower()
    matched_refs: list[str] = []

    # 1. 노드명 자체가 quote나 claim_text에 포함되는 경우
    for c in claims:
        combined = f"{c.quote.lower()} {(c.claim_text or '').lower()}"
        if name_lower in combined or (len(combined) > 0 and combined in name_lower):
            matched_refs.append(c.ref_id)

    if matched_refs:
        return True, matched_refs

    # 2. 노드명이 복합명사인 경우 2글자 이상의 핵심 고유 토큰이 quote/claim_text에 명확히 등장하는지 검증
    name_tokens = [
        tok for tok in re.findall(r"[a-zA-Z0-9가-힣]+", name_lower)
        if len(tok) >= 2 and tok not in STOP_CONCEPT_TOKENS
    ]
    if name_tokens:
        for c in claims:
            combined = f"{c.quote.lower()} {(c.claim_text or '').lower()}"
            if any(tok in combined for tok in name_tokens):
                matched_refs.append(c.ref_id)

    if matched_refs:
        return True, matched_refs

    # 3. 2음절 이상 고유 서브스트링 일치 (예: '한국전력'의 '전력' in '전력망')
    for i in range(len(name_clean) - 1):
        sub = name_clean[i:i + 2].lower()
        if sub not in STOP_CONCEPT_TOKENS:
            for c in claims:
                combined = f"{c.quote.lower()} {(c.claim_text or '').lower()}"
                if sub in combined:
                    matched_refs.append(c.ref_id)

    # 중복 제거 및 정렬
    unique_refs = sorted(list(set(matched_refs)))
    return (len(unique_refs) > 0), unique_refs


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

    # 상한선: 50,000자 초과 시 명시적 거절 (지시서 4절)
    if len(content) > 50000:
        raise ValueError(f"문서 본문이 상한선(50,000자)을 초과했습니다: {len(content)}자")

    trimmed_content = content

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
"""

    user_prompt = f"""[문서 제목]: {title}
[문서 유형]: {source_type}

[문서 본문]:
{trimmed_content}
{cross_link_instruction}
위 문서에서 핵심 고유 엔티티(7~10개 내외), 관계, 원문 인용문, 요약을 추출하여 JSON으로 반환하세요.
* 주의: 모든 노드는 claims의 quote에 직접 언급된 고품질 고유 엔티티여야 하며, 일반 개념어나 근거 없는 노드는 제외해야 합니다.

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
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    raw_content = data["choices"][0]["message"]["content"]
    cleaned_json_str = clean_json_markdown(raw_content)

    parsed: dict[str, Any] = json.loads(cleaned_json_str)

    # 1. Claims 파싱 및 오프셋 계산, ref_id 부여
    claim_items: list[IntakeClaim] = []
    raw_claims = parsed.get("claims", [])
    for idx, c in enumerate(raw_claims):
        quote = c.get("quote", "").strip()
        start_offset = None
        end_offset = None
        if quote and quote in content:
            start_offset = content.find(quote)
            end_offset = start_offset + len(quote)

        claim_items.append(
            IntakeClaim(
                ref_id=f"c{idx}",
                quote=quote,
                claim_text=c.get("claim_text") or quote,
                confidence=float(c.get("confidence", 1.0)),
                start_offset=start_offset,
                end_offset=end_offset,
            )
        )

    # 2. Nodes 파싱: 근거(Claim) 검증 및 고품질 엔티티(최대 10개) 엄격 선별
    node_items: list[IntakeNode] = []
    name_to_ref: dict[str, str] = {}
    valid_node_names: set[str] = set()

    for idx, n in enumerate(parsed.get("nodes", [])):
        name = str(n.get("name", "")).strip()
        if not name:
            continue

        is_grounded, matched_c_refs = is_node_grounded_in_claims(name, claim_items)
        if not is_grounded:
            # 원문 인용 근거가 전무한 환각/일반개념 노드는 철저히 탈락시킴
            continue

        ref_id = f"n{len(node_items)}"
        name_to_ref[name.lower()] = ref_id
        valid_node_names.add(name.lower())

        node_items.append(
            IntakeNode(
                ref_id=ref_id,
                name=name,
                classification=n.get("classification", "GENERAL"),
                description=n.get("description"),
                claim_refs=matched_c_refs,
                claim_ref=matched_c_refs[0] if matched_c_refs else None,
                properties=n.get("properties") or {},
            )
        )

    # 문서당 최대 10개 핵심 엔티티로 압축
    if len(node_items) > 10:
        node_items = node_items[:10]
        valid_node_names = {n.name.lower() for n in node_items}
        name_to_ref = {n.name.lower(): n.ref_id for n in node_items}

    # 3. Edges 파싱: 탈락된 노드 제외 및 source_ref, target_ref, claim_ref 정렬
    edge_items: list[IntakeEdge] = []
    first_claim_ref = claim_items[0].ref_id if claim_items else None
    existing_entities_lower = {e.lower() for e in (existing_entities or [])}

    for idx, e in enumerate(parsed.get("edges", [])):
        s_name = str(e.get("source_name", "")).strip()
        t_name = str(e.get("target_name", "")).strip()
        if not s_name or not t_name:
            continue

        s_lower = s_name.lower()
        t_lower = t_name.lower()

        # 양 끝점이 선별된 유효 노드이거나 기존 엔티티인 경우에만 엣지 채택
        is_s_valid = s_lower in valid_node_names or s_lower in existing_entities_lower
        is_t_valid = t_lower in valid_node_names or t_lower in existing_entities_lower
        if not (is_s_valid and is_t_valid):
            continue

        s_ref = name_to_ref.get(s_lower)
        t_ref = name_to_ref.get(t_lower)

        # 해당 엣지의 source 및 target이 언급된 실제 Claim 탐색
        matched_c_ref = None

        # 1순위: source와 target 모두 언급된 Claim
        for c in claim_items:
            q_lower = c.quote.lower()
            stmt_lower = (c.claim_text or "").lower()
            if (s_lower in q_lower or s_lower in stmt_lower) and (t_lower in q_lower or t_lower in stmt_lower):
                matched_c_ref = c.ref_id
                break

        # 2순위: target이 언급된 Claim (target이 구체적 제품/기술인 경우가 많음)
        if not matched_c_ref:
            for c in claim_items:
                q_lower = c.quote.lower()
                stmt_lower = (c.claim_text or "").lower()
                if t_lower in q_lower or t_lower in stmt_lower:
                    matched_c_ref = c.ref_id
                    break

        # 3순위: source가 언급된 Claim
        if not matched_c_ref:
            for c in claim_items:
                q_lower = c.quote.lower()
                stmt_lower = (c.claim_text or "").lower()
                if s_lower in q_lower or s_lower in stmt_lower:
                    matched_c_ref = c.ref_id
                    break

        final_c_ref = matched_c_ref or first_claim_ref

        edge_items.append(
            IntakeEdge(
                source_ref=s_ref,
                target_ref=t_ref,
                source_name=s_name,
                target_name=t_name,
                relation=e.get("relation", "RELATED_TO"),
                claim_ref=final_c_ref,
                claim_refs=[final_c_ref] if final_c_ref else [],
                properties=e.get("properties") or {},
            )
        )

    # 전체 정규화 본문 및 출처 보존 (지시서 M1)
    return IntakePayload(
        source_project="agent-ingestion",
        document_title=title,
        document_content=content,
        document_uri=None,
        nodes=node_items,
        edges=edge_items,
        claims=claim_items,
        insights=parsed.get("insights", {}),
    )
