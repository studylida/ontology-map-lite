"""지식그래프 서브그래프 및 노드 탐색 서비스."""

from typing import Optional
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from ontology_map.db.schema import Edge, Node


def get_node_by_id(session: Session, node_id: int) -> Optional[Node]:
    """특정 노드 단건 조회 (Classification 함께 로딩)."""
    stmt = (
        select(Node)
        .options(joinedload(Node.classification))
        .where(Node.id == node_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_node_subgraph(
    session: Session,
    node_id: int,
    max_hops: int = 4,
    hop_limit: Optional[int] = 60,
) -> dict:
    """특정 노드 중심 1~4 hop 서브그래프를 계층(Tier)과 함께 BFS로 계산하여 반환합니다."""
    center_node = get_node_by_id(session, node_id)
    if not center_node:
        return {
            "center_node_id": None,
            "nodes": [],
            "edges": [],
            "has_omitted": False,
            "omitted_count": 0,
        }

    visited_tiers: dict[int, str] = {center_node.id: "CENTER"}
    nodes_by_id: dict[int, Node] = {center_node.id: center_node}

    current_hop_ids = {center_node.id}
    tier_names = {1: "DIRECT", 2: "TWO_HOP", 3: "THREE_HOP", 4: "AMBIENT"}
    total_omitted_count = 0

    # 1. 너비 우선 탐색 (BFS)으로 1-Hop(DIRECT) ~ 4-Hop(AMBIENT) 이웃 수집
    for hop in range(1, max_hops + 1):
        if not current_hop_ids:
            break

        stmt_edges = (
            select(Edge)
            .options(
                joinedload(Edge.relation),
                joinedload(Edge.source_node).joinedload(Node.classification),
                joinedload(Edge.target_node).joinedload(Node.classification),
            )
            .where(
                or_(
                    Edge.source_node_id.in_(current_hop_ids),
                    Edge.target_node_id.in_(current_hop_ids),
                )
            )
        )
        edges = session.execute(stmt_edges).scalars().all()

        next_hop_dict: dict[int, Node] = {}
        for e in edges:
            other_id = e.target_node_id if e.source_node_id in current_hop_ids else e.source_node_id
            other_node = e.target_node if e.source_node_id in current_hop_ids else e.source_node

            if other_id not in visited_tiers and other_id not in next_hop_dict:
                next_hop_dict[other_id] = other_node

        tier_name = tier_names.get(hop, "AMBIENT")
        total_candidates = len(next_hop_dict)

        # 상한선이 지정되어 있고 초과할 때만 안전하게 조절
        if hop_limit is not None and total_candidates > hop_limit:
            selected_items = list(next_hop_dict.items())[:hop_limit]
            total_omitted_count += total_candidates - hop_limit
        else:
            selected_items = list(next_hop_dict.items())

        current_hop_ids = set()
        for oid, onode in selected_items:
            visited_tiers[oid] = tier_name
            nodes_by_id[oid] = onode
            current_hop_ids.add(oid)

    # 2. 수집된 모든 노드 간의 상호 연결 엣지 조회
    all_node_ids = set(nodes_by_id.keys())
    stmt_all_edges = (
        select(Edge)
        .options(joinedload(Edge.relation))
        .where(
            Edge.source_node_id.in_(all_node_ids),
            Edge.target_node_id.in_(all_node_ids),
        )
    )
    all_edges = session.execute(stmt_all_edges).scalars().all()

    edge_list = []
    tier_rank = {"CENTER": 0, "DIRECT": 1, "TWO_HOP": 2, "THREE_HOP": 3, "AMBIENT": 4}
    inv_rank = {0: "DIRECT", 1: "DIRECT", 2: "TWO_HOP", 3: "THREE_HOP", 4: "AMBIENT"}

    for edge in all_edges:
        s_tier = visited_tiers.get(edge.source_node_id, "DIRECT")
        t_tier = visited_tiers.get(edge.target_node_id, "DIRECT")
        edge_tier_val = max(tier_rank.get(s_tier, 1), tier_rank.get(t_tier, 1))

        edge_list.append({
            "id": edge.id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "relation_code": edge.relation.code,
            "relation_name": edge.relation.display_name,
            "properties": edge.properties or {},
            "tier": inv_rank.get(edge_tier_val, "DIRECT"),
        })

    node_list = [
        {
            "id": n.id,
            "name": n.name,
            "classification_code": n.classification.code,
            "classification_name": n.classification.display_name,
            "description": n.description,
            "properties": n.properties or {},
            "tier": visited_tiers.get(n.id, "DIRECT"),
        }
        for n in nodes_by_id.values()
    ]

    return {
        "center_node_id": center_node.id,
        "nodes": node_list,
        "edges": edge_list,
        "has_omitted": total_omitted_count > 0,
        "omitted_count": total_omitted_count,
    }
