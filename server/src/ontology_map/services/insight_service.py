"""노드 인사이트 및 Q&A 서비스."""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from ontology_map.db.schema import NodeInsight, NodeQAPair


def get_node_insight(session: Session, node_id: int) -> Optional[NodeInsight]:
    """특정 노드의 종합 인사이트 보고서를 조회합니다."""
    # [빈칸 D]: NodeInsight 테이블에서 node_id가 일치하는 레코드 1개를 조회하는 쿼리를 작성해보세요.
    stmt = select(NodeInsight).where(NodeInsight.node_id == node_id)
    return session.execute(stmt).scalar_one_or_none()


def get_node_qa_pairs(session: Session, node_id: int) -> list[NodeQAPair]:
    """특정 노드의 Q&A 목록을 순서(sequence)대로 조회합니다."""
    # [빈칸 E]: NodeQAPair 테이블에서 node_id가 일치하고, sequence 오름차순으로 정렬하는 쿼리를 작성해보세요.
    # 힌트: .order_by(NodeQAPair.sequence.asc())
    stmt = (
        select(NodeQAPair)
        .where(NodeQAPair.node_id == node_id)
        .order_by(NodeQAPair.sequence.asc())
    )
    return list(session.execute(stmt).scalars().all())
