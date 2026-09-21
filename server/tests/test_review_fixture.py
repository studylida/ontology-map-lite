import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.review_fixture import load_review_fixture
from ontology_map.db.session import get_engine
from ontology_map.exploration import TimeWindow, get_exploration, list_peripheral_nodes


def test_review_fixture_preserves_existing_data_and_real_evidence() -> None:
    _, original = load_hbf_fixture()
    _, ids = load_review_fixture()
    with get_engine().connect() as connection:
        before = connection.scalar(
            sa.select(sa.func.count()).select_from(s.knowledge_item)
        )
    created, repeated = load_review_fixture()
    assert not created and repeated == ids and len(ids) == 100
    assert set(original.values()).isdisjoint(ids.values())
    with Session(get_engine()) as session:
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(s.knowledge_item))
            == before
        )
        view = get_exploration(session, ids["sk"], TimeWindow.RECENT_90_DAYS)
        assert {1, 3, 6} <= {
            r.supporting_evidence_group_count for r in view.graph.relations
        }
        assert any(r.has_conflict for r in view.graph.relations)
        tiers = {n.node_id: n.tier for n in view.graph.nodes}
        assert "THREE_HOP" in tiers.values()
        distances = {view.center_node_id: 0}
        for depth in range(1, 4):
            for relation in view.graph.relations:
                a, b = relation.source_node_id, relation.target_node_id
                if distances.get(a) == depth - 1:
                    distances.setdefault(b, depth)
                if distances.get(b) == depth - 1:
                    distances.setdefault(a, depth)
        expected = {"CENTER": 0, "DIRECT": 1, "TWO_HOP": 2, "THREE_HOP": 3}
        assert all(
            distances[node_id] == expected[tier] for node_id, tier in tiers.items()
        )
        first = list_peripheral_nodes(
            session, ids["sk"], TimeWindow.RECENT_90_DAYS, cursor=None, limit=20
        )
        assert len(first.graph.nodes) == 20 and first.next_cursor is not None
        second = list_peripheral_nodes(
            session,
            ids["sk"],
            TimeWindow.RECENT_90_DAYS,
            cursor=first.next_cursor,
            limit=20,
        )
        assert len(second.graph.nodes) == 20
        assert {n.node_id for n in first.graph.nodes}.isdisjoint(
            n.node_id for n in second.graph.nodes
        )
