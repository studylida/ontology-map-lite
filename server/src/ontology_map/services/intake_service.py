"""이질적 데이터 수용 및 지식그래프 자동 적재 서비스."""

from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from ontology_map.db.schema import Claim, Classification, Document, Edge, Relation
from ontology_map.services.entity_resolution import resolve_or_create_node


def get_or_create_classification(session: Session, code: str) -> Classification:
    """분류 코드가 DB에 없으면 실시간 자동 등록(승격)합니다."""
    clean_code = code.strip().upper()
    stmt = select(Classification).where(Classification.code == clean_code)
    cls_obj = session.execute(stmt).scalar_one_or_none()

    if not cls_obj:
        cls_obj = Classification(
            code=clean_code,
            display_name=clean_code.capitalize(),
            is_active=True,
        )
        session.add(cls_obj)
        session.flush()

    return cls_obj


def get_or_create_relation(session: Session, code: str) -> Relation:
    """관계 코드가 DB에 없으면 실시간 자동 등록(승격)합니다."""
    clean_code = code.strip().upper()
    stmt = select(Relation).where(Relation.code == clean_code)
    rel_obj = session.execute(stmt).scalar_one_or_none()

    if not rel_obj:
        rel_obj = Relation(
            code=clean_code,
            display_name=clean_code,
            is_directed=True,
            is_active=True,
        )
        session.add(rel_obj)
        session.flush()

    return rel_obj


def process_intake(session: Session, payload: Any) -> dict[str, Any]:
    """동기 프로젝트의 최종 분석 산출물을 8개 테이블에 정규화하여 적재합니다."""
    
    # 1. 문서(Document) 메타정보가 있으면 저장
    doc = None
    if payload.document_title or payload.document_uri:
        doc = Document(
            title=payload.document_title or f"External Document ({payload.source_project})",
            normalized_content=payload.document_content or "",
            source_uri=payload.document_uri,
            metadata_json=payload.raw_metadata or {},
            status="COMPLETED",
        )
        session.add(doc)
        session.flush()

    # 2. 동기 프로젝트가 추출해 준 근거(Claim) 목록이 있으면 저장
    claims_created_count = 0
    if doc and payload.claims:
        for c in payload.claims:
            claim_obj = Claim(
                document_id=doc.id,
                quote_text=c.quote,
                statement=c.claim_text or c.quote,
            )
            session.add(claim_obj)
            claims_created_count += 1

    # 3. 노드(Node) 처리: 자동 승격 및 엔티티 해소(중복 방지)
    node_name_map = {}
    for n in payload.nodes:
        cls_obj = get_or_create_classification(session, n.classification)
        node = resolve_or_create_node(session, n.name, cls_obj.code, n.description, n.properties)
        node_name_map[n.name] = node

    # 4. 엣지(Edge) 처리: 노드 간 연결선 생성
    edges_created_count = 0
    for e in payload.edges:
        source_node = node_name_map.get(e.source_name)
        target_node = node_name_map.get(e.target_name)

        if source_node and target_node:
            rel_obj = get_or_create_relation(session, e.relation)

            # 이미 동일한 엣지가 있는지 확인
            stmt_edge = select(Edge).where(
                Edge.source_node_id == source_node.id,
                Edge.target_node_id == target_node.id,
                Edge.relation_id == rel_obj.id,
            )
            existing_edge = session.execute(stmt_edge).scalar_one_or_none()

            if not existing_edge:
                new_edge = Edge(
                    source_node_id=source_node.id,
                    target_node_id=target_node.id,
                    relation_id=rel_obj.id,
                    properties=e.properties or {},
                )
                session.add(new_edge)
                edges_created_count += 1

    return {
        "status": "success",
        "source_project": payload.source_project,
        "document_id": doc.id if doc else None,
        "nodes_created": len(payload.nodes),
        "edges_created": edges_created_count,
        "claims_created": claims_created_count,
    }
