from datetime import UTC, datetime

import sqlalchemy as sa
from test_relations import rollback_session

from ontology_map.db import insight_generation as db
from ontology_map.db import schema as s
from ontology_map.db.panel_fixture import load_panel_fixture


def _preparing_publication(session, node_id: int, now: datetime) -> int:
    ready = (
        session.execute(
            sa.select(s.publication_affected_node)
            .join(
                s.promotion_batch,
                s.promotion_batch.c.promotion_batch_id
                == s.publication_affected_node.c.promotion_batch_id,
            )
            .where(
                s.publication_affected_node.c.node_id == node_id,
                s.promotion_batch.c.promotion_status == "COMMITTED",
                s.promotion_batch.c.publication_status == "READY",
            )
            .order_by(
                s.promotion_batch.c.ready_at.desc(),
                s.promotion_batch.c.promotion_batch_id.desc(),
            )
            .limit(1)
        )
        .mappings()
        .one()
    )
    policy = session.scalar(
        sa.select(s.promotion_batch.c.lint_policy_version_id).where(
            s.promotion_batch.c.promotion_batch_id == ready["promotion_batch_id"]
        )
    )
    batch_id = session.scalar(
        s.promotion_batch.insert()
        .values(
            lint_policy_version_id=policy,
            promotion_status="COMMITTED",
            publication_status="PREPARING",
            started_at=now,
            committed_at=now,
        )
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    assert batch_id is not None
    affected = dict(ready)
    affected["promotion_batch_id"] = batch_id
    session.execute(s.publication_affected_node.insert().values(**affected))
    return int(batch_id)


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


def test_prepare_insight_only_exposes_conflicts_for_center_node_scope() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id = _preparing_publication(session, ids["gaon"], now)
        initial = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
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

        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        visible_90 = {
            pair.conflict_set_id
            for pair in prepared.agent_input.recent_90_days.conflict_pairs
        }
        visible_year = {
            pair.conflict_set_id
            for pair in prepared.agent_input.recent_1_year.conflict_pairs
        }
        assert in_scope_id in visible_90
        assert out_of_scope_id not in visible_90
        assert in_scope_id in visible_year
        assert out_of_scope_id not in visible_year
