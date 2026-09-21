from datetime import UTC, datetime
from unittest.mock import Mock

from ontology_map import exploration as exploration_service
from ontology_map import topic_api
from ontology_map.db.exploration import (
    AdjacencyRow,
    CenterRow,
    FollowupRow,
    NodeRow,
)
from ontology_map.db.topic_references import TopicReferenceRow
from ontology_map.exploration import (
    Graph,
    GraphNode,
    GraphRelation,
    NodeType,
    TimeWindow,
)
from ontology_map.topic_exploration import TopicExploration, TopicReference

NOW = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)


def _direct_row(node_id: int) -> AdjacencyRow:
    return AdjacencyRow(
        owner_node_id=1,
        other_node=NodeRow(
            node_id=node_id,
            name=f"node-{node_id}",
            node_type_code="COMPANY",
            node_type_display_name="회사",
        ),
        relation_id=1000 + node_id,
        source_node_id=1,
        target_node_id=node_id,
        relation_type_display_name="직접 관계",
        directionality="DIRECTED",
        evidence_group_ids=(node_id,),
        has_conflict=False,
    )


def test_general_exploration_caps_direct_neighbors_at_24(monkeypatch) -> None:
    rows = [_direct_row(node_id) for node_id in range(2, 32)]

    monkeypatch.setattr(exploration_service, "is_public_node", lambda *_args: True)
    monkeypatch.setattr(
        exploration_service,
        "get_center",
        lambda *_args: CenterRow(
            node_id=1,
            name="center",
            node_type_code="COMPANY",
            node_type_display_name="회사",
            node_context_id=1,
            context_text="context",
        ),
    )

    def list_adjacencies(_session, node_ids, *_bounds):
        ids = set(node_ids)
        if ids == {1}:
            return rows
        if 1 in ids:
            return [row for row in rows if row.other_node.node_id in ids]
        return []

    monkeypatch.setattr(exploration_service, "list_adjacencies", list_adjacencies)
    monkeypatch.setattr(
        exploration_service,
        "list_topic_adjacencies_for_members",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        exploration_service,
        "get_activity_counts",
        lambda _session, node_ids, *_bounds: {node_id: 1 for node_id in node_ids},
    )
    monkeypatch.setattr(exploration_service, "list_ambient_nodes", lambda *_args: [])
    monkeypatch.setattr(
        exploration_service,
        "list_followups",
        lambda *_args: [
            FollowupRow(slot=1, question_text="q1", target_node_id=2),
            FollowupRow(slot=2, question_text="q2", target_node_id=3),
        ],
    )

    result = exploration_service.get_exploration(
        Mock(),
        1,
        TimeWindow.RECENT_90_DAYS,
        now=NOW,
    )

    direct_nodes = [node for node in result.graph.nodes if node.tier == "DIRECT"]
    assert exploration_service.MAX_DIRECT_NODES == 24
    assert len(direct_nodes) == 24
    assert [node.node_id for node in direct_nodes] == list(range(2, 26))
    assert all(node.tier != "TWO_HOP" for node in result.graph.nodes)


def test_topic_api_lists_active_and_inactive_references(monkeypatch) -> None:
    rows = [
        TopicReferenceRow(
            node_id=77,
            topic_code="SEMICONDUCTOR",
            canonical_display_name="반도체",
            is_active=True,
        ),
        TopicReferenceRow(
            node_id=88,
            topic_code="INVESTMENT",
            canonical_display_name="투자",
            is_active=False,
        ),
    ]
    monkeypatch.setattr(topic_api, "list_topic_references", Mock(return_value=rows))

    response = topic_api.read_topic_references(Mock())

    assert [item.node_id for item in response.items] == ["77", "88"]
    assert [item.canonical_display_name for item in response.items] == [
        "반도체",
        "투자",
    ]
    assert [item.is_active for item in response.items] == [True, False]


def test_topic_api_exposes_lightweight_counts_and_string_ids(monkeypatch) -> None:
    result = TopicExploration(
        topic=TopicReference(
            node_id=77,
            topic_code="SEMICONDUCTOR",
            canonical_display_name="반도체",
            is_active=True,
        ),
        time_window=TimeWindow.RECENT_90_DAYS,
        total_public_membership_count=3,
        recent_member_count=2,
        recent_activity_evidence_group_count=4,
        graph=Graph(
            nodes=[
                GraphNode(
                    node_id=77,
                    name="반도체",
                    node_type=NodeType(code="TOPIC", display_name="주제"),
                    tier="CENTER",
                    activity_evidence_group_count=4,
                ),
                GraphNode(
                    node_id=10,
                    name="회사 A",
                    node_type=NodeType(code="COMPANY", display_name="회사"),
                    tier="DIRECT",
                    activity_evidence_group_count=2,
                ),
            ],
            relations=[
                GraphRelation(
                    relation_id=88,
                    source_node_id=10,
                    target_node_id=77,
                    relation_type_display_name="주제 분류",
                    directionality="DIRECTED",
                    supporting_evidence_group_count=2,
                    has_conflict=False,
                )
            ],
        ),
    )
    monkeypatch.setattr(topic_api, "get_topic_exploration", Mock(return_value=result))

    response = topic_api.read_topic_exploration(
        "77",
        TimeWindow.RECENT_90_DAYS,
        Mock(),
    )

    assert response.topic.node_id == "77"
    assert response.total_public_membership_count == 3
    assert response.recent_member_count == 2
    assert response.recent_activity_evidence_group_count == 4
    assert response.graph.nodes[0].node_id == "77"
    assert response.graph.relations[0].relation_id == "88"
