"""FastAPI 핵심 엔드포인트 라우터."""

from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.orm import Session, joinedload

from ontology_map.db.schema import Classification, Document, Node, Relation
from ontology_map.db.session import open_session
from ontology_map.schemas import (
    AgentExtractJsonRequest,
    IntakeClaim,
    IntakeEdge,
    IntakeNode,
    IntakePayload,
    IntakeResponse,
)
from ontology_map.services.entity_resolution import resolve_or_create_node
from ontology_map.services.graph_service import get_node_by_id, get_node_subgraph
from ontology_map.services.insight_service import get_node_insight, get_node_qa_pairs
from ontology_map.services.intake_service import process_intake
from ontology_map.services.task_queue import task_queue_manager

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

# --- Agent Extraction Queue Endpoints ---

@router.post("/agent/extract-async", status_code=status.HTTP_202_ACCEPTED)
async def extract_knowledge_async(payload: AgentExtractJsonRequest):
    """웹 URL 또는 텍스트 메모를 비동기 대기열에 등록합니다 (202 Accepted)."""
    # [빈칸 1]: task_queue_manager의 enqueue 메서드를 await로 호출하여 task_id를 발급받으세요.
    task_id = await task_queue_manager.enqueue(
        source_type=payload.source_type,
        raw_data=payload.content,
        filename_or_url=payload.title,
    )
    return {"task_id": task_id, "status": "pending"}


@router.post("/agent/extract-async/file", status_code=status.HTTP_202_ACCEPTED)
async def extract_file_async(file: UploadFile = File(...)):
    """PDF/DOCX/TXT 문서를 업로드받아 비동기 대기열에 등록합니다 (202 Accepted)."""
    file_bytes = await file.read()
    task_id = await task_queue_manager.enqueue(
        source_type="file",
        raw_data=file_bytes,
        filename_or_url=file.filename,
    )
    return {"task_id": task_id, "status": "pending"}


@router.get("/agent/tasks")
def list_agent_tasks():
    """현재 백그라운드 대기열 및 완료된 작업 목록을 조회합니다 (폴링용)."""
    tasks = task_queue_manager.list_tasks()
    return [t.to_summary_dict() for t in tasks]


@router.get("/agent/tasks/{task_id}")
def get_agent_task(task_id: str):
    """특정 대기열 작업의 진행 상태 및 추출 결과(IntakePayload)를 조회합니다."""
    task = task_queue_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    data = task.to_summary_dict()
    data["result"] = task.result.model_dump() if task.result else None
    return data


@router.delete("/agent/tasks/{task_id}")
def dismiss_agent_task(task_id: str):
    """완료되거나 확인한 작업을 대기열 목록에서 제거합니다."""
    if not task_queue_manager.dismiss_task(task_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return {"status": "ok"}
