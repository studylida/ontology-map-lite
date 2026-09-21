import os

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db.ontology_reference_data import (
    activate_approved_ontology_reference_data,
)
from ontology_map.db.session import get_engine
from ontology_map.db.topic_reference_schema import APPROVED_TOPIC_DEFINITIONS
from ontology_map.db.topic_references import (
    ensure_topic_reference,
    list_topic_references,
)
from ontology_map.exploration import TimeWindow
from ontology_map.search import search_nodes
from ontology_map.topic_exploration import get_topic_exploration

pytestmark = pytest.mark.skipif(
    os.getenv("ONTOLOGY_MAP_TOPIC_REFERENCE_TEST") != "1",
    reason="#206 Topic list 실제 PostgreSQL 검증에서 실행한다.",
)


def test_topic_list_read_keeps_active_and_inactive_reference_rows() -> None:
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection) as session:
                session.execute(sa.text("SELECT 1"))
                first = ensure_topic_reference(
                    session,
                    topic_code="SEMICONDUCTOR",
                    canonical_display_name="반도체",
                    is_active=True,
                )
                second = ensure_topic_reference(
                    session,
                    topic_code="INVESTMENT",
                    canonical_display_name="투자",
                    is_active=False,
                )

                rows = list_topic_references(session)
                selected = {row.node_id: row for row in rows}

                assert selected[first.node_id].canonical_display_name == "반도체"
                assert selected[first.node_id].is_active is True
                assert selected[second.node_id].canonical_display_name == "투자"
                assert selected[second.node_id].is_active is False
        finally:
            transaction.rollback()


def test_approved_activation_is_readable_hidden_from_search_and_normally_empty() -> (
    None
):
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            with Session(
                bind=connection,
                join_transaction_mode="create_savepoint",
            ) as session:
                with session.begin():
                    activation = activate_approved_ontology_reference_data(session)

                rows = list_topic_references(session)
                assert len(rows) == 9
                assert {
                    (row.topic_code, row.canonical_display_name) for row in rows
                } == set(APPROVED_TOPIC_DEFINITIONS)
                assert all(row.is_active for row in rows)

                for _topic_code, canonical_display_name in APPROVED_TOPIC_DEFINITIONS:
                    assert search_nodes(session, canonical_display_name, limit=20) == []

                topic_node_id = activation.topic_node_ids["SEMICONDUCTOR"]
                exploration = get_topic_exploration(
                    session,
                    topic_node_id,
                    TimeWindow.RECENT_90_DAYS,
                )
                assert exploration.total_public_membership_count == 0
                assert exploration.recent_member_count == 0
                assert exploration.recent_activity_evidence_group_count == 0
                assert [node.node_id for node in exploration.graph.nodes] == [
                    topic_node_id
                ]
                assert exploration.graph.relations == []
        finally:
            transaction.rollback()
