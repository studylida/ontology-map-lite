"""근거(Claim)가 없는 고아/무근거 노드 및 관련 엣지 정리 스크립트."""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_path))

from sqlalchemy import delete, or_, select
from ontology_map.db.schema import Edge, Node
from ontology_map.db.session import open_session


def cleanup_orphan_and_unsupported_nodes():
    with next(open_session()) as session:
        # 1. claim_ids가 비어있는 노드 탐색
        stmt_nodes = select(Node).where(or_(Node.claim_ids == [], Node.claim_ids.is_(None)))
        unsupported_nodes = session.execute(stmt_nodes).scalars().all()

        if not unsupported_nodes:
            print("정리할 무근거 노드가 없습니다.")
            return

        print(f"발견된 무근거 노드 수: {len(unsupported_nodes)}개")
        target_node_ids = []
        for n in unsupported_nodes:
            print(f" - [{n.id}] {n.name} (분류: {n.classification_id}, claims: {n.claim_ids})")
            target_node_ids.append(n.id)

        # 2. 관련 엣지 탐색 및 삭제
        stmt_edges = select(Edge).where(
            or_(
                Edge.source_node_id.in_(target_node_ids),
                Edge.target_node_id.in_(target_node_ids),
            )
        )
        related_edges = session.execute(stmt_edges).scalars().all()
        print(f"연관된 엣지 수: {len(related_edges)}개 삭제 예정")
        for e in related_edges:
            print(f"   엣지 [{e.id}]: {e.source_node_id} -> {e.relation_id} -> {e.target_node_id}")

        session.execute(
            delete(Edge).where(
                or_(
                    Edge.source_node_id.in_(target_node_ids),
                    Edge.target_node_id.in_(target_node_ids),
                )
            )
        )

        # 3. 노드 삭제
        session.execute(delete(Node).where(Node.id.in_(target_node_ids)))
        session.commit()
        print(f"총 {len(target_node_ids)}개 무근거 노드 및 {len(related_edges)}개 엣지 정리 완료!")


if __name__ == "__main__":
    cleanup_orphan_and_unsupported_nodes()
