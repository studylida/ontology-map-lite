"""온톨로지 인제스트 및 에이전트 요청/응답 공통 스키마."""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class IntakeNode(BaseModel):
    name: str = Field(..., min_length=1, description="엔티티 이름 (필수)")
    classification: str = Field(default="GENERAL", description="온톨로지 분류 (기본값 GENERAL)")
    description: Optional[str] = None
    properties: Optional[dict[str, Any]] = None


class IntakeEdge(BaseModel):
    source_name: str = Field(..., description="출발 노드 이름 (필수)")
    target_name: str = Field(..., description="도착 노드 이름 (필수)")
    relation: str = Field(default="RELATED_TO", description="관계 유형 (기본값 RELATED_TO)")
    properties: Optional[dict[str, Any]] = None


class IntakeClaim(BaseModel):
    quote: str = Field(..., description="원천 인용문 또는 엑셀 셀 위치 (필수)")
    claim_text: Optional[str] = None
    confidence: Optional[float] = 1.0


class IntakePayload(BaseModel):
    source_project: str = Field(..., description="유입 프로젝트 식별자")
    document_title: Optional[str] = None
    document_content: Optional[str] = None
    document_uri: Optional[str] = None
    nodes: list[IntakeNode] = Field(default_factory=list)
    edges: list[IntakeEdge] = Field(default_factory=list)
    claims: list[IntakeClaim] = Field(default_factory=list)
    insights: Optional[dict[str, Any]] = None
    raw_metadata: Optional[dict[str, Any]] = None


class IntakeResponse(BaseModel):
    status: str = "success"
    source_project: str
    document_id: Optional[int] = None
    primary_node_id: Optional[int] = None
    nodes_created: int
    edges_created: int
    claims_created: int


class AgentExtractJsonRequest(BaseModel):
    source_type: Literal["url", "text"] = "text"
    content: str = Field(..., min_length=1)
    title: Optional[str] = None


class NodeClaimItem(BaseModel):
    id: int
    quote: str
    statement: str
    document_title: Optional[str] = "출처 문서"


class NodeDetailsResponse(BaseModel):
    node_id: int
    name: str
    classification_code: str
    classification_name: str
    description: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)
    claims: list[NodeClaimItem] = Field(default_factory=list)
    recent_history_summary: Optional[str] = None
    overall_insight: Optional[str] = None
    issues: Optional[list[Any]] = None
    qa_pairs: list[dict[str, Any]] = Field(default_factory=list)
