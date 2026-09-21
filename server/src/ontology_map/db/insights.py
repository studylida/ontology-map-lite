from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class InsightRow:
    node_insight_id: int
    node_id: int
    time_window: str
    slot: int
    title: str
    summary_text: str
    synthesis_text: str
    caveat_text: str
    evidence_group_count: int


@dataclass(frozen=True)
class InsightTraceRow:
    claim_id: int
    claim_text: str
    role: str
    display_order: int
    title: str
    publisher_name: str
    published_at: datetime | None
    published_precision: str
    canonical_url: str | None
    quote_text: str
    paragraph_number: int | None
    start_char: int
    end_char: int


def get_bundle(session: Session, node_id: int) -> tuple[int, int] | None:
    row = session.execute(
        sa.text("""
        SELECT latest.node_search_document_id, mt.model_task_id
        FROM (
            SELECT pan.node_search_document_id, pan.node_insight_model_task_id
            FROM publication_affected_node pan
            JOIN promotion_batch pb USING (promotion_batch_id)
            WHERE pan.node_id = :node_id
              AND pb.promotion_status = 'COMMITTED'
              AND pb.publication_status = 'READY'
            ORDER BY pb.ready_at DESC, pb.promotion_batch_id DESC
            LIMIT 1
        ) latest
        JOIN model_task mt ON mt.model_task_id = latest.node_insight_model_task_id
        WHERE mt.task_kind = 'NODE_INSIGHT' AND mt.status = 'SUCCESS'
          AND latest.node_search_document_id IS NOT NULL
    """),
        {"node_id": node_id},
    ).first()
    return (int(row[0]), int(row[1])) if row else None


# 목록과 상세가 같은 불변 결과와 전체 basis 공개 검사를 사용한다.
_VISIBLE_INSIGHTS = """
WITH public_items AS (
    SELECT ki.knowledge_item_id
    FROM knowledge_item ki
    JOIN promotion_batch pb USING (promotion_batch_id)
    WHERE ki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
      AND pb.promotion_status = 'COMMITTED' AND pb.publication_status = 'READY'
      AND NOT EXISTS (
          SELECT 1 FROM lint_finding lf
          JOIN lint_policy_rule lpr USING (lint_policy_rule_id)
          WHERE lf.knowledge_item_id = ki.knowledge_item_id
            AND lf.resolved_at IS NULL AND lpr.severity = 'BLOCKING'
      )
), visible_insights AS (
    SELECT ni.* FROM node_insight ni
    WHERE ni.node_id = :node_id AND ni.time_window = :time_window
      AND ni.node_search_document_id = :document_id
      AND ni.model_task_id = :task_id
      AND EXISTS (
          SELECT 1 FROM search_document_basis b
          JOIN public_items p ON p.knowledge_item_id = b.knowledge_item_id
          WHERE b.node_search_document_id = :document_id
            AND b.knowledge_item_id = :node_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM search_document_basis b
          WHERE b.node_search_document_id = :document_id
            AND NOT EXISTS (SELECT 1 FROM public_items p
                WHERE p.knowledge_item_id = b.knowledge_item_id)
      )
      AND EXISTS (SELECT 1 FROM node_insight_claim nic
          WHERE nic.node_insight_id = ni.node_insight_id AND nic.role = 'KEY_CLAIM')
      AND NOT EXISTS (
          SELECT 1 FROM node_insight_claim nic
          WHERE nic.node_insight_id = ni.node_insight_id AND (
              NOT EXISTS (SELECT 1 FROM public_items p
                  WHERE p.knowledge_item_id = nic.claim_id)
              OR NOT EXISTS (SELECT 1 FROM search_document_basis b
                  WHERE b.node_search_document_id = :document_id
                    AND b.knowledge_item_id = nic.claim_id)
              OR NOT EXISTS (SELECT 1 FROM claim_observation co
                  WHERE co.claim_id = nic.claim_id)
          )
      )
)
"""


def list_insights(
    session: Session, node_id: int, time_window: str, bundle: tuple[int, int]
) -> list[InsightRow]:
    rows = session.execute(
        sa.text(
            _VISIBLE_INSIGHTS
            + """
        SELECT ni.node_insight_id, ni.node_id, ni.time_window, ni.slot,
               ni.title, ni.summary_text, ni.synthesis_text, ni.caveat_text,
               count(DISTINCT sd.evidence_group_id) FILTER (
                   WHERE sd.published_at >= ni.as_of_at - CASE ni.time_window
                       WHEN 'RECENT_90_DAYS' THEN interval '90 days'
                       ELSE interval '365 days' END
                     AND sd.published_at < ni.as_of_at
               )::integer AS evidence_group_count
        FROM visible_insights ni
        JOIN node_insight_claim nic USING (node_insight_id)
        JOIN claim_observation co USING (claim_id)
        JOIN observation o USING (observation_id)
        JOIN source_document sd USING (source_document_id)
        GROUP BY ni.node_insight_id, ni.node_id, ni.time_window, ni.slot,
                 ni.title, ni.summary_text, ni.synthesis_text,
                 ni.caveat_text, ni.as_of_at
        ORDER BY ni.slot
    """
        ),
        {
            "node_id": node_id,
            "time_window": time_window,
            "document_id": bundle[0],
            "task_id": bundle[1],
        },
    ).mappings()
    return [InsightRow(**row) for row in rows]


def get_owner(session: Session, insight_id: int) -> tuple[int, str] | None:
    row = session.execute(
        sa.text("""
        SELECT node_id, time_window FROM node_insight
        WHERE node_insight_id = :insight_id
    """),
        {"insight_id": insight_id},
    ).first()
    return (int(row[0]), str(row[1])) if row else None


def list_traces(session: Session, insight_id: int) -> list[InsightTraceRow]:
    rows = session.execute(
        sa.text("""
        SELECT nic.claim_id, c.statement_text AS claim_text, nic.role,
               nic.display_order, sd.title, sd.publisher_name, sd.published_at,
               sd.published_precision, sd.canonical_url, o.quote_text,
               o.paragraph_number, o.start_char, o.end_char
        FROM node_insight_claim nic
        JOIN claim c USING (claim_id)
        JOIN claim_observation co USING (claim_id)
        JOIN observation o USING (observation_id)
        JOIN source_document sd USING (source_document_id)
        WHERE nic.node_insight_id = :insight_id
        ORDER BY nic.display_order, sd.published_at DESC NULLS LAST,
                 sd.source_document_id, o.observation_id
    """),
        {"insight_id": insight_id},
    ).mappings()
    return [InsightTraceRow(**row) for row in rows]
