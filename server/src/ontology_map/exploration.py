from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from sqlalchemy.orm import Session

from ontology_map.db.exploration import (
    AdjacencyRow,
    NodeRow,
    get_activity_counts,
    get_center,
    is_public_node,
    list_adjacencies,
    list_ambient_nodes,
    list_followups,
    list_peripheral_node_rows,
    list_previous_peripheral_ids,
)
from ontology_map.db.topic_exploration import (
    list_topic_adjacencies_for_members,
)
from ontology_map.pagination import InvalidCursorError, decode_cursor, encode_cursor

MAX_DIRECT_NODES = 24
MAX_TWO_HOP_NODES = 18
MAX_THREE_HOP_NODES = 20
MAX_RELATIONS = 60
MAX_RECOMMENDATIONS = 4


class TimeWindow(StrEnum):
    RECENT_90_DAYS = "RECENT_90_DAYS"
    RECENT_1_YEAR = "RECENT_1_YEAR"
    ALL_TIME = "ALL_TIME"

    def start_at(self, end_at: datetime) -> datetime | None:
        if self is TimeWindow.ALL_TIME:
            return None
        days = 90 if self is TimeWindow.RECENT_90_DAYS else 365
        return end_at - timedelta(days=days)


class ExplorationNotFoundError(Exception):
    pass


class PublicationNotReadyError(Exception):
    pass


@dataclass(frozen=True)
class NodeType:
    code: str
    display_name: str


@dataclass(frozen=True)
class GraphNode:
    node_id: int
    name: str
    node_type: NodeType
    tier: str
    activity_evidence_group_count: int


@dataclass(frozen=True)
class GraphRelation:
    relation_id: int
    source_node_id: int
    target_node_id: int
    relation_type_display_name: str
    directionality: Literal["DIRECTED", "SYMMETRIC"]
    supporting_evidence_group_count: int
    has_conflict: bool


@dataclass(frozen=True)
class Graph:
    nodes: list[GraphNode]
    relations: list[GraphRelation]


@dataclass(frozen=True)
class RecommendationNode:
    node_id: int
    name: str
    node_type: NodeType


@dataclass(frozen=True)
class Recommendation:
    target_node: RecommendationNode
    reason_code: str
    via_node_id: int | None
    supporting_evidence_group_count: int | None
    path: list[GraphRelation] = field(default_factory=list)


@dataclass(frozen=True)
class FollowupQuestion:
    slot: int
    question_text: str
    target_node_id: int


@dataclass(frozen=True)
class Exploration:
    center_node_id: int
    context_text: str
    graph: Graph
    recommendations: list[Recommendation]
    followup_questions: list[FollowupQuestion]


@dataclass(frozen=True)
class PeripheralPage:
    graph: Graph
    next_cursor: str | None


@dataclass
class _Candidate:
    node: NodeRow
    evidence_group_ids: set[int] = field(default_factory=set)
    evidence_groups_by_via: dict[int, set[int]] = field(default_factory=dict)

    @property
    def support_count(self) -> int:
        return len(self.evidence_group_ids)

    @property
    def via_node_id(self) -> int | None:
        if not self.evidence_groups_by_via:
            return None
        return min(
            self.evidence_groups_by_via,
            key=lambda node_id: (-len(self.evidence_groups_by_via[node_id]), node_id),
        )


@dataclass
class _Relation:
    relation_id: int
    source_node_id: int
    target_node_id: int
    relation_type_display_name: str
    directionality: Literal["DIRECTED", "SYMMETRIC"]
    evidence_group_ids: set[int] = field(default_factory=set)
    has_conflict: bool = False


def _node_type(node: NodeRow) -> NodeType:
    return NodeType(
        code=node.node_type_code,
        display_name=node.node_type_display_name,
    )


def _collect_candidates(
    rows: list[AdjacencyRow], excluded_node_ids: set[int]
) -> dict[int, _Candidate]:
    candidates: dict[int, _Candidate] = {}
    for row in rows:
        node_id = row.other_node.node_id
        if node_id in excluded_node_ids:
            continue
        candidate = candidates.setdefault(node_id, _Candidate(node=row.other_node))
        evidence_group_ids = set(row.evidence_group_ids)
        candidate.evidence_group_ids.update(evidence_group_ids)
        candidate.evidence_groups_by_via.setdefault(row.owner_node_id, set()).update(
            evidence_group_ids
        )
    return candidates


def _rank_candidates(
    candidates: dict[int, _Candidate], activity_counts: dict[int, int]
) -> list[_Candidate]:
    return sorted(
        candidates.values(),
        key=lambda candidate: (
            -candidate.support_count,
            -activity_counts.get(candidate.node.node_id, 0),
            candidate.node.node_id,
        ),
    )


def _collect_relations(
    rows: list[AdjacencyRow], selected_ids: set[int]
) -> dict[int, _Relation]:
    relations: dict[int, _Relation] = {}
    for row in rows:
        if (
            row.owner_node_id not in selected_ids
            or row.other_node.node_id not in selected_ids
        ):
            continue
        relation = relations.setdefault(
            row.relation_id,
            _Relation(
                relation_id=row.relation_id,
                source_node_id=row.source_node_id,
                target_node_id=row.target_node_id,
                relation_type_display_name=row.relation_type_display_name,
                directionality=row.directionality,
            ),
        )
        relation.evidence_group_ids.update(row.evidence_group_ids)
        relation.has_conflict = relation.has_conflict or row.has_conflict
    return relations


def _best_relation_id(
    relations: dict[int, _Relation], first_node_id: int, second_node_id: int
) -> int | None:
    pair = {first_node_id, second_node_id}
    matching = [
        relation
        for relation in relations.values()
        if {relation.source_node_id, relation.target_node_id} == pair
    ]
    if not matching:
        return None
    return min(
        matching,
        key=lambda relation: (-len(relation.evidence_group_ids), relation.relation_id),
    ).relation_id


def _select_relations(
    relations: dict[int, _Relation],
    center_node_id: int,
    direct: list[_Candidate],
    two_hop: list[_Candidate],
) -> list[_Relation]:
    selected_ids: list[int] = []
    for candidate in direct:
        relation_id = _best_relation_id(
            relations, center_node_id, candidate.node.node_id
        )
        if relation_id is not None and relation_id not in selected_ids:
            selected_ids.append(relation_id)
    for candidate in two_hop:
        via_node_id = candidate.via_node_id
        if via_node_id is None:
            continue
        relation_id = _best_relation_id(relations, via_node_id, candidate.node.node_id)
        if relation_id is not None and relation_id not in selected_ids:
            selected_ids.append(relation_id)

    remaining = sorted(
        (
            relation
            for relation in relations.values()
            if relation.relation_id not in selected_ids
        ),
        key=lambda relation: (-len(relation.evidence_group_ids), relation.relation_id),
    )
    return [relations[relation_id] for relation_id in selected_ids] + remaining


def _recommendations(
    direct: list[_Candidate],
    two_hop: list[_Candidate],
    ambient: list[tuple[NodeRow, int]],
    activity_counts: dict[int, int],
) -> list[Recommendation]:
    preferred: list[tuple[str, _Candidate | tuple[NodeRow, int]]] = [
        *(("DIRECT", candidate) for candidate in direct[:2]),
        *(("TWO_HOP", candidate) for candidate in two_hop[:1]),
        *(("AMBIENT", candidate) for candidate in ambient[:1]),
    ]
    preferred_ids = {
        value.node.node_id if isinstance(value, _Candidate) else value[0].node_id
        for _, value in preferred
    }
    fallback: list[tuple[str, _Candidate | tuple[NodeRow, int]]] = [
        *(("DIRECT", candidate) for candidate in direct),
        *(("TWO_HOP", candidate) for candidate in two_hop),
        *(("AMBIENT", candidate) for candidate in ambient),
    ]
    fallback = [
        item
        for item in fallback
        if (
            item[1].node.node_id
            if isinstance(item[1], _Candidate)
            else item[1][0].node_id
        )
        not in preferred_ids
    ]
    fallback.sort(
        key=lambda item: (
            -(item[1].support_count if isinstance(item[1], _Candidate) else 0),
            -(
                activity_counts.get(item[1].node.node_id, 0)
                if isinstance(item[1], _Candidate)
                else item[1][1]
            ),
            item[1].node.node_id
            if isinstance(item[1], _Candidate)
            else item[1][0].node_id,
        )
    )

    results: list[Recommendation] = []
    for reason_code, value in (preferred + fallback)[:MAX_RECOMMENDATIONS]:
        if isinstance(value, _Candidate):
            target_node = value.node
            results.append(
                Recommendation(
                    target_node=RecommendationNode(
                        node_id=target_node.node_id,
                        name=target_node.name,
                        node_type=_node_type(target_node),
                    ),
                    reason_code=reason_code,
                    via_node_id=value.via_node_id if reason_code == "TWO_HOP" else None,
                    supporting_evidence_group_count=(
                        value.support_count if reason_code == "DIRECT" else None
                    ),
                )
            )
        else:
            target_node = value[0]
            results.append(
                Recommendation(
                    target_node=RecommendationNode(
                        node_id=target_node.node_id,
                        name=target_node.name,
                        node_type=_node_type(target_node),
                    ),
                    reason_code=reason_code,
                    via_node_id=None,
                    supporting_evidence_group_count=None,
                )
            )
    return results


def _recommendation_paths(
    items: list[Recommendation], center_id: int, relations: list[GraphRelation]
) -> list[Recommendation]:
    results = []
    for item in items:
        nodes = [center_id]
        if item.via_node_id is not None:
            nodes.append(item.via_node_id)
        nodes.append(item.target_node.node_id)
        path = []
        if item.reason_code != "AMBIENT":
            for first, second in zip(nodes, nodes[1:], strict=False):
                matches = [
                    r
                    for r in relations
                    if {r.source_node_id, r.target_node_id} == {first, second}
                ]
                if not matches:
                    raise PublicationNotReadyError
                path.append(
                    min(
                        matches,
                        key=lambda r: (
                            -r.supporting_evidence_group_count,
                            r.relation_id,
                        ),
                    )
                )
        results.append(replace(item, path=path))
    return results


def get_exploration(
    session: Session,
    center_node_id: int,
    time_window: TimeWindow,
    *,
    now: datetime | None = None,
) -> Exploration:
    if not is_public_node(session, center_node_id):
        raise ExplorationNotFoundError
    center = get_center(session, center_node_id)
    if center is None:
        raise PublicationNotReadyError

    end_at = now or datetime.now(UTC)
    start_at = time_window.start_at(end_at)

    center_activity = get_activity_counts(
        session, [center_node_id], start_at, end_at
    ).get(center_node_id, 0)
    if center_activity == 0:
        return Exploration(
            center_node_id=center_node_id,
            context_text=center.context_text,
            graph=Graph(
                nodes=[
                    GraphNode(
                        node_id=center.node_id,
                        name=center.name,
                        node_type=_node_type(center),
                        tier="CENTER",
                        activity_evidence_group_count=0,
                    )
                ],
                relations=[],
            ),
            recommendations=[],
            followup_questions=[],
        )

    direct_rows = [
        *list_adjacencies(session, [center_node_id], start_at, end_at),
        *list_topic_adjacencies_for_members(
            session, [center_node_id], start_at, end_at
        ),
    ]
    direct_candidates = _collect_candidates(direct_rows, {center_node_id})
    direct_activity = get_activity_counts(
        session, list(direct_candidates), start_at, end_at
    )
    direct = _rank_candidates(direct_candidates, direct_activity)[:MAX_DIRECT_NODES]

    direct_ids = {candidate.node.node_id for candidate in direct}
    two_hop_rows = list_adjacencies(session, sorted(direct_ids), start_at, end_at)
    two_hop_candidates = _collect_candidates(
        two_hop_rows, {center_node_id, *direct_candidates}
    )
    two_hop_activity = get_activity_counts(
        session, list(two_hop_candidates), start_at, end_at
    )
    two_hop = _rank_candidates(two_hop_candidates, two_hop_activity)[:MAX_TWO_HOP_NODES]

    two_hop_ids = {candidate.node.node_id for candidate in two_hop}
    # 표시 상한에서 제외된 가까운 node를 실제 3단계로 잘못 분류하지 않는다.
    all_two_hop = _collect_candidates(
        list_adjacencies(session, sorted(direct_candidates), start_at, end_at),
        {center_node_id, *direct_candidates},
    )
    three_candidates = _collect_candidates(
        list_adjacencies(session, sorted(two_hop_ids), start_at, end_at),
        {center_node_id, *direct_candidates, *all_two_hop},
    )
    three_activity = get_activity_counts(
        session, list(three_candidates), start_at, end_at
    )
    three_hop = _rank_candidates(three_candidates, three_activity)[:MAX_THREE_HOP_NODES]
    selected_ids = {
        center_node_id,
        *direct_ids,
        *two_hop_ids,
        *(candidate.node.node_id for candidate in three_hop),
    }
    activity_counts = get_activity_counts(
        session, sorted(selected_ids), start_at, end_at
    )
    relation_rows = [
        *list_adjacencies(session, sorted(selected_ids), start_at, end_at),
        *list_topic_adjacencies_for_members(
            session, sorted(selected_ids), start_at, end_at
        ),
    ]
    relations = _collect_relations(relation_rows, selected_ids)
    selected_relations = _select_relations(
        relations, center_node_id, direct, two_hop + three_hop
    )[:MAX_RELATIONS]

    ambient = list_ambient_nodes(
        session,
        sorted(selected_ids),
        start_at,
        end_at,
        MAX_RECOMMENDATIONS,
    )
    all_activity_counts = activity_counts | direct_activity | two_hop_activity
    followups = (
        []
        if time_window is TimeWindow.ALL_TIME
        else list_followups(session, center.node_context_id)
    )

    graph_nodes = [
        GraphNode(
            node_id=center.node_id,
            name=center.name,
            node_type=_node_type(center),
            tier="CENTER",
            activity_evidence_group_count=activity_counts.get(center.node_id, 0),
        ),
        *(
            GraphNode(
                node_id=candidate.node.node_id,
                name=candidate.node.name,
                node_type=_node_type(candidate.node),
                tier="DIRECT",
                activity_evidence_group_count=activity_counts.get(
                    candidate.node.node_id, 0
                ),
            )
            for candidate in direct
        ),
        *(
            GraphNode(
                node_id=candidate.node.node_id,
                name=candidate.node.name,
                node_type=_node_type(candidate.node),
                tier="TWO_HOP",
                activity_evidence_group_count=activity_counts.get(
                    candidate.node.node_id, 0
                ),
            )
            for candidate in two_hop
        ),
    ]
    graph_nodes.extend(
        GraphNode(
            node_id=c.node.node_id,
            name=c.node.name,
            node_type=_node_type(c.node),
            tier="THREE_HOP",
            activity_evidence_group_count=activity_counts.get(c.node.node_id, 0),
        )
        for c in three_hop
    )
    graph_relations = [
        GraphRelation(
            relation_id=relation.relation_id,
            source_node_id=relation.source_node_id,
            target_node_id=relation.target_node_id,
            relation_type_display_name=relation.relation_type_display_name,
            directionality=relation.directionality,
            supporting_evidence_group_count=len(relation.evidence_group_ids),
            has_conflict=relation.has_conflict,
        )
        for relation in selected_relations
    ]
    return Exploration(
        center_node_id=center_node_id,
        context_text=center.context_text,
        graph=Graph(nodes=graph_nodes, relations=graph_relations),
        recommendations=_recommendation_paths(
            _recommendations(direct, two_hop, ambient, all_activity_counts),
            center_node_id,
            graph_relations,
        ),
        followup_questions=[
            FollowupQuestion(
                slot=followup.slot,
                question_text=followup.question_text,
                target_node_id=followup.target_node_id,
            )
            for followup in followups
        ],
    )


def _peripheral_position(
    cursor: str | None,
    center_node_id: int,
    time_window: TimeWindow,
) -> int:
    if cursor is None:
        return 0
    values = decode_cursor(
        cursor,
        kind="exploration_peripheral",
        scope={
            "center_node_id": center_node_id,
            "time_window": time_window.value,
        },
    )
    if len(values) != 1 or type(values[0]) is not int or values[0] <= 0:
        raise InvalidCursorError
    return values[0]


def _connects_page_to_active(
    relation: _Relation,
    page_node_ids: set[int],
    active_node_ids: set[int],
) -> bool:
    return (
        relation.source_node_id in page_node_ids
        and relation.target_node_id in active_node_ids
    ) or (
        relation.target_node_id in page_node_ids
        and relation.source_node_id in active_node_ids
    )


def list_peripheral_nodes(
    session: Session,
    center_node_id: int,
    time_window: TimeWindow,
    *,
    cursor: str | None,
    limit: int,
    now: datetime | None = None,
) -> PeripheralPage:
    after_node_id = _peripheral_position(cursor, center_node_id, time_window)
    end_at = now or datetime.now(UTC)
    start_at = time_window.start_at(end_at)

    # ponytail: 같은 선택 규칙을 재사용한다.
    # 조회 비용이 문제가 되면 활성 graph 계산만 분리한다.
    active_graph = get_exploration(
        session,
        center_node_id,
        time_window,
        now=end_at,
    ).graph
    active_node_ids = {node.node_id for node in active_graph.nodes}
    rows = list_peripheral_node_rows(
        session,
        sorted(active_node_ids),
        start_at,
        end_at,
        after_node_id,
        limit + 1,
    )
    page_rows = rows[:limit]
    page_node_ids = {node.node_id for node, _activity_count in page_rows}
    previous_ids = set(
        list_previous_peripheral_ids(session, sorted(active_node_ids), after_node_id)
    )
    selected_node_ids = active_node_ids | page_node_ids | previous_ids
    relations = _collect_relations(
        list_adjacencies(session, sorted(selected_node_ids), start_at, end_at),
        selected_node_ids,
    )
    page_relations = sorted(
        (
            relation
            for relation in relations.values()
            if _connects_page_to_active(
                relation,
                page_node_ids,
                selected_node_ids,
            )
        ),
        key=lambda relation: relation.relation_id,
    )
    next_cursor = None
    if len(rows) > limit:
        next_cursor = encode_cursor(
            "exploration_peripheral",
            {
                "center_node_id": center_node_id,
                "time_window": time_window.value,
            },
            [page_rows[-1][0].node_id],
        )

    return PeripheralPage(
        graph=Graph(
            nodes=[
                GraphNode(
                    node_id=node.node_id,
                    name=node.name,
                    node_type=_node_type(node),
                    tier="AMBIENT",
                    activity_evidence_group_count=activity_count,
                )
                for node, activity_count in page_rows
            ],
            relations=[
                GraphRelation(
                    relation_id=relation.relation_id,
                    source_node_id=relation.source_node_id,
                    target_node_id=relation.target_node_id,
                    relation_type_display_name=relation.relation_type_display_name,
                    directionality=relation.directionality,
                    supporting_evidence_group_count=len(relation.evidence_group_ids),
                    has_conflict=relation.has_conflict,
                )
                for relation in page_relations
            ],
        ),
        next_cursor=next_cursor,
    )
