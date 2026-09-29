"""지식그래프 코어 및 온톨로지 핵심 스키마 (Alembic / SQLAlchemy 2.0)."""

from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# -------------------------------------------------------------
# 1. 온톨로지 메타 (동적 관리)
# -------------------------------------------------------------
class Classification(Base):
    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(sa.String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)


class Relation(Base):
    __tablename__ = "relations"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(sa.String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text)
    is_directed: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)


# -------------------------------------------------------------
# 2. 지식그래프 원천 문서 & Claim (근거/이력)
# -------------------------------------------------------------
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    normalized_content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_uri: Mapped[Optional[str]] = mapped_column(sa.Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # 상태 관리 & 에러 추적
    status: Mapped[str] = mapped_column(sa.String(50), default="PENDING", nullable=False, index=True)
    error_code: Mapped[Optional[str]] = mapped_column(sa.String(100))
    error_detail: Mapped[Optional[str]] = mapped_column(sa.Text)

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )

    claims: Mapped[list["Claim"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    statement: Mapped[str] = mapped_column(sa.Text, nullable=False)
    start_offset: Mapped[Optional[int]] = mapped_column(sa.Integer)
    end_offset: Mapped[Optional[int]] = mapped_column(sa.Integer)

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())

    document: Mapped["Document"] = relationship(back_populates="claims")


# -------------------------------------------------------------
# 3. 지식그래프 코어 (Node, Edge)
# -------------------------------------------------------------
class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False, index=True)
    classification_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("classifications.id"), nullable=False, index=True
    )
    description: Mapped[Optional[str]] = mapped_column(sa.Text)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # 이 노드가 언급된 Claim ID 목록 (Array)
    claim_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )

    classification: Mapped["Classification"] = relationship()
    insight: Mapped[Optional["NodeInsight"]] = relationship(
        back_populates="node", uselist=False, cascade="all, delete-orphan"
    )
    qa_pairs: Mapped[list["NodeQAPair"]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )


class Edge(Base):
    __tablename__ = "edges"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    source_node_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_node_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("relations.id"), nullable=False, index=True
    )
    claim_id: Mapped[Optional[int]] = mapped_column(
        sa.BigInteger, sa.ForeignKey("claims.id", ondelete="SET NULL"), index=True
    )
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())

    source_node: Mapped["Node"] = relationship(foreign_keys=[source_node_id])
    target_node: Mapped["Node"] = relationship(foreign_keys=[target_node_id])
    relation: Mapped["Relation"] = relationship()
    claim: Mapped[Optional["Claim"]] = relationship()


# -------------------------------------------------------------
# 4. LLM 파생 결과 (인사이트 종합보고서 & Q&A)
# -------------------------------------------------------------
class NodeInsight(Base):
    __tablename__ = "node_insights"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("nodes.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    recent_history_summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    overall_insight: Mapped[str] = mapped_column(sa.Text, nullable=False)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    input_fingerprint: Mapped[Optional[str]] = mapped_column(sa.String(64), nullable=True, index=True)

    generated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())

    node: Mapped["Node"] = relationship(back_populates="insight")


class NodeQAPair(Base):
    __tablename__ = "node_qa_pairs"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        sa.BigInteger, sa.ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(sa.Text, nullable=False)
    answer: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sequence: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)

    node: Mapped["Node"] = relationship(back_populates="qa_pairs")
