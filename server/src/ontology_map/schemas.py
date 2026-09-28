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
    nodes_created: int
    edges_created: int
    claims_created: int


class AgentExtractJsonRequest(BaseModel):
    source_type: Literal["url", "text"] = "text"
    content: str = Field(..., min_length=1)
    title: Optional[str] = None
