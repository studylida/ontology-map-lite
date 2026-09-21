"""Read-only Topic reference graph queries for #203."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.exploration import AdjacencyRow, NodeRow
from ontology_map.db.topic_references import TopicReferenceRow


@dataclass(frozen=True)
class TopicCenterRow:
    reference: TopicReferenceRow
    node_type_display_name: str


@dataclass(frozen=True)
class TopicActivity:
    recent_member_count: int
    recent_evidence_group_count: int
    member_evidence_group_counts: dict[int, int]


_PUBLIC_MEMBER_NODES_CTE = """
WITH ranked_ready AS (
    SELECT
        pan.node_id,
        pan.node_search_document_id,
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
public_member_nodes AS (
    SELECT
        n.node_id,
        rr.node_search_document_id,
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
      AND nki.lifecycle_kind = 'EVIDENCE_BACKED'
      AND nki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
      AND nt.node_type_code IN ('COMPANY', 'PERSON', 'TECHNOLOGY', 'EVENT')
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
                bki.lifecycle_kind <> 'EVIDENCE_BACKED'
                OR bki.current_state NOT IN (
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
)
"""

_MEMBERSHIP_FROM = """
FROM public_member_nodes AS member
JOIN search_document_basis AS rb
  ON rb.node_search_document_id = member.node_search_document_id
JOIN relation AS r ON r.relation_id = rb.knowledge_item_id
JOIN knowledge_item AS rki ON rki.knowledge_item_id = r.relation_id
JOIN relation_type_revision AS rtr
  ON rtr.relation_type_revision_id = r.relation_type_revision_id
JOIN relation_type AS rt ON rt.relation_type_id = rtr.relation_type_id
JOIN topic_reference AS tr ON tr.node_id = r.target_node_id
JOIN node AS topic_node ON topic_node.node_id = tr.node_id
JOIN node_type AS topic_type ON topic_type.node_type_id = topic_node.node_type_id
JOIN knowledge_item AS tki ON tki.knowledge_item_id = topic_node.node_id
JOIN claim_relation AS cr
  ON cr.relation_id = r.relation_id AND cr.stance = 'SUPPORT'
JOIN search_document_basis AS cb
  ON cb.node_search_document_id = member.node_search_document_id
 AND cb.knowledge_item_id = cr.claim_id
JOIN knowledge_item AS cki ON cki.knowledge_item_id = cr.claim_id
JOIN claim_observation AS co ON co.claim_id = cr.claim_id
JOIN observation AS o ON o.observation_id = co.observation_id
JOIN source_document AS sd ON sd.source_document_id = o.source_document_id
WHERE rt.relation_code = 'HAS_TOPIC'
  AND r.source_node_id = member.node_id
  AND r.target_node_id = tr.node_id
  AND topic_type.node_type_code = 'TOPIC'
  AND tki.lifecycle_kind = 'PRODUCT_REFERENCE'
  AND rki.item_kind = 'RELATION'
  AND rki.lifecycle_kind = 'EVIDENCE_BACKED'
  AND rki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
  AND cki.item_kind = 'CLAIM'
  AND cki.lifecycle_kind = 'EVIDENCE_BACKED'
  AND cki.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
  AND NOT EXISTS (
      SELECT 1
      FROM lint_finding AS lf
      JOIN lint_policy_rule AS lpr
        ON lpr.lint_policy_rule_id = lf.lint_policy_rule_id
      WHERE lf.knowledge_item_id IN (r.relation_id, cr.claim_id)
        AND lf.resolved_at IS NULL
        AND lpr.severity = 'BLOCKING'
  )
"""

_CONFLICT_SQL = """
EXISTS (
    SELECT 1
    FROM conflict_set AS cs
    WHERE cs.relation_id = r.relation_id
      AND cs.current_state IN ('AGENT_PROPOSED', 'HUMAN_CONFIRMED')
      AND EXISTS (
          SELECT 1 FROM conflict_member AS cm
          WHERE cm.conflict_set_id = cs.conflict_set_id
      )
      AND NOT EXISTS (
          SELECT 1
          FROM conflict_member AS cm
          JOIN knowledge_item AS mki ON mki.knowledge_item_id = cm.claim_id
          WHERE cm.conflict_set_id = cs.conflict_set_id
            AND (
                mki.lifecycle_kind <> 'EVIDENCE_BACKED'
                OR mki.current_state NOT IN (
                    'EVIDENCE_VERIFIED', 'HUMAN_VERIFIED'
                )
                OR EXISTS (
                    SELECT 1
                    FROM lint_finding AS mlf
                    JOIN lint_policy_rule AS mlpr
                      ON mlpr.lint_policy_rule_id = mlf.lint_policy_rule_id
                    WHERE mlf.knowledge_item_id = cm.claim_id
                      AND mlf.resolved_at IS NULL
                      AND mlpr.severity = 'BLOCKING'
                )
            )
      )
)
"""


def get_topic_center(session: Session, node_id: int) -> TopicCenterRow | None:
    row = (
        session.execute(
            sa.text(
                """
                SELECT
                    tr.node_id,
                    tr.topic_code,
                    tr.canonical_display_name,
                    tr.is_active,
                    nt.display_name AS node_type_display_name
                FROM topic_reference AS tr
                JOIN node AS n ON n.node_id = tr.node_id
                JOIN node_type AS nt ON nt.node_type_id = n.node_type_id
                JOIN knowledge_item AS ki ON ki.knowledge_item_id = n.node_id
                WHERE tr.node_id = :node_id
                  AND nt.node_type_code = 'TOPIC'
                  AND ki.item_kind = 'NODE'
                  AND ki.lifecycle_kind = 'PRODUCT_REFERENCE'
                  AND ki.current_state IS NULL
                  AND ki.promotion_batch_id IS NULL
                """
            ),
            {"node_id": node_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return TopicCenterRow(
        reference=TopicReferenceRow(
            node_id=int(row["node_id"]),
            topic_code=str(row["topic_code"]),
            canonical_display_name=str(row["canonical_display_name"]),
            is_active=bool(row["is_active"]),
        ),
        node_type_display_name=str(row["node_type_display_name"]),
    )


def _adjacency_rows(
    session: Session,
    *,
    member_node_ids: list[int] | None,
    topic_node_id: int | None,
    owner_is_topic: bool,
    start_at: datetime | None,
    end_at: datetime,
) -> list[AdjacencyRow]:
    predicates: list[str] = []
    parameters: dict[str, object] = {"start_at": start_at, "end_at": end_at}
    if member_node_ids is not None:
        if not member_node_ids:
            return []
        predicates.append("member.node_id = ANY(CAST(:member_node_ids AS bigint[]))")
        parameters["member_node_ids"] = member_node_ids
    if topic_node_id is not None:
        predicates.append("tr.node_id = :topic_node_id")
        parameters["topic_node_id"] = topic_node_id
    predicates.append(
        "((CAST(:start_at AS timestamptz) IS NULL AND "
        "(sd.published_at IS NULL OR sd.published_at < :end_at)) OR "
        "(sd.published_at >= CAST(:start_at AS timestamptz) AND "
        "sd.published_at < :end_at))"
    )
    extra = " AND " + " AND ".join(predicates)
    statement = sa.text(
        _PUBLIC_MEMBER_NODES_CTE
        + """
        SELECT
            member.node_id AS member_node_id,
            member.name AS member_name,
            member.node_type_code AS member_type_code,
            member.node_type_display_name AS member_type_display_name,
            tr.node_id AS topic_node_id,
            tr.canonical_display_name AS topic_name,
            topic_type.display_name AS topic_type_display_name,
            r.relation_id,
            r.source_node_id,
            r.target_node_id,
            rtr.display_name AS relation_type_display_name,
            rtr.directionality,
            array_agg(
                DISTINCT sd.evidence_group_id ORDER BY sd.evidence_group_id
            ) AS evidence_group_ids,
        """
        + _CONFLICT_SQL
        + """ AS has_conflict
        """
        + _MEMBERSHIP_FROM
        + extra
        + """
        GROUP BY
            member.node_id,
            member.name,
            member.node_type_code,
            member.node_type_display_name,
            tr.node_id,
            tr.canonical_display_name,
            topic_type.display_name,
            r.relation_id,
            r.source_node_id,
            r.target_node_id,
            rtr.display_name,
            rtr.directionality
        ORDER BY member.node_id, tr.node_id, r.relation_id
        """
    )
    rows = session.execute(statement, parameters).mappings().all()
    results: list[AdjacencyRow] = []
    for row in rows:
        if owner_is_topic:
            owner_node_id = int(row["topic_node_id"])
            other = NodeRow(
                node_id=int(row["member_node_id"]),
                name=str(row["member_name"]),
                node_type_code=str(row["member_type_code"]),
                node_type_display_name=str(row["member_type_display_name"]),
            )
        else:
            owner_node_id = int(row["member_node_id"])
            other = NodeRow(
                node_id=int(row["topic_node_id"]),
                name=str(row["topic_name"]),
                node_type_code="TOPIC",
                node_type_display_name=str(row["topic_type_display_name"]),
            )
        results.append(
            AdjacencyRow(
                owner_node_id=owner_node_id,
                other_node=other,
                relation_id=int(row["relation_id"]),
                source_node_id=int(row["source_node_id"]),
                target_node_id=int(row["target_node_id"]),
                relation_type_display_name=str(row["relation_type_display_name"]),
                directionality=cast(
                    Literal["DIRECTED", "SYMMETRIC"], row["directionality"]
                ),
                evidence_group_ids=tuple(
                    int(value) for value in row["evidence_group_ids"]
                ),
                has_conflict=bool(row["has_conflict"]),
            )
        )
    return results


def list_topic_adjacencies_for_members(
    session: Session,
    member_node_ids: list[int],
    start_at: datetime | None,
    end_at: datetime,
) -> list[AdjacencyRow]:
    """Return public direct HAS_TOPIC edges without requiring Topic publication."""

    return _adjacency_rows(
        session,
        member_node_ids=member_node_ids,
        topic_node_id=None,
        owner_is_topic=False,
        start_at=start_at,
        end_at=end_at,
    )


def list_topic_memberships(
    session: Session,
    topic_node_id: int,
    start_at: datetime | None,
    end_at: datetime,
) -> list[AdjacencyRow]:
    return _adjacency_rows(
        session,
        member_node_ids=None,
        topic_node_id=topic_node_id,
        owner_is_topic=True,
        start_at=start_at,
        end_at=end_at,
    )


def get_topic_activity(
    session: Session,
    topic_node_id: int,
    start_at: datetime | None,
    end_at: datetime,
) -> TopicActivity:
    statement = sa.text(
        _PUBLIC_MEMBER_NODES_CTE
        + """
        SELECT
            member.node_id,
            count(DISTINCT sd.evidence_group_id)::integer AS evidence_group_count
        """
        + _MEMBERSHIP_FROM
        + """
          AND tr.node_id = :topic_node_id
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
        GROUP BY member.node_id
        ORDER BY member.node_id
        """
    )
    rows = (
        session.execute(
            statement,
            {
                "topic_node_id": topic_node_id,
                "start_at": start_at,
                "end_at": end_at,
            },
        )
        .mappings()
        .all()
    )
    counts = {int(row["node_id"]): int(row["evidence_group_count"]) for row in rows}
    evidence_group_count = session.scalar(
        sa.text(
            _PUBLIC_MEMBER_NODES_CTE
            + """
            SELECT count(DISTINCT sd.evidence_group_id)::integer
            """
            + _MEMBERSHIP_FROM
            + """
              AND tr.node_id = :topic_node_id
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
            """
        ),
        {
            "topic_node_id": topic_node_id,
            "start_at": start_at,
            "end_at": end_at,
        },
    )
    return TopicActivity(
        recent_member_count=len(counts),
        recent_evidence_group_count=int(evidence_group_count or 0),
        member_evidence_group_counts=counts,
    )
