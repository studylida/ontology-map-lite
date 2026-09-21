from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ontology_map import panel
from ontology_map.api import (
    EvidenceLocatorResponse,
    EvidenceSourceResponse,
    NodeTypeResponse,
    _resource_id,
)
from ontology_map.db.session import open_read_session
from ontology_map.exploration import TimeWindow
from ontology_map.pagination import InvalidCursorError

router = APIRouter(prefix="/api/v1")
ResourceId = Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")]
ReadSession = Annotated[Session, Depends(open_read_session)]
Window = Annotated[TimeWindow, Query()]
Cursor = Annotated[str | None, Query(min_length=1, max_length=2048)]


class ClaimConnectionNode(BaseModel):
    node_id: str
    name: str
    node_type: NodeTypeResponse


class ClaimRelationConnection(BaseModel):
    relation_id: str
    display_name: str
    directionality: Literal["DIRECTED", "SYMMETRIC"]
    source_node: ClaimConnectionNode
    target_node: ClaimConnectionNode
    other_node: ClaimConnectionNode
    stance: Literal["SUPPORT", "DISPUTE"]


class ClaimConnection(BaseModel):
    kind: Literal["RELATION", "ATTRIBUTE", "EVENT_TIME", "CONFLICT"]
    target_id: str
    position: str | None
    label: str
    relation: ClaimRelationConnection | None = None


class PanelClaim(BaseModel):
    claim_id: str
    claim_text: str
    modality: Literal[
        "FACT",
        "PLAN_OR_TARGET",
        "PREDICTION_OR_ESTIMATE",
        "OPINION_OR_EVALUATION",
    ]
    knowledge_state: str
    evidence_group_count: int = Field(ge=0)
    as_of_at: datetime
    role: Literal["KEY_CLAIM", "SUPPORTING_CLAIM", "CONTRASTING_CLAIM"] | None = None
    connections: list[ClaimConnection] = []


class ClaimPage(BaseModel):
    items: list[PanelClaim]
    next_cursor: str | None


class PanelTrace(BaseModel):
    trace_id: str
    source: EvidenceSourceResponse
    quote_text: str
    locator: EvidenceLocatorResponse
    period_role: Literal["IN_WINDOW", "BACKGROUND", "UNKNOWN"]


class TracePage(BaseModel):
    items: list[PanelTrace]
    next_cursor: str | None


class TraceQuery(BaseModel):
    time_window: TimeWindow
    as_of_at: datetime
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("as_of_at")
    @classmethod
    def aware_date(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.year < 2:
            raise ValueError(
                "as_of_at must include a timezone and allow the period start"
            )
        return value


class QuestionItem(BaseModel):
    question_id: str
    question_text: str


class QuestionPage(BaseModel):
    items: list[QuestionItem]
    next_cursor: str | None


class QuestionAnswer(QuestionItem):
    answer: str
    caveat: str | None
    node_id: str
    time_window: TimeWindow
    as_of_at: datetime
    section_id: str | None
    claims: list[PanelClaim]


class ReportSection(BaseModel):
    section_id: str
    title: str
    synthesis: str | None = None
    caveat: str | None = None
    claims: list[PanelClaim] = []


class ReportItem(BaseModel):
    report_id: str
    node_id: str
    title: str
    summary: str
    as_of_at: datetime
    time_window: TimeWindow
    evidence_group_count: int = Field(ge=0)
    sections: list[ReportSection]
    conclusion: str | None = None
    caveat: str | None = None


class ReportPage(BaseModel):
    items: list[ReportItem]
    next_cursor: None = None


@router.get("/nodes/{node_id}/claims", response_model=ClaimPage)
def claims(
    node_id: ResourceId,
    time_window: Window,
    session: ReadSession,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> ClaimPage:
    return ClaimPage.model_validate(
        panel.list_claims(session, _resource_id(node_id), time_window, cursor, limit)
    )


@router.get("/nodes/{node_id}/claims/{claim_id}/evidence", response_model=TracePage)
def evidence(
    node_id: ResourceId,
    claim_id: ResourceId,
    query: Annotated[TraceQuery, Query()],
    session: ReadSession,
) -> TracePage:
    return TracePage.model_validate(
        panel.claim_evidence(
            session,
            _resource_id(node_id),
            _resource_id(claim_id),
            query.time_window,
            query.as_of_at,
            query.cursor,
        )
    )


@router.get("/nodes/{node_id}/questions", response_model=QuestionPage)
def questions(
    node_id: ResourceId,
    time_window: Window,
    session: ReadSession,
    cursor: Cursor = None,
) -> QuestionPage:
    return QuestionPage.model_validate(
        panel.list_questions(session, _resource_id(node_id), time_window, cursor)
    )


@router.get("/questions/{question_id}", response_model=QuestionAnswer)
def answer(question_id: ResourceId, session: ReadSession) -> QuestionAnswer:
    return QuestionAnswer.model_validate(
        panel.read_question(session, _resource_id(question_id))
    )


@router.get(
    "/nodes/{node_id}/insight-report",
    response_model=ReportPage,
    response_model_exclude_none=True,
)
def report(
    node_id: ResourceId,
    time_window: Window,
    session: ReadSession,
    detail: Annotated[bool, Query()] = False,
) -> ReportPage:
    return ReportPage.model_validate(
        panel.read_report(session, _resource_id(node_id), time_window, detail=detail)
    )


def panel_error_handler(_request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, panel.PanelNotReadyError):
        status, code, retryable = 503, "PANEL_NOT_READY", True
    elif isinstance(error, InvalidCursorError):
        status, code, retryable = 422, "INVALID_REQUEST", False
    else:
        status, code, retryable = 404, "PANEL_NOT_FOUND", False
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "retryable": retryable}}
    )
