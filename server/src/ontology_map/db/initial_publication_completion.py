"""#129/#68 consumer integration and existing READY gate for issue #215."""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import followup_generation as followup_product
from ontology_map.db import followup_generation as followup_db
from ontology_map.db import insight_generation as insight_db
from ontology_map.db import schema as s
from ontology_map.db.initial_publication_context import prepare_node_context
from ontology_map.db.initial_publication_contracts import (
    InitialPublicationError,
    PreparedDerivedWork,
    PublicationNotReady,
    PublicationReadiness,
    PublicationStateError,
)
from ontology_map.db.initial_publication_start import membership

_REQUIRED_WINDOWS = {"RECENT_90_DAYS", "RECENT_1_YEAR"}


def prepare_derived_work(
    session: Session,
    *,
    promotion_batch_id: int,
    node_id: int,
) -> PreparedDerivedWork:
    """Consume the merged #129/#68 preparation boundaries for this generation."""
    row = (
        session.execute(
            sa.text(
                """
                SELECT pan.node_context_id, pb.committed_at,
                       pb.promotion_status, pb.publication_status
                FROM publication_affected_node pan
                JOIN promotion_batch pb
                  ON pb.promotion_batch_id = pan.promotion_batch_id
                WHERE pan.promotion_batch_id = :batch_id
                  AND pan.node_id = :node_id
                """
            ),
            {"batch_id": promotion_batch_id, "node_id": node_id},
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or row["promotion_status"] != "COMMITTED"
        or row["publication_status"] != "PREPARING"
        or row["node_context_id"] is None
        or row["committed_at"] is None
    ):
        raise PublicationStateError(
            "derived generation requires a completed PREPARING NODE_CONTEXT"
        )

    context_id = int(row["node_context_id"])
    as_of_at = row["committed_at"]
    return PreparedDerivedWork(
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
        as_of_at=as_of_at,
        followup_bundle=followup_db.prepare_followup(
            session,
            context_id,
            as_of_at,
        ),
        insight_bundle=insight_db.prepare_insight_bundle(
            session,
            promotion_batch_id=promotion_batch_id,
            node_id=node_id,
            as_of_at=as_of_at,
        ),
    )


def _node_readiness_reasons(
    session: Session,
    *,
    batch_id: int,
    node_id: int,
    committed_at: datetime,
) -> list[str]:
    prefix = f"node {node_id}: "
    pan = (
        session.execute(
            sa.text(
                """
                SELECT node_search_document_id, node_context_id,
                       node_insight_model_task_id
                FROM publication_affected_node
                WHERE promotion_batch_id = :batch_id
                  AND node_id = :node_id
                """
            ),
            {"batch_id": batch_id, "node_id": node_id},
        )
        .mappings()
        .one()
    )
    if pan["node_search_document_id"] is None:
        return [prefix + "search document missing"]
    if pan["node_context_id"] is None:
        return [prefix + "NODE_CONTEXT missing"]

    reasons: list[str] = []
    insight_task_id = (
        None
        if pan["node_insight_model_task_id"] is None
        else int(pan["node_insight_model_task_id"])
    )
    if insight_task_id is None:
        reasons.append(prefix + "NODE_INSIGHT task pointer missing")

    try:
        prepared_context = prepare_node_context(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
    except InitialPublicationError as exc:
        reasons.append(prefix + f"search/context basis stale: {exc}")
        prepared_context = None

    context_row = (
        session.execute(
            sa.text(
                """
                SELECT c.model_task_id, t.task_kind, t.status, t.input_hash
                FROM node_context c
                JOIN model_task t ON t.model_task_id = c.model_task_id
                WHERE c.node_context_id = :context_id
                  AND c.node_id = :node_id
                  AND c.node_search_document_id = :document_id
                """
            ),
            {
                "context_id": int(pan["node_context_id"]),
                "node_id": node_id,
                "document_id": int(pan["node_search_document_id"]),
            },
        )
        .mappings()
        .one_or_none()
    )
    if (
        context_row is None
        or context_row["task_kind"] != "NODE_CONTEXT"
        or context_row["status"] != "SUCCESS"
        or prepared_context is None
        or bytes(context_row["input_hash"]) != prepared_context.input_hash
    ):
        reasons.append(prefix + "NODE_CONTEXT is not current SUCCESS")

    followup_rows = (
        session.execute(
            sa.text(
                """
                SELECT qs.time_window, qs.as_of_at, qs.model_task_id,
                       t.task_kind, t.status, t.prompt_version
                FROM node_question_set qs
                JOIN model_task t ON t.model_task_id = qs.model_task_id
                WHERE qs.node_context_id = :context_id
                ORDER BY qs.time_window, qs.model_task_id
                """
            ),
            {"context_id": int(pan["node_context_id"])},
        )
        .mappings()
        .all()
    )
    successful_followups = [
        row
        for row in followup_rows
        if row["task_kind"] == "FOLLOWUP_QUESTIONS"
        and row["status"] == "SUCCESS"
        and row["as_of_at"] == committed_at
    ]
    followup_windows = {str(row["time_window"]) for row in successful_followups}
    followup_task_ids = {int(row["model_task_id"]) for row in successful_followups}
    prompt_versions = {str(row["prompt_version"]) for row in successful_followups}
    valid_task_shape = (
        (
            len(followup_task_ids) == 2
            and prompt_versions == {"followup-questions-129-v2"}
        )
        or (
            len(followup_task_ids) == 1
            and prompt_versions == {"followup-questions-129-v3"}
        )
        or (
            len(followup_task_ids) == 1
            and prompt_versions == {followup_product.PROMPT_VERSION}
        )
    )
    if (
        len(followup_rows) != 2
        or len(successful_followups) != 2
        or followup_windows != _REQUIRED_WINDOWS
        or not valid_task_shape
    ):
        reasons.append(prefix + "FOLLOWUP window bundle is not current SUCCESS")

    if insight_task_id is not None:
        insight_task = (
            session.execute(
                sa.text(
                    """
                    SELECT task_kind, status
                    FROM model_task
                    WHERE model_task_id = :task_id
                    """
                ),
                {"task_id": insight_task_id},
            )
            .mappings()
            .one_or_none()
        )
        if (
            insight_task is None
            or insight_task["task_kind"] != "NODE_INSIGHT"
            or insight_task["status"] != "SUCCESS"
        ):
            reasons.append(prefix + "NODE_INSIGHT task is not SUCCESS")

        insight_rows = (
            session.execute(
                sa.text(
                    """
                    SELECT time_window, as_of_at
                    FROM node_insight_window
                    WHERE node_id = :node_id
                      AND node_search_document_id = :document_id
                      AND model_task_id = :task_id
                    ORDER BY time_window
                    """
                ),
                {
                    "node_id": node_id,
                    "document_id": int(pan["node_search_document_id"]),
                    "task_id": insight_task_id,
                },
            )
            .mappings()
            .all()
        )
        insight_windows = {
            str(row["time_window"])
            for row in insight_rows
            if row["as_of_at"] == committed_at
        }
        if len(insight_rows) != 2 or insight_windows != _REQUIRED_WINDOWS:
            reasons.append(prefix + "NODE_INSIGHT atomic window bundle is incomplete")
    return reasons


def publication_readiness(
    session: Session,
    batch_id: int,
) -> PublicationReadiness:
    """Evaluate existing #46/#163 completeness without redefining normal empty."""
    batch = (
        session.execute(
            sa.text(
                """
                SELECT promotion_status, publication_status, committed_at
                FROM promotion_batch
                WHERE promotion_batch_id = :batch_id
                """
            ),
            {"batch_id": batch_id},
        )
        .mappings()
        .one_or_none()
    )
    if batch is None:
        return PublicationReadiness(False, ("promotion batch does not exist",))
    if batch["promotion_status"] != "COMMITTED":
        return PublicationReadiness(False, ("promotion is not COMMITTED",))
    if batch["publication_status"] == "READY":
        return PublicationReadiness(True, ())
    if batch["publication_status"] != "PREPARING" or batch["committed_at"] is None:
        return PublicationReadiness(False, ("publication is not PREPARING",))

    # Phase A froze this generation's membership in publication_affected_node.
    # READY validation consumes that authoritative set and never re-projects
    # #216 provenance after PREPARING.
    existing = membership(session, batch_id)
    reasons: list[str] = []
    committed_at = batch["committed_at"]
    for node_id in existing:
        reasons.extend(
            _node_readiness_reasons(
                session,
                batch_id=batch_id,
                node_id=node_id,
                committed_at=committed_at,
            )
        )
    return PublicationReadiness(not reasons, tuple(reasons))


def _lock_batch(session: Session, batch_id: int) -> dict[str, object]:
    row = (
        session.execute(
            sa.text(
                """
                SELECT promotion_status, publication_status
                FROM promotion_batch
                WHERE promotion_batch_id = :batch_id
                FOR UPDATE
                """
            ),
            {"batch_id": batch_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise PublicationStateError("promotion batch does not exist")
    return dict(row)


def mark_publication_ready(session: Session, batch_id: int) -> bool:
    """Validate completeness and atomically transition PREPARING to READY."""
    if not session.in_transaction():
        raise PublicationStateError("caller-owned transaction is required")

    batch = _lock_batch(session, batch_id)
    if batch["promotion_status"] != "COMMITTED":
        raise PublicationStateError("READY requires COMMITTED promotion")
    if batch["publication_status"] == "READY":
        return False
    if batch["publication_status"] != "PREPARING":
        raise PublicationStateError("READY transition requires PREPARING publication")

    readiness = publication_readiness(session, batch_id)
    if not readiness.ready:
        raise PublicationNotReady(readiness.reasons)

    updated = session.scalar(
        s.promotion_batch.update()
        .where(
            s.promotion_batch.c.promotion_batch_id == batch_id,
            s.promotion_batch.c.promotion_status == "COMMITTED",
            s.promotion_batch.c.publication_status == "PREPARING",
        )
        .values(
            publication_status="READY",
            ready_at=sa.func.current_timestamp(),
        )
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    if updated is None:
        raise PublicationStateError("READY transition lost its locked state")
    return True


__all__ = [
    "mark_publication_ready",
    "prepare_derived_work",
    "publication_readiness",
]
