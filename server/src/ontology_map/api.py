"""FastAPI 핵심 엔드포인트 라우터."""

from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ontology_map.db.schema import Classification, Document, Node, Relation
from ontology_map.db.session import open_session
from ontology_map.services.entity_resolution import resolve_or_create_node
from ontology_map.services.graph_service import get_node_by_id, get_node_subgraph
from ontology_map.services.insight_service import get_node_insight, get_node_qa_pairs

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
