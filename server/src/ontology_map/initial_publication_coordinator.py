"""Minimal durable initial-publication orchestration for issue #215 Phase D.

This module owns dependency order and READY integration only. Task identity,
provider lease/retry/call-slot accounting and product validation stay in the
NODE_CONTEXT, #129 FOLLOWUP, #68 NODE_INSIGHT and shared #127 boundaries.
"""

from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import followup_tasks, insight_tasks, schema
from ontology_map.db import initial_publication as publication
from ontology_map.followup_runner import (
    ProviderPreflight as FollowupProviderPreflight,
)
from ontology_map.followup_runner import run_followup
from ontology_map.insight_runner import ProviderPreflight as InsightProviderPreflight
from ontology_map.insight_runner import run_insight
from ontology_map.node_context_runner import (
    ProviderPreflight as NodeContextProviderPreflight,
)
from ontology_map.node_context_runner import run_node_context
from ontology_map.pilot_budget import current_pilot


@dataclass(frozen=True)
class InitialPublicationRunResult:
    promotion_batch_id: int
    publication_status: str
    affected_node_ids: tuple[int, ...]
    ready: bool
    reasons: tuple[str, ...]


def _publication_node(
    session: Session,
    batch_id: int,
    node_id: int,
) -> tuple[int | None, datetime]:
    row = (
        session.execute(
            sa.select(
                schema.publication_affected_node.c.node_context_id,
                schema.promotion_batch.c.committed_at,
            )
            .select_from(
                schema.publication_affected_node.join(
                    schema.promotion_batch,
                    schema.promotion_batch.c.promotion_batch_id
                    == schema.publication_affected_node.c.promotion_batch_id,
                )
            )
            .where(
                schema.publication_affected_node.c.promotion_batch_id == batch_id,
                schema.publication_affected_node.c.node_id == node_id,
            )
        )
        .mappings()
        .one()
    )
    committed_at = row["committed_at"]
    if committed_at is None:
        raise publication.PublicationStateError(
            "initial publication requires committed_at"
        )
    context_id = row["node_context_id"]
    return (
        None if context_id is None else int(context_id),
        committed_at,
    )


def _status(session: Session, batch_id: int) -> str:
    return str(
        session.execute(
            sa.select(schema.promotion_batch.c.publication_status).where(
                schema.promotion_batch.c.promotion_batch_id == batch_id
            )
        ).scalar_one()
    )


def run_initial_publication(
    engine: sa.Engine,
    batch_id: int,
    worker_name: str,
    *,
    prepare_node_context_provider: NodeContextProviderPreflight,
    prepare_followup_provider: FollowupProviderPreflight,
    prepare_insight_provider: InsightProviderPreflight,
) -> InitialPublicationRunResult:
    """Advance one initial publication generation through current durable runners.

    One invocation gives each claimable durable task at most one provider slot.
    A subsequent invocation resumes the same membership/tasks and lets each
    task's own durable lifecycle decide whether it is claimable. No task status
    is reset or reclassified here.
    """
    with Session(engine) as session, session.begin():
        started = publication.start_initial_publication(session, batch_id)
    affected = started.affected_node_ids
    if started.publication_status == "READY":
        return InitialPublicationRunResult(batch_id, "READY", affected, True, ())
    with Session(engine) as session, session.begin():
        existing = publication.publication_readiness(session, batch_id)
        if existing.ready:
            publication.mark_publication_ready(session, batch_id)
            return InitialPublicationRunResult(batch_id, "READY", affected, True, ())
    pilot = current_pilot(required=False)

    for node_id in affected:
        with Session(engine) as session, session.begin():
            publication.ensure_search_document(
                session,
                promotion_batch_id=batch_id,
                node_id=node_id,
            )
            prepared_context = publication.prepare_node_context(
                session,
                promotion_batch_id=batch_id,
                node_id=node_id,
            )
            context_task = publication.ensure_node_context_task(
                session,
                prepared_context,
            )

        run_node_context(
            engine,
            context_task.model_task_id,
            worker_name,
            promotion_batch_id=batch_id,
            node_id=node_id,
            prepare_provider=prepare_node_context_provider,
        )
        if pilot is not None:
            pilot.require_active()

        with Session(engine) as session:
            context_id, as_of_at = _publication_node(session, batch_id, node_id)
        if context_id is None:
            continue

        with Session(engine) as session, session.begin():
            followup = followup_tasks.enqueue_followup(
                session,
                context_id,
                as_of_at,
            )
            insight = insight_tasks.enqueue_insight(
                session,
                batch_id,
                node_id,
                as_of_at,
            )

        run_followup(
            engine,
            followup.task_id,
            worker_name,
            node_context_id=context_id,
            as_of_at=as_of_at,
            prepare_provider=prepare_followup_provider,
        )
        if pilot is not None:
            pilot.require_active()
        run_insight(
            engine,
            insight.task_id,
            worker_name,
            promotion_batch_id=batch_id,
            node_id=node_id,
            as_of_at=as_of_at,
            prepare_provider=prepare_insight_provider,
        )
        if pilot is not None:
            pilot.require_active()

    with Session(engine) as session, session.begin():
        readiness = publication.publication_readiness(session, batch_id)
        if readiness.ready:
            publication.mark_publication_ready(session, batch_id)
        status = _status(session, batch_id)
    return InitialPublicationRunResult(
        promotion_batch_id=batch_id,
        publication_status=status,
        affected_node_ids=affected,
        ready=status == "READY",
        reasons=() if status == "READY" else readiness.reasons,
    )
