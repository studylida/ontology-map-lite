import pytest
import sqlalchemy as sa

from ontology_map.db import fixture
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.schema import (
    followup_question,
    model_task,
    node_alias,
    node_insight,
    node_search_document,
    promotion_batch,
    publication_affected_node,
)
from ontology_map.db.session import get_engine

EXPECTED_NAMES = {
    "SK하이닉스",
    "SanDisk",
    "HBF",
    "UCIe",
    "FMS 2026 HBF 발표",
}


def test_hbf_fixture_rejects_non_development_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProductionSettings:
        environment = "production"

    monkeypatch.setattr(fixture, "get_settings", ProductionSettings)

    with pytest.raises(RuntimeError, match="development"):
        load_hbf_fixture()


def test_hbf_fixture_is_complete_and_idempotent() -> None:
    _created, first_ids = load_hbf_fixture()
    created_again, second_ids = load_hbf_fixture()

    assert not created_again
    assert second_ids == first_ids
    assert len(first_ids) == 5

    with get_engine().connect() as connection:
        names = set(
            connection.scalars(
                sa.select(node_alias.c.alias_text).where(node_alias.c.is_preferred)
            )
        )
        assert EXPECTED_NAMES <= names

        ready_nodes = connection.scalar(
            sa.select(sa.func.count())
            .select_from(publication_affected_node)
            .join(
                promotion_batch,
                promotion_batch.c.promotion_batch_id
                == publication_affected_node.c.promotion_batch_id,
            )
            .where(
                promotion_batch.c.promotion_status == "COMMITTED",
                promotion_batch.c.publication_status == "READY",
                publication_affected_node.c.node_search_document_id.is_not(None),
                publication_affected_node.c.node_context_id.is_not(None),
                publication_affected_node.c.node_insight_model_task_id.is_not(None),
            )
        )
        assert ready_nodes >= 5

        embedding_tasks = connection.scalar(
            sa.select(sa.func.count())
            .select_from(model_task)
            .where(model_task.c.task_kind == "EMBEDDING")
        )
        assert embedding_tasks == 0

        context_ids = connection.scalars(
            sa.select(publication_affected_node.c.node_context_id).where(
                publication_affected_node.c.node_id.in_(first_ids.values())
            )
        ).all()
        question_counts = connection.execute(
            sa.select(
                followup_question.c.node_context_id,
                sa.func.count(),
            )
            .where(followup_question.c.node_context_id.in_(context_ids))
            .group_by(followup_question.c.node_context_id)
        ).all()
        assert {int(count) for _context_id, count in question_counts} == {2}
        assert len(question_counts) == 5

        insight_windows = set(
            connection.scalars(
                sa.select(node_insight.c.time_window).where(
                    node_insight.c.node_id.in_(first_ids.values())
                )
            )
        )
        assert insight_windows == {"RECENT_90_DAYS", "RECENT_1_YEAR"}

        search_document_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(node_search_document)
            .where(node_search_document.c.node_id.in_(first_ids.values()))
        )
        assert search_document_count == 5
