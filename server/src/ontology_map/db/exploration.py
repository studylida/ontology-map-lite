from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class NodeRow:
    node_id: int
    name: str
    node_type_code: str
    node_type_display_name: str


@dataclass(frozen=True)
class CenterRow(NodeRow):
    node_context_id: int
    context_text: str


@dataclass(frozen=True)
class FollowupRow:
    slot: int
    question_text: str
    target_node_id: int


@dataclass(frozen=True)
class AdjacencyRow:
    owner_node_id: int
    other_node: NodeRow
    relation_id: int
    source_node_id: int
    target_node_id: int
    relation_type_display_name: str
    directionality: Literal["DIRECTED", "SYMMETRIC"]
    evidence_group_ids: tuple[int, ...]
    has_conflict: bool


_PUBLIC_NODES_CTE = """
WITH RECURSIVE node_resolution(source_node_id, current_node_id) AS (
    SELECT node_id, node_id FROM node

    UNION ALL

    SELECT nr.source_node_id, nm.canonical_node_id
    FROM node_resolution AS nr
    JOIN node_merge AS nm
      ON nm.source_node_id = nr.current_node_id
     AND nm.reversed_at IS NULL
),
canonical_nodes AS (
    SELECT nr.source_node_id, nr.current_node_id AS canonical_node_id
    FROM node_resolution AS nr
    WHERE NOT EXISTS (
        SELECT 1
        FROM node_merge AS nm
        WHERE nm.source_node_id = nr.current_node_id
          AND nm.reversed_at IS NULL
    )
),
ranked_ready AS (
    SELECT
        pan.node_id,
        pan.node_search_document_id,
        pan.node_context_id,
        row_number() OVER (
            PARTITION BY pan.node_id
            ORDER BY pb.ready_at DESC, pb.promotion_batch_id DESC
        ) AS ready_rank
    FROM publication_affected_node AS pan
    JOIN promotion_batch AS pb
      ON pb.promotion_batch_id = pan.promotion_batch_id
    WHERE pb.promotion_status = 'COMMITTED'
      AND pb.publication_status = 'READY'
),
visible_ready_nodes AS (
    SELECT
        n.node_id,
        rr.node_search_document_id,
        rr.node_context_id,
        na.alias_text AS name,
        nt.node_type_code,
        nt.display_name AS node_type_display_name
    FROM ranked_ready AS rr
    JOIN node AS n ON n.node_id = rr.node_id
    JOIN knowledge_item AS nki ON nki.knowledge_item_id = n.node_id
    JOIN node_alias AS na ON na.node_id = n.node_id AND na.is_preferred
    JOIN node_type AS nt ON nt.node_type_id = n.node_type_id
    WHERE rr.ready_rank = 1
      AND rr.node_search_document_id IS NOT NULL
      AND nki.item_kind = 'NODE'
      AND nki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
      AND NOT EXISTS (
          SELECT 1
          FROM lint_finding AS lf
          JOIN lint_policy_rule AS lpr
            ON lpr.lint_policy_rule_id = lf.lint_policy_rule_id
          WHERE lf.knowledge_item_id = n.node_id
            AND lf.resolved_at IS NULL
            AND lpr.severity = 'BLOCKING'
      )
      AND NOT EXISTS (
          SELECT 1
          FROM search_document_basis AS sdb
          JOIN knowledge_item AS bki
            ON bki.knowledge_item_id = sdb.knowledge_item_id
          WHERE sdb.node_search_document_id = rr.node_search_document_id
            AND (
                bki.current_state NOT IN (
                    'EVIDENCE_VERIFIED', 'HUMAN_VERIFIED'
                )
                OR EXISTS (
                    SELECT 1
                    FROM lint_finding AS blf
                    JOIN lint_policy_rule AS blpr
                      ON blpr.lint_policy_rule_id = blf.lint_policy_rule_id
                    WHERE blf.knowledge_item_id = bki.knowledge_item_id
                      AND blf.resolved_at IS NULL
                      AND blpr.severity = 'BLOCKING'
                )
            )
      )
),
public_nodes AS (
    SELECT vrn.*
    FROM visible_ready_nodes AS vrn
    WHERE NOT EXISTS (
        SELECT 1
        FROM node_merge AS nm
        WHERE nm.source_node_id = vrn.node_id
          AND nm.reversed_at IS NULL
    )
),
public_node_documents AS (
    SELECT
        cn.canonical_node_id AS node_id,
        vrn.node_search_document_id
    FROM visible_ready_nodes AS vrn
    JOIN canonical_nodes AS cn ON cn.source_node_id = vrn.node_id
)
"""

_ACTIVITY_CTES = """
,
eligible_claim_evidence AS (
    SELECT DISTINCT
        c.claim_id,
        sd.evidence_group_id
    FROM claim AS c
    JOIN knowledge_item AS cki ON cki.knowledge_item_id = c.claim_id
    JOIN claim_observation AS co ON co.claim_id = c.claim_id
    JOIN observation AS o ON o.observation_id = co.observation_id
    JOIN source_document AS sd ON sd.source_document_id = o.source_document_id
    WHERE cki.item_kind = 'CLAIM'
      AND cki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
      AND (
          (
              CAST(:start_at AS timestamptz) IS NULL
              AND (sd.published_at IS NULL OR sd.published_at < :end_at)
          )
          OR (
              sd.published_at >= CAST(:start_at AS timestamptz)
              AND sd.published_at < :end_at
          )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM lint_finding AS lf
          JOIN lint_policy_rule AS lpr
            ON lpr.lint_policy_rule_id = lf.lint_policy_rule_id
          WHERE lf.knowledge_item_id = c.claim_id
            AND lf.resolved_at IS NULL
            AND lpr.severity = 'BLOCKING'
      )
),
node_evidence AS (
    SELECT pn.node_id, ece.evidence_group_id
    FROM public_nodes AS pn
    JOIN search_document_basis AS cb
      ON cb.node_search_document_id = pn.node_search_document_id
    JOIN eligible_claim_evidence AS ece ON ece.claim_id = cb.knowledge_item_id
    JOIN claim_relation AS cr ON cr.claim_id = ece.claim_id
    JOIN relation AS r ON r.relation_id = cr.relation_id
    JOIN search_document_basis AS rb
      ON rb.node_search_document_id = pn.node_search_document_id
     AND rb.knowledge_item_id = r.relation_id
    JOIN knowledge_item AS rki ON rki.knowledge_item_id = r.relation_id
    WHERE pn.node_id IN (r.source_node_id, r.target_node_id)
      AND rki.item_kind = 'RELATION'
      AND rki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
      AND NOT EXISTS (
          SELECT 1
          FROM lint_finding AS lf
          JOIN lint_policy_rule AS lpr
            ON lpr.lint_policy_rule_id = lf.lint_policy_rule_id
          WHERE lf.knowledge_item_id = r.relation_id
            AND lf.resolved_at IS NULL
            AND lpr.severity = 'BLOCKING'
      )

    UNION

    SELECT pn.node_id, ece.evidence_group_id
    FROM public_nodes AS pn
    JOIN search_document_basis AS cb
      ON cb.node_search_document_id = pn.node_search_document_id
    JOIN eligible_claim_evidence AS ece ON ece.claim_id = cb.knowledge_item_id
    JOIN claim_attribute_value AS cav
      ON cav.claim_id = ece.claim_id AND cav.target_node_id = pn.node_id

    UNION

    SELECT pn.node_id, ece.evidence_group_id
    FROM public_nodes AS pn
    JOIN search_document_basis AS cb
      ON cb.node_search_document_id = pn.node_search_document_id
    JOIN eligible_claim_evidence AS ece ON ece.claim_id = cb.knowledge_item_id
    JOIN event_temporal_basis AS etb
      ON etb.claim_id = ece.claim_id AND etb.event_node_id = pn.node_id
),
activity_counts AS (
    SELECT node_id, count(*)::integer AS evidence_group_count
    FROM node_evidence
    GROUP BY node_id
)
"""


def is_public_node(session: Session, node_id: int) -> bool:
    statement = sa.text(
        """
        SELECT EXISTS (
            SELECT 1
            FROM node AS n
            JOIN knowledge_item AS nki ON nki.knowledge_item_id = n.node_id
            WHERE n.node_id = :node_id
              AND nki.item_kind = 'NODE'
              AND nki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
              AND NOT EXISTS (
                  SELECT 1
                  FROM node_merge AS nm
                  WHERE nm.source_node_id = n.node_id
                    AND nm.reversed_at IS NULL
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM lint_finding AS lf
                  JOIN lint_policy_rule AS lpr
                    ON lpr.lint_policy_rule_id = lf.lint_policy_rule_id
                  WHERE lf.knowledge_item_id = n.node_id
                    AND lf.resolved_at IS NULL
                    AND lpr.severity = 'BLOCKING'
              )
        )
        """
    )
    return bool(session.scalar(statement, {"node_id": node_id}))


def get_center(session: Session, node_id: int) -> CenterRow | None:
    statement = sa.text(
        _PUBLIC_NODES_CTE
        + """
        SELECT
            pn.node_id,
            pn.name,
            pn.node_type_code,
            pn.node_type_display_name,
            nc.node_context_id,
            nc.context_text
        FROM public_nodes AS pn
        JOIN node_context AS nc
          ON nc.node_context_id = pn.node_context_id
         AND nc.node_id = pn.node_id
         AND nc.node_search_document_id = pn.node_search_document_id
        JOIN model_task AS mt ON mt.model_task_id = nc.model_task_id
        WHERE pn.node_id = :node_id
          AND mt.task_kind = 'NODE_CONTEXT'
          AND mt.status = 'SUCCESS'
        """
    )
    row = session.execute(statement, {"node_id": node_id}).mappings().one_or_none()
    if row is None:
        return None
    return CenterRow(
        node_id=int(row["node_id"]),
        name=str(row["name"]),
        node_type_code=str(row["node_type_code"]),
        node_type_display_name=str(row["node_type_display_name"]),
        node_context_id=int(row["node_context_id"]),
        context_text=str(row["context_text"]),
    )


def list_followups(session: Session, node_context_id: int) -> list[FollowupRow]:
    statement = sa.text(
        _PUBLIC_NODES_CTE
        + """
        SELECT fq.slot, fq.question_text, fq.target_node_id
        FROM followup_question AS fq
        JOIN model_task AS mt ON mt.model_task_id = fq.model_task_id
        JOIN public_nodes AS target ON target.node_id = fq.target_node_id
        WHERE fq.node_context_id = :node_context_id
          AND mt.task_kind = 'FOLLOWUP_QUESTIONS'
          AND mt.status = 'SUCCESS'
        ORDER BY fq.slot
        """
    )
    rows = session.execute(statement, {"node_context_id": node_context_id}).mappings()
    return [
        FollowupRow(
            slot=int(row["slot"]),
            question_text=str(row["question_text"]),
            target_node_id=int(row["target_node_id"]),
        )
        for row in rows
    ]


def list_adjacencies(
    session: Session,
    owner_node_ids: list[int],
    start_at: datetime | None,
    end_at: datetime,
) -> list[AdjacencyRow]:
    if not owner_node_ids:
        return []
    statement = sa.text(
        _PUBLIC_NODES_CTE
        + """
        SELECT
            owner.node_id AS owner_node_id,
            other.node_id AS other_node_id,
            other.name AS other_name,
            other.node_type_code,
            other.node_type_display_name,
            r.relation_id,
            source_resolution.canonical_node_id AS source_node_id,
            target_resolution.canonical_node_id AS target_node_id,
            rtr.display_name AS relation_type_display_name,
            rtr.directionality,
            array_agg(
                DISTINCT sd.evidence_group_id ORDER BY sd.evidence_group_id
            ) AS evidence_group_ids,
            EXISTS (
                SELECT 1
                FROM conflict_set AS cs
                WHERE cs.relation_id = r.relation_id
                  AND cs.current_state IN ('AGENT_PROPOSED', 'HUMAN_CONFIRMED')
                  AND EXISTS (
                      SELECT 1
                      FROM conflict_member AS cm
                      WHERE cm.conflict_set_id = cs.conflict_set_id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM conflict_member AS cm
                      JOIN knowledge_item AS mki
                        ON mki.knowledge_item_id = cm.claim_id
                      WHERE cm.conflict_set_id = cs.conflict_set_id
                        AND (
                            mki.current_state NOT IN (
                                'EVIDENCE_VERIFIED', 'HUMAN_VERIFIED'
                            )
                            OR NOT EXISTS (
                                SELECT 1
                                FROM public_node_documents AS conflict_document
                                JOIN search_document_basis AS mb
                                  ON mb.node_search_document_id =
                                     conflict_document.node_search_document_id
                                WHERE conflict_document.node_id = owner.node_id
                                  AND mb.knowledge_item_id = cm.claim_id
                            )
                            OR EXISTS (
                                SELECT 1
                                FROM lint_finding AS mlf
                                JOIN lint_policy_rule AS mlpr
                                  ON mlpr.lint_policy_rule_id =
                                     mlf.lint_policy_rule_id
                                WHERE mlf.knowledge_item_id = cm.claim_id
                                  AND mlf.resolved_at IS NULL
                                  AND mlpr.severity = 'BLOCKING'
                            )
                        )
                  )
            ) AS has_conflict
        FROM public_nodes AS owner
        JOIN public_node_documents AS owner_document
          ON owner_document.node_id = owner.node_id
        JOIN search_document_basis AS rb
          ON rb.node_search_document_id = owner_document.node_search_document_id
        JOIN relation AS r ON r.relation_id = rb.knowledge_item_id
        JOIN canonical_nodes AS source_resolution
          ON source_resolution.source_node_id = r.source_node_id
        JOIN canonical_nodes AS target_resolution
          ON target_resolution.source_node_id = r.target_node_id
        JOIN knowledge_item AS rki ON rki.knowledge_item_id = r.relation_id
        JOIN relation_type_revision AS rtr
          ON rtr.relation_type_revision_id = r.relation_type_revision_id
        JOIN public_nodes AS other
          ON other.node_id = CASE
              WHEN source_resolution.canonical_node_id = owner.node_id
                THEN target_resolution.canonical_node_id
              ELSE source_resolution.canonical_node_id
          END
        JOIN claim_relation AS cr
          ON cr.relation_id = r.relation_id AND cr.stance = 'SUPPORT'
        JOIN search_document_basis AS cb
          ON cb.node_search_document_id = owner_document.node_search_document_id
         AND cb.knowledge_item_id = cr.claim_id
        JOIN knowledge_item AS cki ON cki.knowledge_item_id = cr.claim_id
        JOIN claim_observation AS co ON co.claim_id = cr.claim_id
        JOIN observation AS o ON o.observation_id = co.observation_id
        JOIN source_document AS sd
          ON sd.source_document_id = o.source_document_id
        WHERE owner.node_id = ANY(CAST(:owner_node_ids AS bigint[]))
          AND owner.node_id IN (
              source_resolution.canonical_node_id,
              target_resolution.canonical_node_id
          )
          AND rki.item_kind = 'RELATION'
          AND rki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
          AND cki.item_kind = 'CLAIM'
          AND cki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
          AND (
              (
                  CAST(:start_at AS timestamptz) IS NULL
                  AND (sd.published_at IS NULL OR sd.published_at < :end_at)
              )
              OR (
                  sd.published_at >= CAST(:start_at AS timestamptz)
                  AND sd.published_at < :end_at
              )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM lint_finding AS lf
              JOIN lint_policy_rule AS lpr
                ON lpr.lint_policy_rule_id = lf.lint_policy_rule_id
              WHERE lf.knowledge_item_id IN (r.relation_id, cr.claim_id)
                AND lf.resolved_at IS NULL
                AND lpr.severity = 'BLOCKING'
          )
        GROUP BY
            owner.node_id,
            other.node_id,
            other.name,
            other.node_type_code,
            other.node_type_display_name,
            r.relation_id,
            source_resolution.canonical_node_id,
            target_resolution.canonical_node_id,
            rtr.display_name,
            rtr.directionality
        """
    )
    rows = session.execute(
        statement,
        {
            "owner_node_ids": owner_node_ids,
            "start_at": start_at,
            "end_at": end_at,
        },
    ).mappings()
    return [
        AdjacencyRow(
            owner_node_id=int(row["owner_node_id"]),
            other_node=NodeRow(
                node_id=int(row["other_node_id"]),
                name=str(row["other_name"]),
                node_type_code=str(row["node_type_code"]),
                node_type_display_name=str(row["node_type_display_name"]),
            ),
            relation_id=int(row["relation_id"]),
            source_node_id=int(row["source_node_id"]),
            target_node_id=int(row["target_node_id"]),
            relation_type_display_name=str(row["relation_type_display_name"]),
            directionality=cast(
                Literal["DIRECTED", "SYMMETRIC"], row["directionality"]
            ),
            evidence_group_ids=tuple(int(value) for value in row["evidence_group_ids"]),
            has_conflict=bool(row["has_conflict"]),
        )
        for row in rows
    ]


def get_activity_counts(
    session: Session,
    node_ids: list[int],
    start_at: datetime | None,
    end_at: datetime,
) -> dict[int, int]:
    if not node_ids:
        return {}
    statement = sa.text(
        _PUBLIC_NODES_CTE
        + _ACTIVITY_CTES
        + """
        SELECT pn.node_id, coalesce(ac.evidence_group_count, 0) AS activity_count
        FROM public_nodes AS pn
        LEFT JOIN activity_counts AS ac ON ac.node_id = pn.node_id
        WHERE pn.node_id = ANY(CAST(:node_ids AS bigint[]))
        """
    )
    rows = session.execute(
        statement,
        {
            "node_ids": node_ids,
            "start_at": start_at,
            "end_at": end_at,
        },
    ).mappings()
    return {int(row["node_id"]): int(row["activity_count"]) for row in rows}


def list_ambient_nodes(
    session: Session,
    excluded_node_ids: list[int],
    start_at: datetime | None,
    end_at: datetime,
    limit: int,
) -> list[tuple[NodeRow, int]]:
    statement = sa.text(
        _PUBLIC_NODES_CTE
        + _ACTIVITY_CTES
        + """
        SELECT
            pn.node_id,
            pn.name,
            pn.node_type_code,
            pn.node_type_display_name,
            coalesce(ac.evidence_group_count, 0) AS activity_count
        FROM public_nodes AS pn
        LEFT JOIN activity_counts AS ac ON ac.node_id = pn.node_id
        WHERE NOT (pn.node_id = ANY(CAST(:excluded_node_ids AS bigint[])))
          AND coalesce(ac.evidence_group_count, 0) > 0
        ORDER BY activity_count DESC, pn.node_id ASC
        LIMIT :limit
        """
    )
    rows = session.execute(
        statement,
        {
            "excluded_node_ids": excluded_node_ids,
            "start_at": start_at,
            "end_at": end_at,
            "limit": limit,
        },
    ).mappings()
    return [
        (
            NodeRow(
                node_id=int(row["node_id"]),
                name=str(row["name"]),
                node_type_code=str(row["node_type_code"]),
                node_type_display_name=str(row["node_type_display_name"]),
            ),
            int(row["activity_count"]),
        )
        for row in rows
    ]


def list_peripheral_node_rows(
    session: Session,
    excluded_node_ids: list[int],
    start_at: datetime | None,
    end_at: datetime,
    after_node_id: int,
    limit: int,
) -> list[tuple[NodeRow, int]]:
    statement = sa.text(
        _PUBLIC_NODES_CTE
        + _ACTIVITY_CTES
        + """
        SELECT
            pn.node_id,
            pn.name,
            pn.node_type_code,
            pn.node_type_display_name,
            coalesce(ac.evidence_group_count, 0) AS activity_count
        FROM public_nodes AS pn
        LEFT JOIN activity_counts AS ac ON ac.node_id = pn.node_id
        WHERE pn.node_id > :after_node_id
          AND NOT (pn.node_id = ANY(CAST(:excluded_node_ids AS bigint[])))
          AND coalesce(ac.evidence_group_count, 0) > 0
        ORDER BY pn.node_id ASC
        LIMIT :limit
        """
    )
    rows = session.execute(
        statement,
        {
            "excluded_node_ids": excluded_node_ids,
            "start_at": start_at,
            "end_at": end_at,
            "after_node_id": after_node_id,
            "limit": limit,
        },
    ).mappings()
    return [
        (
            NodeRow(
                node_id=int(row["node_id"]),
                name=str(row["name"]),
                node_type_code=str(row["node_type_code"]),
                node_type_display_name=str(row["node_type_display_name"]),
            ),
            int(row["activity_count"]),
        )
        for row in rows
    ]


def list_previous_peripheral_ids(
    session: Session, excluded_node_ids: list[int], through_id: int
) -> list[int]:
    """동일 scope에서 처음부터 순차 조회한 page의 ID 경계를 재사용한다."""
    statement = sa.text(
        _PUBLIC_NODES_CTE
        + """
        SELECT node_id FROM public_nodes
        WHERE node_id <= :through_id
          AND NOT (node_id = ANY(CAST(:excluded_node_ids AS bigint[])))
        ORDER BY node_id
    """
    )
    return [
        int(value)
        for value in session.scalars(
            statement,
            {"through_id": through_id, "excluded_node_ids": excluded_node_ids},
        )
    ]
