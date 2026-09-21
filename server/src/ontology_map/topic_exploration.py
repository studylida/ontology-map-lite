"""Lightweight direct-membership exploration for product Reference Topics."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ontology_map.db.topic_exploration import (
    get_topic_activity,
    get_topic_center,
    list_topic_memberships,
)
from ontology_map.exploration import (
    Graph,
    GraphNode,
    GraphRelation,
    NodeType,
    TimeWindow,
)


class TopicExplorationNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class TopicReference:
    node_id: int
    topic_code: str
    canonical_display_name: str
    is_active: bool


@dataclass(frozen=True)
class TopicExploration:
    topic: TopicReference
    time_window: TimeWindow
    total_public_membership_count: int
    recent_member_count: int
    recent_activity_evidence_group_count: int
    graph: Graph


def get_topic_exploration(
    session: Session,
    topic_node_id: int,
    time_window: TimeWindow,
    *,
    now: datetime | None = None,
) -> TopicExploration:
    center = get_topic_center(session, topic_node_id)
    if center is None:
        raise TopicExplorationNotFoundError

    end_at = now or datetime.now(UTC)
    start_at = time_window.start_at(end_at)
    all_memberships = list_topic_memberships(session, topic_node_id, None, end_at)
    memberships = list_topic_memberships(session, topic_node_id, start_at, end_at)
    activity = get_topic_activity(session, topic_node_id, start_at, end_at)

    members = {row.other_node.node_id: row.other_node for row in memberships}
    graph_nodes = [
        GraphNode(
            node_id=topic_node_id,
            name=center.reference.canonical_display_name,
            node_type=NodeType(
                code="TOPIC", display_name=center.node_type_display_name
            ),
            tier="CENTER",
            activity_evidence_group_count=activity.recent_evidence_group_count,
        ),
        *(
            GraphNode(
                node_id=member.node_id,
                name=member.name,
                node_type=NodeType(
                    code=member.node_type_code,
                    display_name=member.node_type_display_name,
                ),
                tier="DIRECT",
                activity_evidence_group_count=(
                    activity.member_evidence_group_counts.get(member.node_id, 0)
                ),
            )
            for member in (members[node_id] for node_id in sorted(members))
        ),
    ]
    graph_relations = [
        GraphRelation(
            relation_id=row.relation_id,
            source_node_id=row.source_node_id,
            target_node_id=row.target_node_id,
            relation_type_display_name=row.relation_type_display_name,
            directionality=row.directionality,
            supporting_evidence_group_count=len(row.evidence_group_ids),
            has_conflict=row.has_conflict,
        )
        for row in sorted(memberships, key=lambda item: item.relation_id)
    ]
    return TopicExploration(
        topic=TopicReference(
            node_id=center.reference.node_id,
            topic_code=center.reference.topic_code,
            canonical_display_name=center.reference.canonical_display_name,
            is_active=center.reference.is_active,
        ),
        time_window=time_window,
        total_public_membership_count=len(
            {row.other_node.node_id for row in all_memberships}
        ),
        recent_member_count=activity.recent_member_count,
        recent_activity_evidence_group_count=activity.recent_evidence_group_count,
        graph=Graph(nodes=graph_nodes, relations=graph_relations),
    )
