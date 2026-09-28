"""FastAPI 핵심 엔드포인트 라우터."""

from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.orm import Session, joinedload

from ontology_map.db.schema import Classification, Document, Node, Relation
from ontology_map.db.session import open_session
from ontology_map.services.entity_resolution import resolve_or_create_node
from ontology_map.services.graph_service import get_node_by_id, get_node_subgraph
from ontology_map.services.insight_service import get_node_insight, get_node_qa_pairs
from ontology_map.services.intake_service import process_intake

router = APIRouter(prefix="/api/v1")


# --- Response / Request Schemas ---
class HealthResponse(BaseModel):
    status: str = "ok"


class ClassificationResponse(BaseModel):
    id: int
    code: str
    display_name: str
    description: Optional[str] = None


class NodeCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    classification_code: str
    description: Optional[str] = None
    properties: Optional[dict[str, Any]] = None


class IntakeNode(BaseModel):
    name: str = Field(..., min_length=1, description="엔티티 이름 (필수)")
    # 분류 코드가 없으면 기본값으로 'GENERAL'을 부여합니다.
    classification: str = Field(default="GENERAL", description="온톨로지 분류 (기본값 GENERAL)")
    description: Optional[str] = None
    properties: Optional[dict[str, Any]] = None


class IntakeEdge(BaseModel):
    source_name: str = Field(..., description="출발 노드 이름 (필수)")
    target_name: str = Field(..., description="도착 노드 이름 (필수)")
    # 관계명이 없으면 기본값으로 'RELATED_TO'를 부여합니다.
    relation: str = Field(default="RELATED_TO", description="관계 유형 (기본값 RELATED_TO)")
    properties: Optional[dict[str, Any]] = None


class IntakeClaim(BaseModel):
    quote: str = Field(..., description="원천 인용문 또는 엑셀 셀 위치 (필수)")
    claim_text: Optional[str] = None
    confidence: Optional[float] = 1.0


class IntakePayload(BaseModel):
    """동기 프로젝트들의 이질적인 데이터를 유연하게 수용하는 최상위 DTO."""
    source_project: str = Field(..., description="출처 식별자 (예: excel-agent, news-agent, gov-insight)")
    document_title: Optional[str] = None
    document_content: Optional[str] = None
    document_uri: Optional[str] = None
    
    # [빈칸 1]: 노드 목록을 받는 필드입니다. 없을 경우 빈 리스트([])를 기본값으로 갖도록 완성해 보세요.
    nodes: list[IntakeNode] = Field(default_factory=list, description="엔티티 노드 목록")

    # [빈칸 2]: 엣지 목록을 받는 필드입니다. 없을 경우 빈 리스트([])를 기본값으로 갖도록 완성해 보세요.
    edges: list[IntakeEdge] = Field(default_factory=list, description="관계 엣지 목록")

    claims: list[IntakeClaim] = Field(default_factory=list)
    raw_metadata: Optional[dict[str, Any]] = None


class IntakeResponse(BaseModel):
    """적재 성공 후 클라이언트에게 반환할 요약 결과."""
    status: str = "success"
    source_project: str
    document_id: Optional[int] = None
    nodes_created: int
    edges_created: int
    claims_created: int


class NodeSearchItem(BaseModel):
    id: int
    name: str
    classification_code: str
    classification_name: str
    description: Optional[str] = None



# --- Endpoints ---

@router.get("/health", response_model=HealthResponse)
def health_check():
    """서버 및 DB 헬스 체크."""
    return HealthResponse()


@router.get("/classifications", response_model=list[ClassificationResponse])
def list_classifications(session: Session = Depends(open_session)):
    """온톨로지 분류(Classification) 목록 조회."""
    stmt = select(Classification).where(Classification.is_active.is_(True))
    return session.execute(stmt).scalars().all()


@router.post("/nodes")
def create_or_resolve_node(
    payload: NodeCreateRequest,
    session: Session = Depends(open_session),
):
    """엔티티 노드 조회 또는 신규 생성."""
    try:
        node = resolve_or_create_node(
            session=session,
            name=payload.name,
            classification_code=payload.classification_code,
            description=payload.description,
            properties=payload.properties,
        )
        session.commit()
        return {"id": node.id, "name": node.name, "classification_id": node.classification_id}
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/nodes/{node_id}/graph")
def get_subgraph(
    node_id: int,
    session: Session = Depends(open_session),
):
    """특정 노드 중심 1-hop 서브그래프 조회."""
    result = get_node_subgraph(session, node_id)
    if not result["nodes"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return result


@router.get("/nodes/{node_id}/insights")
def get_insights(
    node_id: int,
    session: Session = Depends(open_session),
):
    """특정 노드의 종합 인사이트 및 Q&A 목록 조회."""
    node = get_node_by_id(session, node_id)
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    insight = get_node_insight(session, node_id)
    qa_pairs = get_node_qa_pairs(session, node_id)

    return {
        "node_id": node.id,
        "node_name": node.name,
        "insight": {
            "recent_history_summary": insight.recent_history_summary,
            "overall_insight": insight.overall_insight,
            "issues": insight.issues,
            "generated_at": insight.generated_at,
        } if insight else None,
        "qa_pairs": [
            {
                "question": qa.question,
                "answer": qa.answer,
                "sequence": qa.sequence,
            }
            for qa in qa_pairs
        ],
    }


@router.post("/intake", response_model=IntakeResponse)
def intake_external_data(
    payload: IntakePayload,
    session: Session = Depends(open_session),
):
    """타 프로젝트 분석 산출물을 8개 테이블로 자동 보정·적재하는 API."""
    result = process_intake(session=session, payload=payload)
    session.commit()
    return result


@router.get("/nodes/search", response_model=list[NodeSearchItem])
def search_nodes(
    q: str = "",
    session: Session = Depends(open_session),
):
    """노드 이름 및 설명 대상 대소문자 무시 부분 일치 검색 API."""
    clean_q = q.strip()
    
    # [빈칸 1]: Node.name 또는 Node.description에 clean_q가 포함되어 있는지
    #           대소문자 무시(ilike) 조건을 or_() 안에 채워보세요.
    # 힌트: Node.name.ilike(f"%{clean_q}%")
    stmt = (
        select(Node)
        .options(joinedload(Node.classification))
        .where(
            or_(
                Node.name.ilike(f"%{clean_q}%"),
                Node.description.ilike(f"%{clean_q}%"),
            )
        )
        .order_by(Node.id.desc())
        .limit(20)
    )
    
    nodes = session.execute(stmt).scalars().all()
    
    return [
        NodeSearchItem(
            id=n.id,
            name=n.name,
            classification_code=n.classification.code,
            classification_name=n.classification.display_name,
            description=n.description,
        )
        for n in nodes
    ]
