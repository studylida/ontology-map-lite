from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ontology_map.api import (
    APIError,
    ErrorResponse,
    GraphNodeResponse,
    GraphRelationResponse,
    GraphResponse,
    NodeTypeResponse,
)
from ontology_map.db.session import open_read_session
from ontology_map.db.topic_references import list_topic_references
from ontology_map.exploration import TimeWindow
from ontology_map.topic_exploration import (
    TopicExplorationNotFoundError,
    get_topic_exploration,
)

router = APIRouter(prefix="/api/v1")
_MAX_BIGINT = 9_223_372_036_854_775_807


class TopicReferenceResponse(BaseModel):
    node_id: str
    topic_code: str
    canonical_display_name: str
    is_active: bool


class TopicReferenceListResponse(BaseModel):
    items: list[TopicReferenceResponse]


class TopicExplorationResponse(BaseModel):
    topic: TopicReferenceResponse
    time_window: TimeWindow
    total_public_membership_count: int = Field(ge=0)
    recent_member_count: int = Field(ge=0)
    recent_activity_evidence_group_count: int = Field(ge=0)
    graph: GraphResponse


def _resource_id(value: str) -> int:
    resource_id = int(value)
    if resource_id > _MAX_BIGINT:
        raise APIError(422, "INVALID_REQUEST", retryable=False)
    return resource_id


def _topic_response(
    *,
    node_id: int,
    topic_code: str,
    canonical_display_name: str,
    is_active: bool,
) -> TopicReferenceResponse:
    return TopicReferenceResponse(
        node_id=str(node_id),
        topic_code=topic_code,
        canonical_display_name=canonical_display_name,
        is_active=is_active,
    )


@router.get("/topics", response_model=TopicReferenceListResponse)
def read_topic_references(
    session: Annotated[Session, Depends(open_read_session)],
) -> TopicReferenceListResponse:
    return TopicReferenceListResponse(
        items=[
            _topic_response(
                node_id=row.node_id,
                topic_code=row.topic_code,
                canonical_display_name=row.canonical_display_name,
                is_active=row.is_active,
            )
            for row in list_topic_references(session)
        ]
    )


@router.get(
    "/topics/{topic_node_id}/exploration",
    response_model=TopicExplorationResponse,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def read_topic_exploration(
    topic_node_id: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
    time_window: Annotated[TimeWindow, Query()],
    session: Annotated[Session, Depends(open_read_session)],
) -> TopicExplorationResponse:
    try:
        result = get_topic_exploration(
            session,
            _resource_id(topic_node_id),
            time_window,
        )
    except TopicExplorationNotFoundError as error:
        raise APIError(404, "TOPIC_NOT_FOUND", retryable=False) from error

    return TopicExplorationResponse(
        topic=_topic_response(
            node_id=result.topic.node_id,
            topic_code=result.topic.topic_code,
            canonical_display_name=result.topic.canonical_display_name,
            is_active=result.topic.is_active,
        ),
        time_window=result.time_window,
        total_public_membership_count=result.total_public_membership_count,
        recent_member_count=result.recent_member_count,
        recent_activity_evidence_group_count=(
            result.recent_activity_evidence_group_count
        ),
        graph=GraphResponse(
            nodes=[
                GraphNodeResponse(
                    node_id=str(graph_node.node_id),
                    name=graph_node.name,
                    node_type=NodeTypeResponse(
                        code=graph_node.node_type.code,
                        display_name=graph_node.node_type.display_name,
                    ),
                    tier=graph_node.tier,
                    activity_evidence_group_count=(
                        graph_node.activity_evidence_group_count
                    ),
                )
                for graph_node in result.graph.nodes
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
    )
