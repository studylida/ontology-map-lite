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


def get_node_subgraph(session: Session, node_id: int) -> dict:
    """특정 노드 중심 1-hop 서브그래프(연결된 노드 및 엣지)를 반환합니다."""
    
    # 1. 중심 노드 조회
    center_node = get_node_by_id(session, node_id)
    if not center_node:
        return {"center_node": None, "nodes": [], "edges": []}

    # 2. 중심 노드와 연결된 모든 Edge 조회 (출발 또는 도착이 node_id인 경우)
    # [빈칸 C]: Edge.source_node_id 또는 Edge.target_node_id가 node_id와 같은 조건을 or_() 안에 채워보세요.
    stmt_edges = (
        select(Edge)
        .options(
            joinedload(Edge.relation),
            joinedload(Edge.source_node).joinedload(Node.classification),
            joinedload(Edge.target_node).joinedload(Node.classification),
        )
        .where(
            or_(
                Edge.source_node_id==node_id,  # 출발지가 중심 노드인 경우
                Edge.target_node_id==node_id   # 도착지가 중심 노드인 경우
            )
        )
    )
    edges = session.execute(stmt_edges).scalars().all()

    # 3. 연결된 모든 이웃 노드들을 중복 없이 모으기
    connected_nodes = {center_node.id: center_node}
    edge_list = []

    for edge in edges:
        connected_nodes[edge.source_node_id] = edge.source_node
        connected_nodes[edge.target_node_id] = edge.target_node

        edge_list.append({
            "id": edge.id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "relation_code": edge.relation.code,
            "relation_name": edge.relation.display_name,
            "properties": edge.properties,
        })

    node_list = [
        {
            "id": n.id,
            "name": n.name,
            "classification_code": n.classification.code,
            "classification_name": n.classification.display_name,
            "description": n.description,
            "properties": n.properties,
        }
        for n in connected_nodes.values()
    ]

    return {
        "center_node_id": center_node.id,
        "nodes": node_list,
        "edges": edge_list,
    }
