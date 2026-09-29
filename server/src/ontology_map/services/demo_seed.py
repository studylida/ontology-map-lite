"""시연 베이스라인 시드 서비스 (한결정밀, 부산공장, 2024 실적)."""

from typing import Any
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ontology_map.db.schema import Claim, Document, Edge, Node
from ontology_map.services.intake_service import get_or_create_classification, get_or_create_relation
from ontology_map.services.entity_resolution import find_node_by_name


def ensure_demo_seed(session: Session) -> dict[str, Any]:
    """시연 1단계 실행을 위한 기준 데이터('한결정밀', '부산공장', OPERATES 관계, 2024년 100억 매출 실적)를 멱등하게 보장합니다."""
    # 1. 온톨로지 메타 (Classification, Relation)
    cls_company = get_or_create_classification(session, "COMPANY")
    cls_facility = get_or_create_classification(session, "FACILITY")
    rel_operates = get_or_create_relation(session, "OPERATES")
    if rel_operates.display_name != "운영":
        rel_operates.display_name = "운영"

    # 2. 기준 Document
    doc_title = "한결정밀 · 시연 기준 자료"
    doc_content = "한결정밀은 부산공장을 운영한다.\n한결정밀의 2024년 별도 매출 실적은 100억원이다."
    doc = session.execute(
        select(Document).where(Document.title == doc_title)
    ).scalars().first()

    if not doc:
        doc = Document(
            title=doc_title,
            normalized_content=doc_content,
            source_uri="demo://seed/hangyeol-baseline",
            metadata_json={"source_project": "demo-seed", "data_mode": "synthetic_seed"},
            status="COMPLETED",
        )
        session.add(doc)
        session.flush()

    # 3. Claims
    q1 = "한결정밀은 부산공장을 운영한다."
    s1 = "한결정밀은 부산공장을 운영한다."
    c1 = session.execute(
        select(Claim).where(and_(Claim.document_id == doc.id, Claim.quote_text == q1))
    ).scalars().first()
    if not c1:
        c1 = Claim(document_id=doc.id, quote_text=q1, statement=s1)
        session.add(c1)
        session.flush()

    q2 = "한결정밀의 2024년 별도 매출 실적은 100억원이다."
    s2 = "한결정밀의 2024년 별도 매출 실적은 100억원이다."
    c2 = session.execute(
        select(Claim).where(and_(Claim.document_id == doc.id, Claim.quote_text == q2))
    ).scalars().first()
    if not c2:
        c2 = Claim(document_id=doc.id, quote_text=q2, statement=s2)
        session.add(c2)
        session.flush()

    # 4. Nodes
    # 4-1. 한결정밀
    node_company = find_node_by_name(session, "한결정밀")
    company_props = {
        "metrics": {
            "revenue": {
                "period": "2024",
                "value": 100,
                "unit": "억원",
                "currency": "KRW",
                "consolidation": "separate",
                "valueKind": "actual",
            }
        }
    }
    if not node_company:
        node_company = Node(
            name="한결정밀",
            classification_id=cls_company.id,
            description="정밀부품 제조 기업 (시연 기준 기업)",
            properties=company_props,
            claim_ids=[c1.id, c2.id],
        )
        session.add(node_company)
        session.flush()
    else:
        cur_props = dict(node_company.properties or {})
        if "metrics" not in cur_props:
            cur_props["metrics"] = company_props["metrics"]
            node_company.properties = cur_props
        cur_cids = set(node_company.claim_ids or [])
        cur_cids.add(c1.id)
        cur_cids.add(c2.id)
        node_company.claim_ids = sorted(list(cur_cids))

    # 4-2. 부산공장
    node_facility = find_node_by_name(session, "부산공장")
    if not node_facility:
        node_facility = Node(
            name="부산공장",
            classification_id=cls_facility.id,
            description="한결정밀 주요 제조 공장 (시연 기준 시설)",
            properties={},
            claim_ids=[c1.id],
        )
        session.add(node_facility)
        session.flush()
    else:
        cur_cids = set(node_facility.claim_ids or [])
        cur_cids.add(c1.id)
        node_facility.claim_ids = sorted(list(cur_cids))

    # 5. Edge (한결정밀 --OPERATES--> 부산공장)
    edge_operates = session.execute(
        select(Edge).where(
            and_(
                Edge.source_node_id == node_company.id,
                Edge.target_node_id == node_facility.id,
                Edge.relation_id == rel_operates.id,
            )
        )
    ).scalars().first()

    if not edge_operates:
        edge_operates = Edge(
            source_node_id=node_company.id,
            target_node_id=node_facility.id,
            relation_id=rel_operates.id,
            claim_id=c1.id,
            properties={"claim_ids": [c1.id]},
        )
        session.add(edge_operates)
        session.flush()
    else:
        edge_props = dict(edge_operates.properties or {})
        e_cids = set(edge_props.get("claim_ids", []))
        e_cids.add(c1.id)
        edge_props["claim_ids"] = sorted(list(e_cids))
        edge_operates.properties = edge_props
        if not edge_operates.claim_id:
            edge_operates.claim_id = c1.id

    return {
        "status": "success",
        "document_id": doc.id,
        "company_node_id": node_company.id,
        "facility_node_id": node_facility.id,
        "edge_id": edge_operates.id,
        "claim_ids": [c1.id, c2.id],
    }
