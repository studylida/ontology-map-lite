"""노드 인사이트 및 Q&A 생성/조회 서비스 (M3 단일 생성 흐름 및 최신성 지문 원자적 갱신)."""

import hashlib
import json
import os
import re
from typing import Any, Optional
import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, joinedload

from ontology_map.db.schema import Claim, Edge, Node, NodeInsight, NodeQAPair
from ontology_map.settings import get_settings

DEFAULT_OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-luna")

ANALYSIS_SYSTEM_PROMPT = """당신은 지식그래프 엔티티의 누적된 사실(Claims)과 관계망(Edges)을 입체적으로 종합 분석하는 수석 지식 애널리스트입니다.

[분석 및 해석 지침]
1. 단순 요약 금지: 원천 인용문(Quote)을 단순히 짜깁기하거나 기사 본문을 그대로 반복/바꿔 쓰지 마세요.
2. 종합 해석(Synthesis) 중심:
   - 전략적 위상(Strategic Role): 이 엔티티가 전체 지식망에서 어떤 핵심 매개체나 허브 역할을 수행하는지 해석하세요.
   - 생태계 및 기술 파급력: 파트너십, 공동 개발, 기술 스택 연계가 결합되었을 때 창출되는 시너지와 산업적 함의를 도출하세요.
   - 한계 및 불확실성(Caveat): 확인된 사실과 해석을 구분하고, 현재 자료에서 확인되지 않은 미공개 사항(예: 일정, 양산 규모, 실질 성과)이나 향후 관전 포인트를 객관적으로 짚으세요.
3. recent_history_summary: 시간 순서 또는 확인된 핵심 이벤트 중심의 최근 동향 요약.
4. issues: 이 엔티티와 관련해 주의 깊게 모니터링해야 할 핵심 쟁점 및 전략 이슈 태그 (3~5개).
5. qa_pairs: 사용자가 이 엔티티의 역할과 협력 관계를 파악하는 데 가장 유의미한 핵심 질문과 사실 기반의 답변 쌍 (3~5개).

반드시 다른 설명 없이 아래 JSON 규격으로만 응답하세요:
{
  "recent_history_summary": "최근 핵심 동향 요약...",
  "overall_insight": "엔티티의 전략적 위상, 관계 결합에 따른 파급력, 그리고 확인된 한계점을 포괄하는 2~3개 문단의 심층 분석...",
  "issues": ["쟁점 및 핵심 이슈 1", "쟁점 및 핵심 이슈 2", "..."],
  "qa_pairs": [
    {"question": "핵심 질문 1", "answer": "근거 기반 답변 1"}
  ]
}
"""


def clean_json_markdown(raw_text: str) -> str:
    text = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    return cleaned.strip()


def assemble_node_analysis_input(session: Session, node_id: int) -> dict[str, Any]:
    """노드의 승인 Claim, 속성, 관계를 결정론적으로 조립하고 fingerprint를 계산합니다."""
    stmt_node = select(Node).options(joinedload(Node.classification)).where(Node.id == node_id)
    node = session.execute(stmt_node).scalar_one_or_none()
    if not node:
        return {}

    # 1. 노드에 명시적으로 연결된 Claim 목록 (최대 100건 상한)
    claim_ids = sorted(list(node.claim_ids or []))[:100]
    claims: list[Claim] = []
    if claim_ids:
        stmt_claims = select(Claim).where(Claim.id.in_(claim_ids)).order_by(Claim.id.asc())
        claims = list(session.execute(stmt_claims).scalars().all())

    # 2. 노드와 연결된 엣지 목록 (최대 50건)
    stmt_edges = (
        select(Edge)
        .options(joinedload(Edge.relation), joinedload(Edge.source_node), joinedload(Edge.target_node))
        .where(or_(Edge.source_node_id == node_id, Edge.target_node_id == node_id))
        .limit(50)
    )
    edges = list(session.execute(stmt_edges).scalars().all())

    # 3. 결정론적 지문(fingerprint) 계산 (SHA-256)
    fingerprint_data = {
        "node_id": node.id,
        "name": node.name,
        "classification": node.classification.code if node.classification else "GENERAL",
        "description": node.description or "",
        "properties": node.properties or {},
        "claims": [{"id": c.id, "quote": c.quote_text, "statement": c.statement} for c in claims],
        "edges": [
            {
                "id": e.id,
                "rel": e.relation.code if e.relation else "",
                "src": e.source_node.name if e.source_node else "",
                "tgt": e.target_node.name if e.target_node else "",
            }
            for e in edges
        ],
    }

    serialized = json.dumps(fingerprint_data, sort_keys=True, ensure_ascii=False)
    input_fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    return {
        "node": node,
        "claims": claims,
        "edges": edges,
        "input_fingerprint": input_fingerprint,
    }


def get_node_insight(session: Session, node_id: int) -> Optional[NodeInsight]:
    """특정 노드의 종합 인사이트 보고서를 조회합니다 (GET 요청 시 모델 호출 0회)."""
    stmt = select(NodeInsight).where(NodeInsight.node_id == node_id)
    return session.execute(stmt).scalar_one_or_none()


def get_node_qa_pairs(session: Session, node_id: int) -> list[NodeQAPair]:
    """특정 노드의 Q&A 목록을 순서대로 조회합니다 (GET 요청 시 모델 호출 0회)."""
    stmt = select(NodeQAPair).where(NodeQAPair.node_id == node_id).order_by(NodeQAPair.sequence.asc())
    return list(session.execute(stmt).scalars().all())


def generate_node_insight(
    session: Session,
    node_id: int,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    force: bool = False,
) -> tuple[Optional[NodeInsight], list[NodeQAPair]]:
    """누적된 승인 근거로 노드 분석과 Q&A를 단일 생성 흐름으로 만들고 조건부 원자적 교체합니다."""
    assembled = assemble_node_analysis_input(session, node_id)
    if not assembled:
        return None, []

    node: Node = assembled["node"]
    claims: list[Claim] = assembled["claims"]
    edges: list[Edge] = assembled["edges"]
    current_fingerprint: str = assembled["input_fingerprint"]

    # 1. 동일 입력 fingerprint의 기존 유효 결과가 있으면 모델 호출 건너뜀 (3.3절 / M3.5 / T10)
    existing_insight = get_node_insight(session, node_id)
    if existing_insight and existing_insight.input_fingerprint == current_fingerprint and not force:
        qa_pairs = get_node_qa_pairs(session, node_id)
        return existing_insight, qa_pairs

    # 2. 근거 부족 판단: Claim이 0개이고 설명도 빈약한 경우
    if not claims and not node.description:
        # 근거 부족 안내 결과 저장 (지시서 M3.3)
        soft_fallback_msg = "아직 관련 소식이나 확인된 근거가 충분하지 않아 분석 요약을 생성하지 않았습니다."
        if not existing_insight:
            existing_insight = NodeInsight(
                node_id=node.id,
                recent_history_summary="",
                overall_insight=soft_fallback_msg,
                issues=[],
                input_fingerprint=current_fingerprint,
            )
            session.add(existing_insight)
            session.commit()
        else:
            existing_insight.recent_history_summary = ""
            existing_insight.overall_insight = soft_fallback_msg
            existing_insight.issues = []
            existing_insight.input_fingerprint = current_fingerprint
            session.commit()
        return existing_insight, []

    # 3. 단일 프롬프트 구성 (최근 이력, 종합 인사이트, 이슈, Q&A를 한 번에 생성)
    claims_text = "\n".join(
        [f"- [Claim ID: {c.id}] {c.statement} (인용: \"{c.quote_text}\")" for c in claims[:30]]
    )
    edges_text = "\n".join(
        [
            f"- {e.source_node.name} --[{e.relation.display_name or e.relation.code}]--> {e.target_node.name}"
            for e in edges[:20]
            if e.source_node and e.target_node and e.relation
        ]
    )

    user_prompt = f"""[분석 대상 노드]
- 이름: {node.name}
- 분류: {node.classification.display_name if node.classification else 'GENERAL'}
- 설명: {node.description or '없음'}

[승인된 원천 근거 (Claims)]:
{claims_text or '연계된 Claim 없음'}

[지식그래프 연결 관계]:
{edges_text or '연결된 관계 없음'}

위 정보를 바탕으로 최근 이력, 종합 인사이트, 핵심 이슈(최대 5개), Q&A(최대 5개)를 생성하세요.
"""

    settings = get_settings()
    key = api_key or settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
    url = (base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/")
    target_model = model or DEFAULT_MODEL

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        raw_content = data["choices"][0]["message"]["content"]
        cleaned_json = clean_json_markdown(raw_content)
        parsed = json.loads(cleaned_json)
    except Exception as exc:
        # 모델 호출/파싱 실패 시 기존 유효 결과를 훼손하지 않음 (지시서 M3.4)
        if existing_insight:
            return existing_insight, get_node_qa_pairs(session, node_id)
        raise exc

    recent_history = parsed.get("recent_history_summary", "")
    overall_insight = parsed.get("overall_insight", "")
    raw_issues = parsed.get("issues", [])
    issues = raw_issues[:5] if isinstance(raw_issues, list) else []
    raw_qa = parsed.get("qa_pairs", [])
    qa_list = raw_qa[:5] if isinstance(raw_qa, list) else []

    # 4. 저장 직전 입력 최신성 재확인 (T09 늦은 분석 결과 덮어쓰기 방지)
    latest_check = assemble_node_analysis_input(session, node_id)
    if latest_check.get("input_fingerprint") != current_fingerprint and not force:
        # 입력이 생성 도중 변경되었으므로 구버전 저장 방지
        return existing_insight, get_node_qa_pairs(session, node_id)

    # 5. NodeInsight 원자적 교체
    if existing_insight:
        existing_insight.recent_history_summary = recent_history
        existing_insight.overall_insight = overall_insight
        existing_insight.issues = issues
        existing_insight.input_fingerprint = current_fingerprint
    else:
        existing_insight = NodeInsight(
            node_id=node.id,
            recent_history_summary=recent_history,
            overall_insight=overall_insight,
            issues=issues,
            input_fingerprint=current_fingerprint,
        )
        session.add(existing_insight)

    # 6. NodeQAPair 원자적 교체 (기존 삭제 후 최대 5개 신규 삽입)
    stmt_del = delete(NodeQAPair).where(NodeQAPair.node_id == node_id)
    session.execute(stmt_del)

    created_qas: list[NodeQAPair] = []
    for seq, qa in enumerate(qa_list):
        q_text = str(qa.get("question", "")).strip()
        a_text = str(qa.get("answer", "")).strip()
        if q_text and a_text:
            qa_obj = NodeQAPair(
                node_id=node.id,
                question=q_text,
                answer=a_text,
                sequence=seq,
            )
            session.add(qa_obj)
            created_qas.append(qa_obj)

    session.commit()
    session.refresh(existing_insight)

    return existing_insight, created_qas
