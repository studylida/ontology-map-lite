from datetime import UTC, datetime

import sqlalchemy as sa
from test_relations import rollback_session

from ontology_map.db import followup_generation as db
from ontology_map.db import panel as panel_queries
from ontology_map.db import schema as s
from ontology_map.db.panel_fixture import load_panel_fixture


def _insert_relation_conflict(
    session,
    *,
    relation_id: int,
    claim_ids: tuple[int, int],
) -> int:
    conflict_id = session.scalar(
        s.conflict_set.insert()
        .values(
            relation_id=relation_id,
            modality="FACT",
            current_state="AGENT_PROPOSED",
        )
        .returning(s.conflict_set.c.conflict_set_id)
    )
    assert conflict_id is not None
    session.execute(
        s.conflict_member.insert(),
        [
            {
                "conflict_set_id": conflict_id,
                "claim_id": claim_ids[0],
                "position_key": "A",
            },
            {
                "conflict_set_id": conflict_id,
                "claim_id": claim_ids[1],
                "position_key": "B",
            },
        ],
    )
    return int(conflict_id)


def _make_context_preparing(session, context_id: int) -> None:
    batch_id = session.scalar(
        sa.select(s.publication_affected_node.c.promotion_batch_id).where(
            s.publication_affected_node.c.node_context_id == context_id
        )
    )
    assert batch_id is not None
    session.execute(
        s.promotion_batch.update()
        .where(s.promotion_batch.c.promotion_batch_id == batch_id)
        .values(publication_status="PREPARING", ready_at=None)
    )


def test_prepare_followup_only_exposes_conflicts_for_center_node_scope() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = panel_queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        _make_context_preparing(session, context_id)
        now = datetime.now(UTC)
        initial = db.prepare_followup(
            session,
            context_id,
            now,
        )
        claim_ids = tuple(
            item.claim_id for item in initial.agent_input.recent_90_days.claims[:2]
        )
        assert len(claim_ids) == 2

        direct_relation_id = session.scalar(
            sa.select(s.relation.c.relation_id)
            .where(
                s.relation.c.relation_id.in_(initial.basis_ids),
                sa.or_(
                    s.relation.c.source_node_id == ids["gaon"],
                    s.relation.c.target_node_id == ids["gaon"],
                ),
            )
            .order_by(s.relation.c.relation_id)
            .limit(1)
        )
        unrelated_relation_id = session.scalar(
            sa.select(s.relation.c.relation_id)
            .where(
                s.relation.c.source_node_id != ids["gaon"],
                s.relation.c.target_node_id != ids["gaon"],
            )
            .order_by(s.relation.c.relation_id)
            .limit(1)
        )
        assert direct_relation_id is not None
        assert unrelated_relation_id is not None

        out_of_scope_id = _insert_relation_conflict(
            session,
            relation_id=int(unrelated_relation_id),
            claim_ids=(claim_ids[0], claim_ids[1]),
        )
        in_scope_id = _insert_relation_conflict(
            session,
            relation_id=int(direct_relation_id),
            claim_ids=(claim_ids[0], claim_ids[1]),
        )

        prepared = db.prepare_followup(
            session,
            context_id,
            now,
        )
        visible = {
            pair.conflict_set_id
            for pair in prepared.agent_input.recent_90_days.conflict_pairs
        }
        assert in_scope_id in visible
        assert out_of_scope_id not in visible
