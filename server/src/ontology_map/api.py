import os
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path as FilePath
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.orm import Session

from ontology_map import jev_selection
from ontology_map.db import schema
from ontology_map.db.session import get_engine, open_read_session
from ontology_map.db.source_processing import (
    elapsed_seconds,
    finish_processing_job,
    get_processing_job,
    latest_processing_job,
)
from ontology_map.exploration import (
    ExplorationNotFoundError,
    PublicationNotReadyError,
    TimeWindow,
    get_exploration,
    list_peripheral_nodes,
)
from ontology_map.insights import InsightNotFoundError, get_insight, list_node_insights
from ontology_map.pagination import InvalidCursorError
from ontology_map.relations import (
    NodeRelationsNotFoundError,
    RelationEvidenceNotFoundError,
    list_node_relations,
    list_relation_evidence,
)
from ontology_map.search import InvalidSearchQueryError, search_nodes
from ontology_map.source_intake import (
    MAX_UPLOAD_BYTES,
    SourceIntakeError,
    SourceIntakeMetadata,
    extract_normalized_body,
    materialize_uploaded_source,
    parse_published_at,
    split_source_spans,
)
from ontology_map.source_worker import try_submit_source_processing

router = APIRouter(prefix="/api/v1")

_MAX_BIGINT = 9_223_372_036_854_775_807
_JEV_DEMO_MAX_BODY_CHARS = 20_000


class ErrorBody(BaseModel):
    code: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorBody


class NodeTypeResponse(BaseModel):
    code: str
    display_name: str


class GraphNodeResponse(BaseModel):
    node_id: str
    name: str
    node_type: NodeTypeResponse
    tier: Literal["CENTER", "DIRECT", "TWO_HOP", "THREE_HOP"]
    activity_evidence_group_count: int = Field(ge=0)


class GraphRelationResponse(BaseModel):
    directionality: Literal["DIRECTED", "SYMMETRIC"]
    relation_id: str
    source_node_id: str
    target_node_id: str
    relation_type_display_name: str
    supporting_evidence_group_count: int = Field(ge=1)
    has_conflict: bool


class GraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    relations: list[GraphRelationResponse]


class RecommendationNodeResponse(BaseModel):
    node_id: str
    name: str
    node_type: NodeTypeResponse


class RelationPathResponse(GraphRelationResponse):
    source_node_name: str
    target_node_name: str


class RecommendationResponse(BaseModel):
    path: list[RelationPathResponse]

    target_node: RecommendationNodeResponse
    reason_code: Literal["DIRECT", "TWO_HOP", "AMBIENT"]
    via_node_id: str | None
    supporting_evidence_group_count: int | None = Field(default=None, ge=1)


class FollowupQuestionResponse(BaseModel):
    slot: Literal[1, 2]
    question_text: str
    target_node_id: str


class ExplorationResponse(BaseModel):
    center_node_id: str
    context_text: str
    graph: GraphResponse
    recommendations: list[RecommendationResponse]
    followup_questions: list[FollowupQuestionResponse]


class PeripheralNodeResponse(BaseModel):
    node_id: str
    name: str
    node_type: NodeTypeResponse
    tier: Literal["AMBIENT"]
    activity_evidence_group_count: int = Field(ge=0)


class PeripheralGraphResponse(BaseModel):
    nodes: list[PeripheralNodeResponse]
    relations: list[GraphRelationResponse]


class PeripheralResponse(BaseModel):
    graph: PeripheralGraphResponse
    next_cursor: str | None


class SearchResultResponse(BaseModel):
    node_id: str
    name: str
    node_type: NodeTypeResponse


class SearchResponse(BaseModel):
    items: list[SearchResultResponse]


class RelatedNodeResponse(BaseModel):
    node_id: str
    name: str
    node_type: NodeTypeResponse


class NodeRelationResponse(BaseModel):
    source_node_id: str
    target_node_id: str
    directionality: Literal["DIRECTED", "SYMMETRIC"]

    relation_id: str
    other_node: RelatedNodeResponse
    relation_type_display_name: str
    supporting_evidence_group_count: int = Field(ge=1)
    has_conflict: bool


class NodeRelationsResponse(BaseModel):
    items: list[NodeRelationResponse]
    next_cursor: str | None


class EvidenceSourceResponse(BaseModel):
    title: str
    publisher_name: str
    published_at: datetime | None
    published_precision: Literal["INSTANT", "DAY", "MONTH", "YEAR", "UNKNOWN"]
    canonical_url: str | None


class SourceIntakeResponse(BaseModel):
    source_document_id: str
    processing_job_id: str
    version_no: int = Field(ge=1)
    status: Literal[
        "QUEUED",
        "RUNNING",
        "READY",
        "FAILED",
        "INTERRUPTED",
        "EXCLUDED_LANGUAGE",
    ]
    stage: str
    normalized_body: str
    body_hash: str
    canonical_url: str | None


class SourceIntakeStatusResponse(BaseModel):
    source_document_id: str
    processing_job_id: str | None
    body_hash: str
    status: Literal[
        "STORED",
        "QUEUED",
        "RUNNING",
        "READY",
        "FAILED",
        "INTERRUPTED",
        "EXCLUDED_LANGUAGE",
    ]
    stage: str | None
    extraction_task_id: str | None
    promotion_batch_id: str | None
    error_code: str | None
    elapsed_seconds: float | None = Field(default=None, ge=0)
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


class JevSpanResponse(BaseModel):
    source_id: str
    text: str
    drop_probability: float = Field(ge=0, le=1)
    decision: Literal["KEEP", "DROP"]


class JevValidationResponse(BaseModel):
    status: Literal["PASSED", "BLOCKED"]
    reason: str | None
    model: str
    policy_version: str
    body_hash: str
    drop_threshold: float = Field(ge=0, le=1)
    injection_threshold: float = Field(ge=0, le=1)
    spans_total: int = Field(ge=1)
    spans_kept: int = Field(ge=0)
    spans_dropped: int = Field(ge=0)
    calls: int | None = Field(default=None, ge=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    elapsed_seconds: float = Field(ge=0)
    promotion_started: Literal[False] = False
    decisions: list[JevSpanResponse]


class EvidenceLocatorResponse(BaseModel):
    paragraph_number: int | None = Field(default=None, ge=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=1)


class RelationEvidenceResponse(BaseModel):
    item_key: str
    claim_text: str
    modality: Literal[
        "FACT",
        "PLAN_OR_TARGET",
        "PREDICTION_OR_ESTIMATE",
        "OPINION_OR_EVALUATION",
    ]
    stance: Literal["SUPPORT", "DISPUTE"]
    source: EvidenceSourceResponse
    quote_text: str
    locator: EvidenceLocatorResponse


class RelationEvidencePageResponse(BaseModel):
    items: list[RelationEvidenceResponse]
    trace_count: int = Field(ge=0)
    next_cursor: str | None


class APIError(Exception):
    def __init__(self, status_code: int, code: str, *, retryable: bool) -> None:
        self.status_code = status_code
        self.code = code
        self.retryable = retryable


async def api_error_handler(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, APIError):
        raise error
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "retryable": error.retryable}},
    )


async def validation_error_handler(
    _request: Request, _error: Exception
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "INVALID_REQUEST", "retryable": False}},
    )


def _resource_id(value: str) -> int:
    resource_id = int(value)
    if resource_id > _MAX_BIGINT:
        raise APIError(422, "INVALID_REQUEST", retryable=False)
    return resource_id


@router.post(
    "/source-intake",
    response_model=SourceIntakeResponse,
    status_code=201,
    responses={
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def create_source_intake(
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    publisher_name: Annotated[str | None, Form()] = None,
    original_language: Annotated[str | None, Form()] = None,
    author_text: Annotated[str | None, Form()] = None,
    canonical_url: Annotated[str | None, Form()] = None,
    published_at: Annotated[str | None, Form()] = None,
) -> SourceIntakeResponse:
    engine = get_engine()
    try:
        payload = await file.read(MAX_UPLOAD_BYTES + 1)
        result = materialize_uploaded_source(
            engine,
            payload=payload,
            metadata=SourceIntakeMetadata(
                filename=file.filename or "upload.txt",
                title=title,
                publisher_name=publisher_name or "사용자 업로드",
                author_text=author_text,
                original_language=original_language or "ko",
                canonical_url=canonical_url,
                published_at=parse_published_at(published_at),
            ),
            content_type=file.content_type,
        )
    except SourceIntakeError as error:
        raise APIError(error.status_code, error.code, retryable=False) from error
    finally:
        await file.close()
    processing_job = result.processing_job
    if result.processing_job.status == "QUEUED":
        job_id = result.processing_job.source_processing_job_id
        if not try_submit_source_processing(job_id):
            with engine.begin() as connection:
                stopped = finish_processing_job(
                    connection,
                    job_id=job_id,
                    status="INTERRUPTED",
                    stage="INTERRUPTED",
                    error_code="SOURCE_PROCESSING_DISABLED",
                )
            if stopped is not None:
                processing_job = stopped
    return SourceIntakeResponse(
        source_document_id=str(result.write.source_document_id),
        processing_job_id=str(processing_job.source_processing_job_id),
        version_no=result.write.version_no,
        status=processing_job.status,
        stage=processing_job.stage,
        normalized_body=result.normalized_body,
        body_hash=result.body_hash,
        canonical_url=result.canonical_url,
    )


@router.post(
    "/jev-validation",
    response_model=JevValidationResponse,
    responses={
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def validate_source_with_jev(
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form()] = None,
    publisher_name: Annotated[str | None, Form()] = None,
) -> JevValidationResponse:
    started = time.monotonic()
    try:
        payload = await file.read(MAX_UPLOAD_BYTES + 1)
        body = extract_normalized_body(
            payload,
            filename=file.filename or "upload.txt",
            content_type=file.content_type,
        )
        spans = split_source_spans(body)
        if not spans:
            raise SourceIntakeError("SOURCE_EMPTY", "본문을 추출하지 못했습니다.")
        if (
            len(body) > _JEV_DEMO_MAX_BODY_CHARS
            or len(spans) > jev_selection.SPAN_BATCH_SIZE
        ):
            raise SourceIntakeError(
                "JEV_DEMO_INPUT_LIMIT",
                "JEV 시연은 한 번의 판정 호출에 들어가는 문서만 지원합니다.",
            )
        body_hash = sha256(body.encode()).hexdigest()
        api_key = SecretStr(
            os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY", "")
        )
        resolved_title = (title or "").strip() or FilePath(
            file.filename or "upload.txt"
        ).stem
        publisher = (publisher_name or "").strip() or "사용자 업로드"
        result = jev_selection.select_body_sources(
            spans,
            title=resolved_title,
            publisher=publisher,
            api_key=api_key,
        )
    except SourceIntakeError as error:
        raise APIError(error.status_code, error.code, retryable=False) from error
    except jev_selection.JevSelectionError as error:
        if error.code != "PROMPT_INJECTION_DETECTED":
            raise APIError(
                503,
                error.code,
                retryable=error.transient,
            ) from error
        return JevValidationResponse(
            status="BLOCKED",
            reason=error.code,
            model=jev_selection.MODEL,
            policy_version=jev_selection.POLICY_VERSION,
            body_hash=body_hash,
            drop_threshold=jev_selection.DROP_THRESHOLD,
            injection_threshold=jev_selection.INJECTION_THRESHOLD,
            spans_total=len(spans),
            spans_kept=0,
            spans_dropped=0,
            elapsed_seconds=time.monotonic() - started,
            decisions=[],
        )
    finally:
        await file.close()
    selected = set(result.selection.source_ids)
    span_text = {span.source_id: span.quote for span in spans}
    decisions = [
        JevSpanResponse(
            source_id=decision.source_id,
            text=span_text[decision.source_id],
            drop_probability=decision.drop_probability,
            decision="KEEP" if decision.source_id in selected else "DROP",
        )
        for decision in result.decisions
    ]
    return JevValidationResponse(
        status="PASSED",
        reason=None,
        model=jev_selection.MODEL,
        policy_version=jev_selection.POLICY_VERSION,
        body_hash=body_hash,
        drop_threshold=jev_selection.DROP_THRESHOLD,
        injection_threshold=jev_selection.INJECTION_THRESHOLD,
        spans_total=len(spans),
        spans_kept=len(selected),
        spans_dropped=len(spans) - len(selected),
        calls=result.calls,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        elapsed_seconds=time.monotonic() - started,
        decisions=decisions,
    )


@router.get(
    "/source-intake/{source_document_id}",
    response_model=SourceIntakeStatusResponse,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def read_source_intake_status(
    source_document_id: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
    session: Annotated[Session, Depends(open_read_session)],
    processing_job_id: Annotated[
        str | None, Query(pattern=r"^[1-9][0-9]{0,18}$")
    ] = None,
) -> SourceIntakeStatusResponse:
    document_id = _resource_id(source_document_id)
    document = (
        session.execute(
            sa.select(
                schema.source_document.c.source_document_id,
                schema.source_document.c.body_hash,
            ).where(schema.source_document.c.source_document_id == document_id)
        )
        .mappings()
        .one_or_none()
    )
    if document is None:
        raise APIError(404, "SOURCE_DOCUMENT_NOT_FOUND", retryable=False)
    job = (
        latest_processing_job(session.connection(), source_document_id=document_id)
        if processing_job_id is None
        else get_processing_job(
            session.connection(), job_id=_resource_id(processing_job_id)
        )
    )
    if processing_job_id is not None and (
        job is None or job.source_document_id != document_id
    ):
        raise APIError(404, "SOURCE_PROCESSING_JOB_NOT_FOUND", retryable=False)
    return SourceIntakeStatusResponse(
        source_document_id=str(document["source_document_id"]),
        processing_job_id=(None if job is None else str(job.source_processing_job_id)),
        body_hash=bytes(document["body_hash"]).hex(),
        status="STORED" if job is None else job.status,
        stage=None if job is None else job.stage,
        extraction_task_id=(
            None
            if job is None or job.extraction_task_id is None
            else str(job.extraction_task_id)
        ),
        promotion_batch_id=(
            None
            if job is None or job.promotion_batch_id is None
            else str(job.promotion_batch_id)
        ),
        error_code=None if job is None else job.error_code,
        elapsed_seconds=(
            None if job is None else elapsed_seconds(job, now=datetime.now(UTC))
        ),
        created_at=None if job is None else job.created_at,
        started_at=None if job is None else job.started_at,
        finished_at=None if job is None else job.finished_at,
    )


@router.get(
    "/exploration/{center_node_id}",
    response_model=ExplorationResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_exploration(
    center_node_id: Annotated[
        str,
        Path(pattern=r"^[1-9][0-9]{0,18}$"),
    ],
    time_window: Annotated[TimeWindow, Query()],
    session: Annotated[Session, Depends(open_read_session)],
) -> ExplorationResponse:
    try:
        result = get_exploration(
            session,
            _resource_id(center_node_id),
            time_window,
        )
    except ExplorationNotFoundError as error:
        raise APIError(404, "NODE_NOT_FOUND", retryable=False) from error
    except PublicationNotReadyError as error:
        raise APIError(503, "PUBLICATION_NOT_READY", retryable=True) from error

    return ExplorationResponse(
        center_node_id=str(result.center_node_id),
        context_text=result.context_text,
        graph=GraphResponse(
            nodes=[
                GraphNodeResponse(
                    node_id=str(node.node_id),
                    name=node.name,
                    node_type=NodeTypeResponse(
                        code=node.node_type.code,
                        display_name=node.node_type.display_name,
                    ),
                    tier=node.tier,
                    activity_evidence_group_count=node.activity_evidence_group_count,
                )
                for node in result.graph.nodes
            ],
            relations=[
                GraphRelationResponse(
                    relation_id=str(relation.relation_id),
                    source_node_id=str(relation.source_node_id),
                    target_node_id=str(relation.target_node_id),
                    relation_type_display_name=relation.relation_type_display_name,
                    directionality=relation.directionality,
                    supporting_evidence_group_count=(
                        relation.supporting_evidence_group_count
                    ),
                    has_conflict=relation.has_conflict,
                )
                for relation in result.graph.relations
            ],
        ),
        recommendations=[
            RecommendationResponse(
                target_node=RecommendationNodeResponse(
                    node_id=str(recommendation.target_node.node_id),
                    name=recommendation.target_node.name,
                    node_type=NodeTypeResponse(
                        code=recommendation.target_node.node_type.code,
                        display_name=recommendation.target_node.node_type.display_name,
                    ),
                ),
                path=[
                    RelationPathResponse(
                        relation_id=str(edge.relation_id),
                        source_node_id=str(edge.source_node_id),
                        target_node_id=str(edge.target_node_id),
                        source_node_name=next(
                            n.name
                            for n in result.graph.nodes
                            if n.node_id == edge.source_node_id
                        ),
                        target_node_name=next(
                            n.name
                            for n in result.graph.nodes
                            if n.node_id == edge.target_node_id
                        ),
                        relation_type_display_name=edge.relation_type_display_name,
                        directionality=edge.directionality,
                        supporting_evidence_group_count=edge.supporting_evidence_group_count,
                        has_conflict=edge.has_conflict,
                    )
                    for edge in recommendation.path
                ],
                reason_code=recommendation.reason_code,
                via_node_id=(
                    str(recommendation.via_node_id)
                    if recommendation.via_node_id is not None
                    else None
                ),
                supporting_evidence_group_count=(
                    recommendation.supporting_evidence_group_count
                ),
            )
            for recommendation in result.recommendations
        ],
        followup_questions=[
            FollowupQuestionResponse(
                slot=question.slot,
                question_text=question.question_text,
                target_node_id=str(question.target_node_id),
            )
            for question in result.followup_questions
        ],
    )


@router.get(
    "/exploration/{center_node_id}/peripheral",
    response_model=PeripheralResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_peripheral_nodes(
    center_node_id: Annotated[
        str,
        Path(pattern=r"^[1-9][0-9]{0,18}$"),
    ],
    time_window: Annotated[TimeWindow, Query()],
    session: Annotated[Session, Depends(open_read_session)],
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PeripheralResponse:
    try:
        result = list_peripheral_nodes(
            session,
            _resource_id(center_node_id),
            time_window,
            cursor=cursor,
            limit=limit,
        )
    except InvalidCursorError as error:
        raise APIError(422, "INVALID_REQUEST", retryable=False) from error
    except ExplorationNotFoundError as error:
        raise APIError(404, "NODE_NOT_FOUND", retryable=False) from error
    except PublicationNotReadyError as error:
        raise APIError(503, "PUBLICATION_NOT_READY", retryable=True) from error

    return PeripheralResponse(
        graph=PeripheralGraphResponse(
            nodes=[
                PeripheralNodeResponse(
                    node_id=str(node.node_id),
                    name=node.name,
                    node_type=NodeTypeResponse(
                        code=node.node_type.code,
                        display_name=node.node_type.display_name,
                    ),
                    tier="AMBIENT",
                    activity_evidence_group_count=node.activity_evidence_group_count,
                )
                for node in result.graph.nodes
            ],
            relations=[
                GraphRelationResponse(
                    relation_id=str(relation.relation_id),
                    source_node_id=str(relation.source_node_id),
                    target_node_id=str(relation.target_node_id),
                    relation_type_display_name=relation.relation_type_display_name,
                    directionality=relation.directionality,
                    supporting_evidence_group_count=(
                        relation.supporting_evidence_group_count
                    ),
                    has_conflict=relation.has_conflict,
                )
                for relation in result.graph.relations
            ],
        ),
        next_cursor=result.next_cursor,
    )


@router.get(
    "/nodes/search",
    response_model=SearchResponse,
    responses={422: {"model": ErrorResponse}},
)
def read_node_search(
    q: Annotated[str, Query(min_length=1)],
    session: Annotated[Session, Depends(open_read_session)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> SearchResponse:
    try:
        results = search_nodes(session, q, limit)
    except InvalidSearchQueryError as error:
        raise APIError(422, "INVALID_REQUEST", retryable=False) from error

    return SearchResponse(
        items=[
            SearchResultResponse(
                node_id=str(result.node_id),
                name=result.name,
                node_type=NodeTypeResponse(
                    code=result.node_type.code,
                    display_name=result.node_type.display_name,
                ),
            )
            for result in results
        ]
    )


@router.get(
    "/nodes/{node_id}/relations",
    response_model=NodeRelationsResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_node_relations(
    node_id: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
    time_window: Annotated[TimeWindow, Query()],
    session: Annotated[Session, Depends(open_read_session)],
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> NodeRelationsResponse:
    try:
        result = list_node_relations(
            session,
            _resource_id(node_id),
            time_window,
            cursor=cursor,
            limit=limit,
        )
    except InvalidCursorError as error:
        raise APIError(422, "INVALID_REQUEST", retryable=False) from error
    except NodeRelationsNotFoundError as error:
        raise APIError(404, "NODE_NOT_FOUND", retryable=False) from error
    except PublicationNotReadyError as error:
        raise APIError(503, "PUBLICATION_NOT_READY", retryable=True) from error

    return NodeRelationsResponse(
        items=[
            NodeRelationResponse(
                relation_id=str(item.relation_id),
                source_node_id=str(item.source_node_id),
                target_node_id=str(item.target_node_id),
                directionality=item.directionality,
                other_node=RelatedNodeResponse(
                    node_id=str(item.other_node.node_id),
                    name=item.other_node.name,
                    node_type=NodeTypeResponse(
                        code=item.other_node.node_type.code,
                        display_name=item.other_node.node_type.display_name,
                    ),
                ),
                relation_type_display_name=item.relation_type_display_name,
                supporting_evidence_group_count=(item.supporting_evidence_group_count),
                has_conflict=item.has_conflict,
            )
            for item in result.items
        ],
        next_cursor=result.next_cursor,
    )


@router.get(
    "/relations/{relation_id}/evidence",
    response_model=RelationEvidencePageResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def read_relation_evidence(
    relation_id: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
    time_window: Annotated[TimeWindow, Query()],
    session: Annotated[Session, Depends(open_read_session)],
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> RelationEvidencePageResponse:
    try:
        result = list_relation_evidence(
            session,
            _resource_id(relation_id),
            time_window,
            cursor=cursor,
            limit=limit,
        )
    except InvalidCursorError as error:
        raise APIError(422, "INVALID_REQUEST", retryable=False) from error
    except RelationEvidenceNotFoundError as error:
        raise APIError(404, "RELATION_NOT_FOUND", retryable=False) from error

    return RelationEvidencePageResponse(
        items=[
            RelationEvidenceResponse(
                item_key=item.item_key,
                claim_text=item.claim_text,
                modality=item.modality,
                stance=item.stance,
                source=EvidenceSourceResponse(
                    title=item.source.title,
                    publisher_name=item.source.publisher_name,
                    published_at=item.source.published_at,
                    published_precision=item.source.published_precision,
                    canonical_url=item.source.canonical_url,
                ),
                quote_text=item.quote_text,
                locator=EvidenceLocatorResponse(
                    paragraph_number=item.locator.paragraph_number,
                    start_char=item.locator.start_char,
                    end_char=item.locator.end_char,
                ),
            )
            for item in result.items
        ],
        trace_count=result.trace_count,
        next_cursor=result.next_cursor,
    )


class InsightItemResponse(BaseModel):
    insight_id: str
    slot: int = Field(ge=1, le=3)
    title: str
    evidence_group_count: int = Field(ge=0)


class InsightsResponse(BaseModel):
    items: list[InsightItemResponse]


class InsightTraceResponse(BaseModel):
    source: EvidenceSourceResponse
    quote_text: str
    locator: EvidenceLocatorResponse


class InsightClaimResponse(BaseModel):
    claim_id: str
    claim_text: str
    role: Literal["KEY_CLAIM", "SUPPORTING_CLAIM", "CONTRASTING_CLAIM"]
    traces: list[InsightTraceResponse]


class InsightResponse(InsightItemResponse):
    summary: str
    synthesis: str
    caveat: str
    claims: list[InsightClaimResponse]


@router.get(
    "/nodes/{node_id}/insights",
    response_model=InsightsResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_node_insights(
    node_id: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
    time_window: TimeWindow,
    session: Annotated[Session, Depends(open_read_session)],
) -> InsightsResponse:
    try:
        items = list_node_insights(session, _resource_id(node_id), time_window)
    except ExplorationNotFoundError as error:
        raise APIError(404, "NODE_NOT_FOUND", retryable=False) from error
    except PublicationNotReadyError as error:
        raise APIError(503, "PUBLICATION_NOT_READY", retryable=True) from error
    return InsightsResponse(
        items=[
            InsightItemResponse(
                insight_id=str(item.node_insight_id),
                slot=item.slot,
                title=item.title,
                evidence_group_count=item.evidence_group_count,
            )
            for item in items
        ]
    )


@router.get(
    "/insights/{insight_id}",
    response_model=InsightResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def read_insight(
    insight_id: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
    session: Annotated[Session, Depends(open_read_session)],
) -> InsightResponse:
    try:
        item, rows = get_insight(session, _resource_id(insight_id))
    except InsightNotFoundError as error:
        raise APIError(404, "INSIGHT_NOT_FOUND", retryable=False) from error
    except PublicationNotReadyError as error:
        raise APIError(503, "PUBLICATION_NOT_READY", retryable=True) from error
    claims: dict[int, InsightClaimResponse] = {}
    for row in rows:
        claim = claims.setdefault(
            row.claim_id,
            InsightClaimResponse(
                claim_id=str(row.claim_id),
                claim_text=row.claim_text,
                role=row.role,
                traces=[],
            ),
        )
        claim.traces.append(
            InsightTraceResponse(
                source=EvidenceSourceResponse(
                    title=row.title,
                    publisher_name=row.publisher_name,
                    published_at=row.published_at,
                    published_precision=row.published_precision,
                    canonical_url=row.canonical_url,
                ),
                quote_text=row.quote_text,
                locator=EvidenceLocatorResponse(
                    paragraph_number=row.paragraph_number,
                    start_char=row.start_char,
                    end_char=row.end_char,
                ),
            )
        )
    return InsightResponse(
        insight_id=str(item.node_insight_id),
        slot=item.slot,
        title=item.title,
        evidence_group_count=item.evidence_group_count,
        summary=item.summary_text,
        synthesis=item.synthesis_text,
        caveat=item.caveat_text,
        claims=list(claims.values()),
    )
