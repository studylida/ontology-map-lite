"""이질적 데이터 수용 및 지식그래프 자동 적재 서비스 (M1/M2 멱등성 및 근거 연결 완성)."""

from typing import Any, Optional
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import Session

from ontology_map.db.schema import Claim, Classification, Document, Edge, Node, Relation
from ontology_map.services.entity_resolution import find_node_by_name, resolve_or_create_node


def get_or_create_classification(session: Session, code: str) -> Classification:
    """분류 코드가 DB에 없으면 실시간 자동 등록(승격)합니다."""
    clean_code = code.strip().upper()
    stmt = select(Classification).where(Classification.code == clean_code)
    cls_obj = session.execute(stmt).scalars().first()

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
    rel_obj = session.execute(stmt).scalars().first()

    if not rel_obj:
        cls_directed = True
        rel_obj = Relation(
            code=clean_code,
            display_name=clean_code,
            is_directed=cls_directed,
            is_active=True,
        )
        session.add(rel_obj)
        session.flush()

    return rel_obj


def process_intake(session: Session, payload: Any) -> dict[str, Any]:
    """분석 산출물을 8개 테이블에 정규화하여 중복 없이 원자적으로 적재합니다."""
    affected_node_ids: set[int] = set()

    # 1. 문서(Document) 조회 또는 신규 생성 (중복 재사용: title + source_uri 또는 동일 본문)
    doc: Optional[Document] = None
    doc_title = payload.document_title or (
        f"External Document ({payload.source_project})" if payload.source_project else None
    )
    doc_uri = payload.document_uri
    doc_content = payload.document_content or ""

    if doc_title or doc_uri or doc_content:
        stmt_doc = None
        if doc_uri:
            stmt_doc = select(Document).where(Document.source_uri == doc_uri)
        elif doc_title and doc_content:
            stmt_doc = select(Document).where(
                and_(
                    Document.title == doc_title,
                    Document.normalized_content == doc_content,
                )
            )
        elif doc_title:
            stmt_doc = select(Document).where(Document.title == doc_title)

        if stmt_doc is not None:
            doc = session.execute(stmt_doc.order_by(Document.id.desc())).scalars().first()

        if not doc:
            # 4절 상한 검증: 누적 문서 1,000개
            doc_count = session.execute(select(func.count(Document.id))).scalar() or 0
            if doc_count >= 1000:
                raise ValueError("누적 문서 상한선(1,000개)에 도달하여 신규 문서를 생성할 수 없습니다.")

            doc = Document(
                title=doc_title or "무제 문서",
                normalized_content=doc_content,
                source_uri=doc_uri,
                metadata_json=payload.raw_metadata or {},
                status="COMPLETED",
            )
            session.add(doc)
            session.flush()

    # 2. 근거(Claim) 적재 및 재사용 (동일 문서 내 quote_text + statement 중복 방지)
    claims_created_count = 0
    duplicate_claims_reused = 0
    ref_to_claim: dict[str, Claim] = {}
    doc_claim_ids: list[int] = []
    doc_claim_objs: list[Claim] = []

    if doc and payload.claims:
        existing_claim_count = session.execute(
            select(func.count(Claim.id)).where(Claim.document_id == doc.id)
        ).scalar() or 0

        for c in payload.claims:
            quote = c.quote.strip()
            statement = (c.claim_text or quote).strip()

            stmt_claim = select(Claim).where(
                and_(
                    Claim.document_id == doc.id,
                    Claim.quote_text == quote,
                    Claim.statement == statement,
                )
            )
            claim_obj = session.execute(stmt_claim.order_by(Claim.id.desc())).scalars().first()

            if claim_obj:
                duplicate_claims_reused += 1
            else:
                # 4절 상한 검증: 문서별 누적 사실 100개
                if existing_claim_count + claims_created_count >= 100:
                    raise ValueError(
                        f"문서(ID: {doc.id}, '{doc.title}')의 누적 Claim 상한선(100개)을 초과하여 새로운 사실을 저장할 수 없습니다."
                    )
                claim_obj = Claim(
                    document_id=doc.id,
                    quote_text=quote,
                    statement=statement,
                    start_offset=getattr(c, "start_offset", None),
                    end_offset=getattr(c, "end_offset", None),
                )
                session.add(claim_obj)
                session.flush()
                claims_created_count += 1

            if getattr(c, "ref_id", None):
                ref_to_claim[c.ref_id] = claim_obj
            doc_claim_ids.append(claim_obj.id)
            doc_claim_objs.append(claim_obj)

    # 3. 노드(Node) 처리: 임시 ref_id와 이름 해소, 중복 방지, 명시적 Claim 연결
    ref_to_node: dict[str, Node] = {}
    name_to_node: dict[str, Node] = {}
    nodes_created_count = 0

    for n in payload.nodes:
        cls_obj = get_or_create_classification(session, n.classification)
        node: Optional[Node] = None

        if getattr(n, "existing_node_id", None):
            node = session.get(Node, n.existing_node_id)

        if not node:
            # 이름으로 기존 노드 확인
            existing_node = find_node_by_name(session, n.name)
            if existing_node:
                node = existing_node
            else:
                node = resolve_or_create_node(
                    session=session,
                    name=n.name,
                    classification_code=cls_obj.code,
                    description=n.description,
                    properties=n.properties or {},
                )
                nodes_created_count += 1
                affected_node_ids.add(node.id)

        # 노드 설명이나 속성이 보강된 경우 업데이트
        if n.description and (not node.description or node.description != n.description):
            node.description = n.description
            affected_node_ids.add(node.id)

        # M2.4: 속성 누적 (새 속성 키 추가, 충돌 시 기존 채택 값 보존)
        if n.properties:
            cur_props = dict(node.properties or {})
            props_updated = False
            for k, v in n.properties.items():
                if v is not None and k not in cur_props:
                    cur_props[k] = v
                    props_updated = True
            if props_updated:
                node.properties = cur_props
                affected_node_ids.add(node.id)

        # 명시적 Claim 연결: 노드와 직접 연관된 Claim만 선별
        # 1) 명시적 claim_refs / claim_ref
        # 2) Claim 인용문(quote) 또는 요약(claim_text)에 노드명이 직접 언급된 Claim
        matched_node_claim_ids: set[int] = set()
        for cr in getattr(n, "claim_refs", None) or []:
            if cr in ref_to_claim:
                matched_node_claim_ids.add(ref_to_claim[cr].id)
        if getattr(n, "claim_ref", None) and n.claim_ref in ref_to_claim:
            matched_node_claim_ids.add(ref_to_claim[n.claim_ref].id)

        n_name_lower = n.name.lower()
        for claim_obj in doc_claim_objs:
            quote_lower = (claim_obj.quote_text or "").lower()
            stmt_lower = (claim_obj.statement or "").lower()
            if n_name_lower in quote_lower or n_name_lower in stmt_lower:
                matched_node_claim_ids.add(claim_obj.id)

        # 문서에 단일 노드만 있는 극단적 케이스에 한해 doc_claim_ids 폴백
        if not matched_node_claim_ids and len(payload.nodes) == 1 and doc_claim_ids:
            matched_node_claim_ids = set(doc_claim_ids)

        if matched_node_claim_ids:
            cur_claim_ids = set(node.claim_ids or [])
            new_claim_ids = cur_claim_ids | matched_node_claim_ids
            if new_claim_ids != cur_claim_ids:
                node.claim_ids = sorted(list(new_claim_ids))
                affected_node_ids.add(node.id)

        if getattr(n, "ref_id", None):
            ref_to_node[n.ref_id] = node
        name_to_node[n.name.lower()] = node

    # 4. 엣지(Edge) 처리: ref_id 및 노드명 참조 해소, 미해결 끝점 거절, 멱등 적재
    edges_created_count = 0
    for e in payload.edges:
        source_node: Optional[Node] = None
        target_node: Optional[Node] = None

        # 1) source_ref / target_ref 우선 해소
        if getattr(e, "source_ref", None):
            source_node = ref_to_node.get(e.source_ref)
        if getattr(e, "target_ref", None):
            target_node = ref_to_node.get(e.target_ref)

        # 2) source_name / target_name 해소
        if not source_node and getattr(e, "source_name", None):
            s_clean = e.source_name.strip()
            source_node = name_to_node.get(s_clean.lower()) or find_node_by_name(session, s_clean)

        if not target_node and getattr(e, "target_name", None):
            t_clean = e.target_name.strip()
            target_node = name_to_node.get(t_clean.lower()) or find_node_by_name(session, t_clean)

        # 3.2절 지시: 미해결 끝점을 임의의 노드로 자동 생성하지 않는다
        if not source_node or not target_node:
            missing_info = []
            if not source_node:
                missing_info.append(f"출발 노드 '{getattr(e, 'source_name', '?')}'")
            if not target_node:
                missing_info.append(f"도착 노드 '{getattr(e, 'target_name', '?')}'")
            missing_str = ", ".join(missing_info)
            raise ValueError(
                f"지식그래프 적재 실패: 관계 [{getattr(e, 'source_name', '?')} ➔ {e.relation} ➔ {getattr(e, 'target_name', '?')}]의 "
                f"{missing_str}가 승인된 노드 목록 또는 기존 DB에 존재하지 않습니다."
            )

        rel_obj = get_or_create_relation(session, e.relation)

        # 엣지에 연결할 Claim ID 결정 (claim_refs 배열 및 claim_ref 단일 모두 지원)
        edge_claim_ids: list[int] = []
        c_refs = getattr(e, "claim_refs", None) or []
        if getattr(e, "claim_ref", None) and e.claim_ref not in c_refs:
            c_refs = [e.claim_ref] + list(c_refs)

        for cr in c_refs:
            if cr in ref_to_claim:
                edge_claim_ids.append(ref_to_claim[cr].id)

        if not edge_claim_ids and doc_claim_ids:
            edge_claim_ids = [doc_claim_ids[0]]

        # 동일 (source, target, relation) 엣지 존재 여부 확인
        stmt_edge = select(Edge).where(
            and_(
                Edge.source_node_id == source_node.id,
                Edge.target_node_id == target_node.id,
                Edge.relation_id == rel_obj.id,
            )
        )
        existing_edge = session.execute(stmt_edge.order_by(Edge.id.desc())).scalars().first()

        if existing_edge:
            # 기존 엣지에 새 claim_id를 properties["claim_ids"]로 누적
            props = dict(existing_edge.properties or {})
            existing_cids = set(props.get("claim_ids", []))
            if existing_edge.claim_id:
                existing_cids.add(existing_edge.claim_id)
            for cid in edge_claim_ids:
                existing_cids.add(cid)

            sorted_cids = sorted(list(existing_cids))
            if sorted_cids != props.get("claim_ids"):
                props["claim_ids"] = sorted_cids
                existing_edge.properties = props
                affected_node_ids.add(source_node.id)
                affected_node_ids.add(target_node.id)
        else:
            # 신규 엣지 생성
            edge_props = dict(e.properties or {})
            if edge_claim_ids:
                edge_props["claim_ids"] = sorted(list(set(edge_claim_ids)))

            new_edge = Edge(
                source_node_id=source_node.id,
                target_node_id=target_node.id,
                relation_id=rel_obj.id,
                claim_id=edge_claim_ids[0] if edge_claim_ids else None,
                properties=edge_props,
            )
            session.add(new_edge)
            edges_created_count += 1
            affected_node_ids.add(source_node.id)
            affected_node_ids.add(target_node.id)

        # 엣지에 연계된 Claim은 해당 엣지의 양 끝점 노드에도 연결
        if edge_claim_ids:
            for endpoint_node in (source_node, target_node):
                cur_cids = set(endpoint_node.claim_ids or [])
                new_cids = cur_cids | set(edge_claim_ids)
                if new_cids != cur_cids:
                    endpoint_node.claim_ids = sorted(list(new_cids))
                    affected_node_ids.add(endpoint_node.id)

    primary_node_id: Optional[int] = None
    if ref_to_node:
        primary_node_id = list(ref_to_node.values())[0].id
    elif name_to_node:
        primary_node_id = list(name_to_node.values())[0].id

    return {
        "status": "success",
        "source_project": payload.source_project,
        "document_id": doc.id if doc else None,
        "primary_node_id": primary_node_id,
        "nodes_created": nodes_created_count,
        "edges_created": edges_created_count,
        "claims_created": claims_created_count,
        "duplicate_claims_reused": duplicate_claims_reused,
        "affected_node_ids": sorted(list(affected_node_ids)),
    }
