"""Database boundary for approved NODE_INSIGHT generation (#68).

Only verified baseline knowledge is supplied to the Agent. Generated context,
follow-up answers and conflict summaries are deliberately absent from all
queries in this module.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import insight_generation as product
from ontology_map.db import schema as s
from ontology_map.db.publication_grounding import (
    ReferenceTopicIntegrityError,
    reference_topic_identities,
)
from ontology_map.exploration import TimeWindow
from ontology_map.insight_generation_contracts import (
    ClaimConnection,
    EvidenceExcerpt,
    GroundedClaim,
    InsightAgentInput,
    InsightApplyResult,
    InsightBundleProposal,
    InsightReportCandidate,
    InsightWindowInput,
    PeriodRole,
    PreparedInsightBundle,
    RelatedNode,
    VisibleConflictPair,
)
from ontology_map.insight_generation_contracts import (
    TimeWindow as AgentTimeWindow,
)

_PUBLIC_STATES = ("EVIDENCE_VERIFIED", "HUMAN_VERIFIED")


class InsightPreparationError(ValueError):
    pass


class InsightTaskError(ValueError):
    pass


def _ids_statement(sql: str, name: str = "ids") -> sa.TextClause:
    return sa.text(sql).bindparams(sa.bindparam(name, expanding=True))


def _publication_snapshot(
    session: Session,
    *,
    promotion_batch_id: int,
    node_id: int,
) -> dict[str, Any]:
    row = (
        session.execute(
            sa.text("""
                SELECT pan.node_id, pan.node_search_document_id,
                       pan.node_insight_model_task_id,
                       nt.node_type_code, na.alias_text AS preferred_alias,
                       pb.promotion_status, pb.publication_status
                FROM publication_affected_node pan
                JOIN promotion_batch pb
                  ON pb.promotion_batch_id = pan.promotion_batch_id
                JOIN node n ON n.node_id = pan.node_id
                JOIN node_type nt ON nt.node_type_id = n.node_type_id
                LEFT JOIN node_alias na
                  ON na.node_id = n.node_id AND na.is_preferred
                WHERE pan.promotion_batch_id = :batch_id
                  AND pan.node_id = :node_id
                  AND pb.promotion_status = 'COMMITTED'
                  AND pb.publication_status = 'PREPARING'
            """),
            {"batch_id": promotion_batch_id, "node_id": node_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["node_search_document_id"] is None:
        raise InsightPreparationError(
            "NODE_INSIGHT generation requires a PREPARING publication search document"
        )
    return dict(row)


def _basis_ids(
    session: Session,
    *,
    document_id: int,
    current_batch_id: int,
) -> tuple[int, ...]:
    rows = session.execute(
        sa.text("""
            SELECT b.knowledge_item_id, k.current_state, k.promotion_batch_id,
                   p.promotion_status, p.publication_status,
                   EXISTS (
                       SELECT 1
                       FROM lint_finding f
                       JOIN lint_policy_rule r
                         ON r.lint_policy_rule_id = f.lint_policy_rule_id
                       WHERE f.knowledge_item_id = k.knowledge_item_id
                         AND f.resolved_at IS NULL
                         AND r.severity = 'BLOCKING'
                   ) AS has_blocking_lint
            FROM search_document_basis b
            JOIN knowledge_item k
              ON k.knowledge_item_id = b.knowledge_item_id
            JOIN promotion_batch p
              ON p.promotion_batch_id = k.promotion_batch_id
            WHERE b.node_search_document_id = :document_id
            ORDER BY b.knowledge_item_id
        """),
        {"document_id": document_id},
    ).mappings()
    ids: list[int] = []
    for row in rows:
        same_generation = int(row["promotion_batch_id"]) == current_batch_id
        usable = (
            row["current_state"] in _PUBLIC_STATES
            and row["promotion_status"] == "COMMITTED"
            and (same_generation or row["publication_status"] == "READY")
            and not bool(row["has_blocking_lint"])
        )
        if not usable:
            raise InsightPreparationError(
                "search-document basis is not publication-usable"
            )
        ids.append(int(row["knowledge_item_id"]))
    return tuple(ids)


def _node_identities(
    session: Session,
    node_ids: Iterable[int],
    *,
    current_batch_id: int,
) -> dict[int, RelatedNode]:
    ids = sorted(set(node_ids))
    if not ids:
        return {}
    rows = session.execute(
        _ids_statement("""
            SELECT n.node_id, t.node_type_code, a.alias_text AS preferred_alias,
                   k.current_state, k.promotion_batch_id,
                   p.promotion_status, p.publication_status,
                   EXISTS (
                       SELECT 1
                       FROM lint_finding f
                       JOIN lint_policy_rule r
                         ON r.lint_policy_rule_id = f.lint_policy_rule_id
                       WHERE f.knowledge_item_id = n.node_id
                         AND f.resolved_at IS NULL
                         AND r.severity = 'BLOCKING'
                   ) AS has_blocking_lint
            FROM node n
            JOIN node_type t ON t.node_type_id = n.node_type_id
            JOIN knowledge_item k ON k.knowledge_item_id = n.node_id
            JOIN promotion_batch p ON p.promotion_batch_id = k.promotion_batch_id
            LEFT JOIN node_alias a ON a.node_id = n.node_id AND a.is_preferred
            WHERE n.node_id IN :ids
        """),
        {"ids": ids},
    ).mappings()
    result: dict[int, RelatedNode] = {}
    for row in rows:
        same_generation = int(row["promotion_batch_id"]) == current_batch_id
        if not (
            row["current_state"] in _PUBLIC_STATES
            and row["promotion_status"] == "COMMITTED"
            and (same_generation or row["publication_status"] == "READY")
            and not bool(row["has_blocking_lint"])
        ):
            continue
        result[int(row["node_id"])] = RelatedNode(
            node_id=int(row["node_id"]),
            node_type=str(row["node_type_code"]),
            preferred_alias=row["preferred_alias"],
        )
    try:
        references = reference_topic_identities(session, ids)
    except ReferenceTopicIntegrityError as error:
        raise InsightPreparationError(str(error)) from error
    result.update(
        {
            reference.node_id: RelatedNode(
                node_id=reference.node_id,
                node_type="TOPIC",
                preferred_alias=reference.canonical_display_name,
            )
            for reference in references.values()
        }
    )
    return result


def _direct_connections(
    session: Session,
    *,
    node_id: int,
    basis_ids: tuple[int, ...],
    current_batch_id: int,
) -> dict[int, tuple[ClaimConnection, ...]]:
    """Resolve direct meaning available in the current frozen schema."""
    if not basis_ids:
        return {}
    basis = set(basis_ids)
    connections: dict[int, list[ClaimConnection]] = defaultdict(list)

    relation_rows = (
        session.execute(
            _ids_statement("""
            SELECT cr.claim_id, cr.stance,
                   CASE WHEN r.source_node_id = :node_id
                        THEN r.target_node_id
                        ELSE r.source_node_id
                   END AS related_node_id,
                   rev.display_name
            FROM claim_relation cr
            JOIN relation r ON r.relation_id = cr.relation_id
            JOIN relation_type_revision rev
              ON rev.relation_type_revision_id = r.relation_type_revision_id
            WHERE cr.claim_id IN :ids
              AND r.relation_id IN :ids
              AND (r.source_node_id = :node_id OR r.target_node_id = :node_id)
            ORDER BY cr.claim_id, r.relation_id
        """),
            {"ids": list(basis_ids), "node_id": node_id},
        )
        .mappings()
        .all()
    )
    related = _node_identities(
        session,
        [int(row["related_node_id"]) for row in relation_rows],
        current_batch_id=current_batch_id,
    )
    for row in relation_rows:
        claim_id = int(row["claim_id"])
        identity = related.get(int(row["related_node_id"]))
        if claim_id not in basis or identity is None:
            continue
        connections[claim_id].append(
            ClaimConnection(
                kind="RELATION",
                label=str(row["display_name"]),
                stance=row["stance"],
                related_node=identity,
            )
        )

    attribute_rows = session.execute(
        _ids_statement("""
            SELECT v.claim_id, r.display_name
            FROM claim_attribute_value v
            JOIN attribute_revision r
              ON r.attribute_revision_id = v.attribute_revision_id
            WHERE v.claim_id IN :ids AND v.target_node_id = :node_id
            ORDER BY v.claim_id, v.claim_attribute_value_id
        """),
        {"ids": list(basis_ids), "node_id": node_id},
    ).mappings()
    for row in attribute_rows:
        connections[int(row["claim_id"])].append(
            ClaimConnection(kind="ATTRIBUTE", label=str(row["display_name"]))
        )

    event_rows = session.execute(
        _ids_statement("""
            SELECT claim_id
            FROM event_temporal_basis
            WHERE claim_id IN :ids AND event_node_id = :node_id
            ORDER BY claim_id
        """),
        {"ids": list(basis_ids), "node_id": node_id},
    ).mappings()
    for row in event_rows:
        connections[int(row["claim_id"])].append(
            ClaimConnection(kind="EVENT_TIME", label="사건 시점")
        )
    return {claim_id: tuple(items) for claim_id, items in connections.items()}


def _direct_relation_ids(
    session: Session,
    *,
    node_id: int,
    basis_ids: tuple[int, ...],
    current_batch_id: int,
) -> frozenset[int]:
    """Return direct basis relations with an approved public endpoint identity."""
    if not basis_ids:
        return frozenset()
    rows = (
        session.execute(
            _ids_statement("""
                SELECT r.relation_id,
                       CASE WHEN r.source_node_id = :node_id
                            THEN r.target_node_id
                            ELSE r.source_node_id
                       END AS related_node_id
                FROM relation r
                WHERE r.relation_id IN :ids
                  AND (r.source_node_id = :node_id OR r.target_node_id = :node_id)
                ORDER BY r.relation_id
            """),
            {"ids": list(basis_ids), "node_id": node_id},
        )
        .mappings()
        .all()
    )
    related = _node_identities(
        session,
        [int(row["related_node_id"]) for row in rows],
        current_batch_id=current_batch_id,
    )
    return frozenset(
        int(row["relation_id"])
        for row in rows
        if int(row["related_node_id"]) in related
    )


def _grounded_claims(
    session: Session,
    *,
    connections: dict[int, tuple[ClaimConnection, ...]],
    window: TimeWindow,
    as_of_at: datetime,
) -> tuple[GroundedClaim, ...]:
    claim_ids = sorted(connections)
    if not claim_ids:
        return ()
    claim_rows = {
        int(row["claim_id"]): row
        for row in session.execute(
            _ids_statement("""
                SELECT claim_id, statement_text, modality
                FROM claim WHERE claim_id IN :ids
            """),
            {"ids": claim_ids},
        ).mappings()
    }
    evidence: dict[int, list[EvidenceExcerpt]] = defaultdict(list)
    start_at = window.start_at(as_of_at)
    rows = session.execute(
        _ids_statement("""
            SELECT co.claim_id, o.observation_id, o.source_document_id,
                   d.evidence_group_id, d.publisher_name, d.title,
                   o.quote_text, d.published_at
            FROM claim_observation co
            JOIN observation o ON o.observation_id = co.observation_id
            JOIN source_document d ON d.source_document_id = o.source_document_id
            WHERE co.claim_id IN :ids
            ORDER BY co.claim_id, o.observation_id
        """),
        {"ids": claim_ids},
    ).mappings()
    for row in rows:
        published_at = row["published_at"]
        if published_at is not None and published_at >= as_of_at:
            continue
        period_role: PeriodRole
        if published_at is None:
            period_role = "UNKNOWN"
        elif published_at >= start_at:
            period_role = "IN_WINDOW"
        else:
            period_role = "BACKGROUND"
        evidence[int(row["claim_id"])].append(
            EvidenceExcerpt(
                observation_id=int(row["observation_id"]),
                source_document_id=int(row["source_document_id"]),
                evidence_group_id=int(row["evidence_group_id"]),
                publisher_name=str(row["publisher_name"]),
                title=str(row["title"]),
                quote_text=str(row["quote_text"]),
                published_at=published_at,
                period_role=period_role,
            )
        )

    result: list[GroundedClaim] = []
    for claim_id in claim_ids:
        claim_row = claim_rows.get(claim_id)
        excerpts = evidence.get(claim_id, [])
        if claim_row is None or not excerpts:
            continue
        roles = {item.period_role for item in excerpts}
        claim_period_role: PeriodRole
        if "IN_WINDOW" in roles:
            claim_period_role = "IN_WINDOW"
        elif "BACKGROUND" in roles:
            claim_period_role = "BACKGROUND"
        else:
            claim_period_role = "UNKNOWN"
        result.append(
            GroundedClaim(
                claim_id=claim_id,
                statement_text=str(claim_row["statement_text"]),
                modality=claim_row["modality"],
                period_role=claim_period_role,
                connections=connections[claim_id],
                evidence=tuple(excerpts),
            )
        )
    return tuple(result)


def _visible_conflicts(
    session: Session,
    *,
    node_id: int,
    claim_ids: frozenset[int],
    direct_relation_ids: frozenset[int],
) -> tuple[VisibleConflictPair, ...]:
    """Return only #130 conflicts whose semantic target belongs to this node."""
    if not claim_ids:
        return ()
    rows = session.execute(
        sa.text("""
        SELECT c.conflict_set_id, c.relation_id, c.target_node_id,
               c.event_node_id, m.claim_id
        FROM conflict_set c
        JOIN conflict_member m ON m.conflict_set_id = c.conflict_set_id
        WHERE c.current_state IN ('AGENT_PROPOSED', 'HUMAN_CONFIRMED')
        ORDER BY c.conflict_set_id, m.claim_id
    """)
    ).mappings()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["conflict_set_id"])].append(dict(row))
    result: list[VisibleConflictPair] = []
    for conflict_id, members in grouped.items():
        if len(members) != 2:
            continue
        target = members[0]
        relation_id = target["relation_id"]
        target_is_direct = bool(
            target["target_node_id"] == node_id
            or target["event_node_id"] == node_id
            or (relation_id is not None and int(relation_id) in direct_relation_ids)
        )
        member_ids = [int(row["claim_id"]) for row in members]
        if not target_is_direct or not set(member_ids) <= claim_ids:
            continue
        left, right = sorted(member_ids)
        result.append(
            VisibleConflictPair(
                conflict_set_id=conflict_id,
                claim_ids=(left, right),
            )
        )
    return tuple(result)


def _window_input(
    session: Session,
    *,
    node_id: int,
    connections: dict[int, tuple[ClaimConnection, ...]],
    direct_relation_ids: frozenset[int],
    window: TimeWindow,
    as_of_at: datetime,
) -> InsightWindowInput:
    claims = _grounded_claims(
        session,
        connections=connections,
        window=window,
        as_of_at=as_of_at,
    )
    conflicts = _visible_conflicts(
        session,
        node_id=node_id,
        claim_ids=frozenset(item.claim_id for item in claims),
        direct_relation_ids=direct_relation_ids,
    )
    return InsightWindowInput(
        time_window=cast(AgentTimeWindow, window.value),
        claims=claims,
        conflict_pairs=conflicts,
    )


def prepare_insight_bundle(
    session: Session,
    *,
    promotion_batch_id: int,
    node_id: int,
    as_of_at: datetime,
) -> PreparedInsightBundle:
    publication = _publication_snapshot(
        session,
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
    )
    document_id = int(publication["node_search_document_id"])
    basis_ids = _basis_ids(
        session,
        document_id=document_id,
        current_batch_id=promotion_batch_id,
    )
    center = _node_identities(
        session, (node_id,), current_batch_id=promotion_batch_id
    ).get(node_id)
    if center is None or node_id not in set(basis_ids):
        raise InsightPreparationError("center node is not publication-usable basis")
    connections = _direct_connections(
        session,
        node_id=node_id,
        basis_ids=basis_ids,
        current_batch_id=promotion_batch_id,
    )
    direct_relation_ids = _direct_relation_ids(
        session,
        node_id=node_id,
        basis_ids=basis_ids,
        current_batch_id=promotion_batch_id,
    )
    return PreparedInsightBundle(
        promotion_batch_id=promotion_batch_id,
        node_search_document_id=document_id,
        prior_insight_model_task_id=publication["node_insight_model_task_id"],
        basis_ids=basis_ids,
        agent_input=InsightAgentInput(
            node_id=node_id,
            node_type=center.node_type,
            preferred_alias=center.preferred_alias,
            as_of_at=as_of_at,
            recent_90_days=_window_input(
                session,
                node_id=node_id,
                connections=connections,
                direct_relation_ids=direct_relation_ids,
                window=TimeWindow.RECENT_90_DAYS,
                as_of_at=as_of_at,
            ),
            recent_1_year=_window_input(
                session,
                node_id=node_id,
                connections=connections,
                direct_relation_ids=direct_relation_ids,
                window=TimeWindow.RECENT_1_YEAR,
                as_of_at=as_of_at,
            ),
        ),
    )


def _task_for_update(session: Session, model_task_id: int) -> None:
    row = (
        session.execute(
            sa.text("""
                SELECT t.task_kind, t.model_version, t.prompt_version, t.status,
                       o.task_kind AS schema_task_kind, o.schema_json
                FROM model_task t
                JOIN output_schema_definition o
                  ON o.output_schema_definition_id = t.output_schema_definition_id
                WHERE t.model_task_id = :task_id
                FOR UPDATE OF t
            """),
            {"task_id": model_task_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise InsightTaskError("NODE_INSIGHT model_task does not exist")
    if (
        row["task_kind"] != "NODE_INSIGHT"
        or row["schema_task_kind"] != "NODE_INSIGHT"
        or row["model_version"] != product.MODEL_VERSION
        or row["prompt_version"] != product.PROMPT_VERSION
        or row["schema_json"] != product.output_schema()
    ):
        raise InsightTaskError("model_task does not match the active #68 contract")
    if row["status"] != "RUNNING":
        raise InsightTaskError("model_task must be RUNNING before product finalization")


def _lock_publication(
    session: Session,
    prepared: PreparedInsightBundle,
) -> bool:
    row = (
        session.execute(
            sa.text("""
                SELECT pan.node_search_document_id,
                       pan.node_insight_model_task_id,
                       pb.promotion_status, pb.publication_status
                FROM publication_affected_node pan
                JOIN promotion_batch pb
                  ON pb.promotion_batch_id = pan.promotion_batch_id
                WHERE pan.promotion_batch_id = :batch_id
                  AND pan.node_id = :node_id
                FOR UPDATE OF pan, pb
            """),
            {
                "batch_id": prepared.promotion_batch_id,
                "node_id": prepared.agent_input.node_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return False
    return bool(
        row["promotion_status"] == "COMMITTED"
        and row["publication_status"] == "PREPARING"
        and int(row["node_search_document_id"] or 0) == prepared.node_search_document_id
        and row["node_insight_model_task_id"] == prepared.prior_insight_model_task_id
    )


def _finish_task(
    session: Session,
    model_task_id: int,
    status: str,
    finished_at: datetime,
) -> None:
    updated_task_id = session.scalar(
        s.model_task.update()
        .where(
            s.model_task.c.model_task_id == model_task_id,
            s.model_task.c.status == "RUNNING",
        )
        .values(
            status=status,
            next_attempt_at=None,
            lease_owner=None,
            lease_expires_at=None,
            finished_at=finished_at,
        )
        .returning(s.model_task.c.model_task_id)
    )
    if updated_task_id is None:
        raise InsightTaskError("model_task changed during product finalization")


def _insert_report(
    session: Session,
    *,
    prepared: PreparedInsightBundle,
    model_task_id: int,
    time_window: AgentTimeWindow,
    report: InsightReportCandidate,
) -> int:
    report_id = session.scalar(
        s.node_insight.insert()
        .values(
            node_id=prepared.agent_input.node_id,
            node_search_document_id=prepared.node_search_document_id,
            model_task_id=model_task_id,
            time_window=time_window,
            as_of_at=prepared.agent_input.as_of_at,
            slot=1,
            title=report.title,
            summary_text=report.summary_text,
            synthesis_text=report.synthesis_text,
            caveat_text=report.caveat_text,
        )
        .returning(s.node_insight.c.node_insight_id)
    )
    if report_id is None:
        raise InsightTaskError("report insert returned no identity")

    projection = product.report_claim_projection(report)
    session.execute(
        s.node_insight_claim.insert(),
        [
            {
                "node_insight_id": report_id,
                "claim_id": claim_id,
                "role": role,
                "display_order": display_order,
            }
            for claim_id, role, display_order in projection
        ],
    )
    for section in sorted(report.sections, key=lambda item: item.display_order):
        section_id = session.scalar(
            s.node_insight_section.insert()
            .values(
                node_insight_id=report_id,
                display_order=section.display_order,
                title=section.title,
                synthesis_text=section.synthesis_text,
                caveat_text=section.caveat_text,
            )
            .returning(s.node_insight_section.c.section_id)
        )
        if section_id is None:
            raise InsightTaskError("section insert returned no identity")
        session.execute(
            s.node_insight_section_claim.insert(),
            [
                {
                    "section_id": section_id,
                    "claim_id": reference.claim_id,
                    "role": reference.role,
                    "display_order": reference.display_order,
                }
                for reference in sorted(
                    section.claims, key=lambda item: item.display_order
                )
            ],
        )
    return int(report_id)


def _insert_window(
    session: Session,
    *,
    prepared: PreparedInsightBundle,
    model_task_id: int,
    time_window: AgentTimeWindow,
    report_id: int | None,
) -> None:
    session.execute(
        s.node_insight_window.insert().values(
            node_id=prepared.agent_input.node_id,
            node_search_document_id=prepared.node_search_document_id,
            model_task_id=model_task_id,
            time_window=time_window,
            as_of_at=prepared.agent_input.as_of_at,
            node_insight_id=report_id,
        )
    )


def apply_insight_bundle(
    session: Session,
    *,
    model_task_id: int,
    prepared: PreparedInsightBundle,
    proposal: InsightBundleProposal,
    finished_at: datetime,
) -> InsightApplyResult:
    """Atomically finalize both windows and the publication task pointer.

    The caller owns the surrounding short transaction. This function never
    commits, sends provider requests or records agent_attempt rows.
    """
    _task_for_update(session, model_task_id)
    if not _lock_publication(session, prepared):
        _finish_task(session, model_task_id, "VALIDATION_BLOCKED", finished_at)
        return InsightApplyResult(
            status="VALIDATION_BLOCKED",
            report_ids=(None, None),
            reason="STALE_PUBLICATION",
        )
    try:
        current = prepare_insight_bundle(
            session,
            promotion_batch_id=prepared.promotion_batch_id,
            node_id=prepared.agent_input.node_id,
            as_of_at=prepared.agent_input.as_of_at,
        )
    except InsightPreparationError:
        current = None
    if current != prepared:
        _finish_task(session, model_task_id, "VALIDATION_BLOCKED", finished_at)
        return InsightApplyResult(
            status="VALIDATION_BLOCKED",
            report_ids=(None, None),
            reason="STALE_INPUT",
        )

    validated = product.validate_bundle(prepared, proposal)
    if not validated.valid:
        _finish_task(session, model_task_id, "VALIDATION_BLOCKED", finished_at)
        return InsightApplyResult(
            status="VALIDATION_BLOCKED",
            report_ids=(None, None),
            reason="REPORT_VALIDATION_BLOCKED",
        )

    reports: tuple[tuple[AgentTimeWindow, InsightReportCandidate | None], ...] = (
        ("RECENT_90_DAYS", validated.recent_90_days),
        ("RECENT_1_YEAR", validated.recent_1_year),
    )
    report_ids: list[int | None] = []
    for time_window, report in reports:
        report_id = (
            _insert_report(
                session,
                prepared=prepared,
                model_task_id=model_task_id,
                time_window=time_window,
                report=report,
            )
            if report is not None
            else None
        )
        _insert_window(
            session,
            prepared=prepared,
            model_task_id=model_task_id,
            time_window=time_window,
            report_id=report_id,
        )
        report_ids.append(report_id)

    updated_node_id = session.scalar(
        s.publication_affected_node.update()
        .where(
            s.publication_affected_node.c.promotion_batch_id
            == prepared.promotion_batch_id,
            s.publication_affected_node.c.node_id == prepared.agent_input.node_id,
        )
        .values(node_insight_model_task_id=model_task_id)
        .returning(s.publication_affected_node.c.node_id)
    )
    if updated_node_id is None:
        raise InsightTaskError("publication pointer update returned no row")
    _finish_task(session, model_task_id, "SUCCESS", finished_at)
    return InsightApplyResult(
        status="SUCCESS",
        report_ids=(report_ids[0], report_ids[1]),
    )
