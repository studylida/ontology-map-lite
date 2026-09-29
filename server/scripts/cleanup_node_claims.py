# server/scripts/cleanup_node_claims.py
"""기존 DB 내 노드들의 claim_ids를 정제하여, 실제 해당 노드가 언급된 인용구/진술문만 매핑되도록 업데이트."""

import re
from sqlalchemy.orm import Session
from ontology_map.db.session import get_engine
from ontology_map.db.schema import Node, Claim, Edge


def clean_claims():
    engine = get_engine()
    with Session(engine) as session:
        nodes = session.query(Node).all()
        claims = session.query(Claim).all()
        claim_map = {c.id: c for c in claims}
        
        print(f"Total nodes: {len(nodes)}, Total claims: {len(claims)}")
        
        updated_count = 0
        for node in nodes:
            raw_name = node.name.strip()
            
            # 검색 토큰 정교화 (단독 'ai', 'pc' 같은 2글자 약어로 인한 오탐 방지)
            tokens = set()
            tokens.add(raw_name.lower())
            
            # 괄호 안 내용 추출 (예: '자율 이동 로봇(AMR)' -> 'amr', '자율 이동 로봇')
            paren_parts = re.findall(r"\((.*?)\)", raw_name)
            for p in paren_parts:
                p_clean = p.strip().lower()
                if len(p_clean) >= 2:
                    tokens.add(p_clean)
                    
            name_no_paren = re.sub(r"\(.*?\)", "", raw_name).strip().lower()
            if len(name_no_paren) >= 2:
                tokens.add(name_no_paren)
                
            # 특수 케이스 및 영/한 상호 별칭
            low_name = raw_name.lower()
            if "엔비디아" in low_name or "nvidia" in low_name:
                tokens.add("nvidia")
                tokens.add("엔비디아")
            if "sk하이닉스" in low_name or "sk hynix" in low_name:
                tokens.add("sk하이닉스")
                tokens.add("하이닉스")
            if "openusd" in low_name:
                tokens.add("openusd")
            if "cuda" in low_name:
                tokens.add("cuda-x")
                tokens.add("cuda")
            if "physicsnemo" in low_name:
                tokens.add("physicsnemo")
            if "cuopt" in low_name:
                tokens.add("cuopt")
            if "metropolis" in low_name:
                tokens.add("metropolis")
            if "omniverse" in low_name:
                tokens.add("omniverse")
            if "tcad" in low_name:
                tokens.add("tcad")
            if "eda" in low_name or "반도체 설계" in low_name:
                tokens.add("eda")
                tokens.add("반도체 설계")
            if "차세대 메모리" in low_name:
                tokens.add("차세대 메모리")
                tokens.add("첨단 메모리")
            if "베라 cpu" in low_name or "vera cpu" in low_name:
                tokens.add("베라 cpu")
                tokens.add("vera cpu")
            if "베라 루빈" in low_name or "vera rubin" in low_name:
                tokens.add("베라 루빈")
                tokens.add("vera rubin")
            if "rtx 스파크" in low_name or "rtx spark" in low_name:
                tokens.add("rtx 스파크")
                tokens.add("spark")
            if "젯슨 토르" in low_name or "jetson thor" in low_name:
                tokens.add("젯슨 토르")
                tokens.add("jetson thor")
            if "ai 팩토리" in low_name:
                tokens.add("ai 팩토리")
            if "디지털 트윈" in low_name:
                tokens.add("디지털 트윈")

            # 해당 노드가 연결된 Edge들 확인
            connected_edges = (
                session.query(Edge)
                .filter((Edge.source_node_id == node.id) | (Edge.target_node_id == node.id))
                .all()
            )
            # 노드가 실제로 속한 문서 후보군 도출 (엣지에 연결된 claim의 document_id)
            doc_ids = set()
            for e in connected_edges:
                if e.claim_id and e.claim_id in claim_map:
                    doc_ids.add(claim_map[e.claim_id].document_id)
                    
            # 만약 엣지로 doc_ids를 못 찾았다면 기존 claim_ids의 document_id 참조
            if not doc_ids and node.claim_ids:
                for cid in node.claim_ids:
                    if cid in claim_map:
                        doc_ids.add(claim_map[cid].document_id)

            # 대상 Claim 범위 한정 (해당 문서의 Claim들만 매칭, 문서가 없으면 전체 claims)
            candidate_claims = [c for c in claims if not doc_ids or c.document_id in doc_ids]

            matched_claim_ids = []
            for claim in candidate_claims:
                target_text = f"{claim.statement or ''} {claim.quote_text or ''}".lower()
                if any(tok in target_text for tok in tokens):
                    matched_claim_ids.append(claim.id)

            matched_claim_ids = sorted(list(set(matched_claim_ids)))

            if matched_claim_ids != (node.claim_ids or []):
                print(f"[Node {node.id}: {node.name}] claim_ids updated: {node.claim_ids} -> {matched_claim_ids}")
                node.claim_ids = matched_claim_ids
                updated_count += 1
            else:
                print(f"[Node {node.id}: {node.name}] claim_ids kept: {matched_claim_ids}")

        session.commit()
        print(f"\nCleanup complete. Updated {updated_count} nodes.")


if __name__ == "__main__":
    clean_claims()
