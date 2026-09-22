"""엔티티 식별 및 노드 생성 서비스."""

from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from ontology_map.db.schema import Classification, Node


def resolve_or_create_node(
    session: Session,
    name: str,
    classification_code: str,
    description: Optional[str] = None,
    properties: Optional[dict[str, Any]] = None,
) -> Node:
    """엔티티 이름을 기준으로 노드를 조회하고, 없으면 신규 생성합니다."""
    
    # 1. Classification 확인
    stmt_cls = select(Classification).where(Classification.code == classification_code)
    classification = session.execute(stmt_cls).scalar_one_or_none()
    if not classification:
        raise ValueError(f"존재하지 않는 Classification 코드입니다: {classification_code}")

    # 2. 이름(name)으로 기존 Node 조회
    stmt_node = select(Node).where(Node.name == name)
    node = session.execute(stmt_node).scalar_one_or_none()

    # 3. 노드가 없으면 새로 생성
    if node is None:
        # Node의 컬럼: name, classification_id, description, properties, claim_ids
        node = Node(
            name=name,
            classification_id=classification.id,
            description=description,
            properties=properties or {},
            claim_ids=[],
        )
        session.add(node)
        session.flush()  # DB에 즉시 반영하여 id를 발급받음

    return node
