from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.exploration import _PUBLIC_NODES_CTE


@dataclass(frozen=True)
class NodeRelationRow:
    source_node_id: int
    target_node_id: int
    directionality: Literal["DIRECTED", "SYMMETRIC"]
    relation_id: int
    other_node_id: int
    other_name: str
    other_node_type_code: str
    other_node_type_display_name: str
    relation_type_display_name: str
    supporting_evidence_group_count: int
    has_conflict: bool


@dataclass(frozen=True)
class EvidenceCounts:
    trace_count: int
    has_support: bool


@dataclass(frozen=True)
class EvidenceTraceRow:
    claim_id: int
    claim_text: str
    modality: str
    stance: str
    source_document_id: int
    source_title: str
    publisher_name: str
    published_at: datetime | None
    published_precision: str
    canonical_url: str | None
    observation_id: int
    quote_text: str
    paragraph_number: int | None
    start_char: int
    end_char: int


_PUBLIC_RELATION_ENDPOINTS_CTE = (
    _PUBLIC_NODES_CTE
    + """
,
public_relation_endpoints AS (
    SELECT
        node_id,
        node_search_document_id,
        name,
        node_type_code,
        node_type_display_name
    FROM public_nodes
    UNION ALL
    SELECT
        n.node_id,
        NULL::bigint AS node_search_document_id,
        tr.canonical_display_name AS name,
        nt.node_type_code,
        nt.display_name AS node_type_display_name
    FROM topic_reference AS tr
    JOIN node AS n ON n.node_id = tr.node_id
    JOIN node_type AS nt ON nt.node_type_id = n.node_type_id
    JOIN knowledge_item AS ki ON ki.knowledge_item_id = n.node_id
    WHERE nt.node_type_code = 'TOPIC'
      AND ki.item_kind = 'NODE'
      AND ki.lifecycle_kind = 'PRODUCT_REFERENCE'
      AND ki.current_state IS NULL
      AND ki.promotion_batch_id IS NULL
)
"""
)


def get_public_search_document_id(session: Session, node_id: int) -> int | None:
    statement = sa.text(
        _PUBLIC_NODES_CTE
        + """
        SELECT node_search_document_id
        FROM public_nodes
        WHERE node_id = :node_id
        """
    )
    value = session.scalar(statement, {"node_id": node_id})
    return int(value) if value is not None else None


def list_node_relations(
    session: Session,
    *,
    node_id: int,
    search_document_id: int,
    start_at: datetime | None,
    end_at: datetime,
    after_support_count: int | None,
    after_relation_id: int | None,
    limit: int,
) -> list[NodeRelationRow]:
    statement = sa.text(
        _PUBLIC_RELATION_ENDPOINTS_CTE
        + """
        SELECT
            r.relation_id,
            source_resolution.canonical_node_id AS source_node_id,
            target_resolution.canonical_node_id AS target_node_id,
            rtr.directionality,
            other.node_id AS other_node_id,
            other.name AS other_name,
            other.node_type_code,
            other.node_type_display_name,
            rtr.display_name AS relation_type_display_name,
            count(DISTINCT sd.evidence_group_id)::integer
                AS supporting_evidence_group_count,
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
                            mki.item_kind <> 'CLAIM'
                            OR mki.lifecycle_kind <> 'EVIDENCE_BACKED'
                            OR mki.current_state NOT IN (
                                'EVIDENCE_VERIFIED', 'HUMAN_VERIFIED'
                            )
                            OR NOT EXISTS (
                                SELECT 1
                                FROM search_document_basis AS mb
                                WHERE mb.node_search_document_id =
                                      :search_document_id
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
        FROM search_document_basis AS rb
        JOIN relation AS r ON r.relation_id = rb.knowledge_item_id
        JOIN canonical_nodes AS source_resolution
          ON source_resolution.source_node_id = r.source_node_id
        JOIN canonical_nodes AS target_resolution
          ON target_resolution.source_node_id = r.target_node_id
        JOIN knowledge_item AS rki ON rki.knowledge_item_id = r.relation_id
        JOIN relation_type_revision AS rtr
          ON rtr.relation_type_revision_id = r.relation_type_revision_id
        JOIN public_relation_endpoints AS other
          ON other.node_id = CASE
              WHEN source_resolution.canonical_node_id = :node_id
                THEN target_resolution.canonical_node_id
              ELSE source_resolution.canonical_node_id
          END
        JOIN claim_relation AS cr
          ON cr.relation_id = r.relation_id AND cr.stance = 'SUPPORT'
        JOIN search_document_basis AS cb
          ON cb.node_search_document_id = :search_document_id
         AND cb.knowledge_item_id = cr.claim_id
        JOIN knowledge_item AS cki ON cki.knowledge_item_id = cr.claim_id
        JOIN claim_observation AS co ON co.claim_id = cr.claim_id
        JOIN observation AS o ON o.observation_id = co.observation_id
        JOIN source_document AS sd
          ON sd.source_document_id = o.source_document_id
        WHERE rb.node_search_document_id = :search_document_id
          AND :node_id IN (
              source_resolution.canonical_node_id,
              target_resolution.canonical_node_id
          )
          AND rki.item_kind = 'RELATION'
          AND rki.lifecycle_kind = 'EVIDENCE_BACKED'
          AND rki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
          AND cki.item_kind = 'CLAIM'
          AND cki.lifecycle_kind = 'EVIDENCE_BACKED'
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
            r.relation_id,
            source_resolution.canonical_node_id,
            target_resolution.canonical_node_id,
            rtr.directionality,
            other.node_id,
            other.name,
            other.node_type_code,
            other.node_type_display_name,
            rtr.display_name
        HAVING CAST(:after_relation_id AS bigint) IS NULL
            OR count(DISTINCT sd.evidence_group_id) <
               CAST(:after_support_count AS bigint)
            OR (
                count(DISTINCT sd.evidence_group_id) =
                    CAST(:after_support_count AS bigint)
                AND r.relation_id > CAST(:after_relation_id AS bigint)
            )
        ORDER BY supporting_evidence_group_count DESC, r.relation_id ASC
        LIMIT :limit
        """
    )
    rows = session.execute(
        statement,
        {
            "node_id": node_id,
            "search_document_id": search_document_id,
            "start_at": start_at,
            "end_at": end_at,
            "after_support_count": after_support_count,
            "after_relation_id": after_relation_id,
            "limit": limit,
        },
    ).mappings()
    return [
        NodeRelationRow(
            relation_id=int(row["relation_id"]),
            source_node_id=int(row["source_node_id"]),
            target_node_id=int(row["target_node_id"]),
            directionality=cast(
                Literal["DIRECTED", "SYMMETRIC"], row["directionality"]
            ),
            other_node_id=int(row["other_node_id"]),
            other_name=str(row["other_name"]),
            other_node_type_code=str(row["node_type_code"]),
            other_node_type_display_name=str(row["node_type_display_name"]),
            relation_type_display_name=str(row["relation_type_display_name"]),
            supporting_evidence_group_count=int(row["supporting_evidence_group_count"]),
            has_conflict=bool(row["has_conflict"]),
        )
        for row in rows
    ]


_RELATION_TRACES_CTE = (
    _PUBLIC_RELATION_ENDPOINTS_CTE
    + """
,
relation_publications AS (
    SELECT DISTINCT owner.node_search_document_id
    FROM relation AS r
    JOIN knowledge_item AS rki ON rki.knowledge_item_id = r.relation_id
    JOIN public_relation_endpoints AS source ON source.node_id = r.source_node_id
    JOIN public_relation_endpoints AS target ON target.node_id = r.target_node_id
    JOIN public_nodes AS owner
      ON owner.node_id IN (r.source_node_id, r.target_node_id)
    JOIN search_document_basis AS rb
      ON rb.node_search_document_id = owner.node_search_document_id
     AND rb.knowledge_item_id = r.relation_id
    WHERE r.relation_id = :relation_id
      AND rki.item_kind = 'RELATION'
      AND rki.lifecycle_kind = 'EVIDENCE_BACKED'
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
),
eligible_traces AS (
    SELECT DISTINCT
        c.claim_id,
        c.statement_text AS claim_text,
        c.modality,
        cr.stance,
        sd.source_document_id,
        sd.title AS source_title,
        sd.publisher_name,
        sd.published_at,
        sd.published_precision,
        sd.canonical_url,
        o.observation_id,
        o.quote_text,
        o.paragraph_number,
        o.start_char,
        o.end_char
    FROM relation_publications AS rp
    JOIN claim_relation AS cr ON cr.relation_id = :relation_id
    JOIN search_document_basis AS cb
      ON cb.node_search_document_id = rp.node_search_document_id
     AND cb.knowledge_item_id = cr.claim_id
    JOIN claim AS c ON c.claim_id = cr.claim_id
    JOIN knowledge_item AS cki ON cki.knowledge_item_id = c.claim_id
    JOIN claim_observation AS co ON co.claim_id = c.claim_id
    JOIN observation AS o ON o.observation_id = co.observation_id
    JOIN source_document AS sd
      ON sd.source_document_id = o.source_document_id
    WHERE cki.item_kind = 'CLAIM'
      AND cki.lifecycle_kind = 'EVIDENCE_BACKED'
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
)
"""
)


def get_relation_evidence_counts(
    session: Session,
    relation_id: int,
    start_at: datetime | None,
    end_at: datetime,
) -> EvidenceCounts:
    statement = sa.text(
        _RELATION_TRACES_CTE
        + """
        SELECT
            count(*)::integer AS trace_count,
            coalesce(bool_or(stance = 'SUPPORT'), false) AS has_support
        FROM eligible_traces
        """
    )
    row = (
        session.execute(
            statement,
            {"relation_id": relation_id, "start_at": start_at, "end_at": end_at},
        )
        .mappings()
        .one()
    )
    return EvidenceCounts(
        trace_count=int(row["trace_count"]),
        has_support=bool(row["has_support"]),
    )


def list_relation_evidence(
    session: Session,
    *,
    relation_id: int,
    start_at: datetime | None,
    end_at: datetime,
    after_published_at: datetime | None,
    after_source_document_id: int | None,
    after_observation_id: int | None,
    after_claim_id: int | None,
    limit: int,
) -> list[EvidenceTraceRow]:
    statement = sa.text(
        _RELATION_TRACES_CTE
        + """
        SELECT *
        FROM eligible_traces
        WHERE CAST(:after_source_document_id AS bigint) IS NULL
           OR (
                CAST(:after_published_at AS timestamptz) IS NOT NULL
                AND (
                    published_at IS NULL
                    OR published_at < CAST(:after_published_at AS timestamptz)
                    OR (
                        published_at = CAST(:after_published_at AS timestamptz)
                        AND (
                            source_document_id <
                                CAST(:after_source_document_id AS bigint)
                            OR (
                                source_document_id =
                                    CAST(:after_source_document_id AS bigint)
                                AND (
                                    observation_id >
                                        CAST(:after_observation_id AS bigint)
                                    OR (
                                        observation_id =
                                            CAST(:after_observation_id AS bigint)
                                        AND claim_id >
                                            CAST(:after_claim_id AS bigint)
                                    )
                                )
                            )
                        )
                    )
                )
           )
           OR (
                CAST(:after_published_at AS timestamptz) IS NULL
                AND published_at IS NULL
                AND (
                    source_document_id < CAST(:after_source_document_id AS bigint)
                    OR (
                        source_document_id =
                            CAST(:after_source_document_id AS bigint)
                        AND (
                            observation_id > CAST(:after_observation_id AS bigint)
                            OR (
                                observation_id =
                                    CAST(:after_observation_id AS bigint)
                                AND claim_id > CAST(:after_claim_id AS bigint)
                            )
                        )
                    )
                )
           )
        ORDER BY
            published_at DESC NULLS LAST,
            source_document_id DESC,
            observation_id ASC,
            claim_id ASC
        LIMIT :limit
        """
    )
    rows = session.execute(
        statement,
        {
            "relation_id": relation_id,
            "start_at": start_at,
            "end_at": end_at,
            "after_published_at": after_published_at,
            "after_source_document_id": after_source_document_id,
            "after_observation_id": after_observation_id,
            "after_claim_id": after_claim_id,
            "limit": limit,
        },
    ).mappings()
    return [
        EvidenceTraceRow(
            claim_id=int(row["claim_id"]),
            claim_text=str(row["claim_text"]),
            modality=str(row["modality"]),
            stance=str(row["stance"]),
            source_document_id=int(row["source_document_id"]),
            source_title=str(row["source_title"]),
            publisher_name=str(row["publisher_name"]),
            published_at=row["published_at"],
            published_precision=str(row["published_precision"]),
            canonical_url=(
                None if row["canonical_url"] is None else str(row["canonical_url"])
            ),
            observation_id=int(row["observation_id"]),
            quote_text=str(row["quote_text"]),
            paragraph_number=(
                int(row["paragraph_number"])
                if row["paragraph_number"] is not None
                else None
            ),
            start_char=int(row["start_char"]),
            end_char=int(row["end_char"]),
        )
        for row in rows
    ]
