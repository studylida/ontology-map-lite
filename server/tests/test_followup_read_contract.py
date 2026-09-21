from hashlib import sha256

import sqlalchemy as sa
from test_relations import rollback_session

from ontology_map.db import panel as queries
from ontology_map.db import schema as s
from ontology_map.db.panel_fixture import load_panel_fixture
from ontology_map.exploration import TimeWindow


def test_question_windows_may_use_independent_successful_model_tasks() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        context = queries.context(session, ids["gaon"])
        assert context is not None
        context_id = int(context["node_context_id"])
        rows = (
            session.execute(
                sa.select(s.node_question_set)
                .where(s.node_question_set.c.node_context_id == context_id)
                .order_by(s.node_question_set.c.time_window)
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2
        original_task_id = int(rows[0]["model_task_id"])
        original = (
            session.execute(
                sa.select(s.model_task).where(
                    s.model_task.c.model_task_id == original_task_id
                )
            )
            .mappings()
            .one()
        )

        second_task_id = session.scalar(
            s.model_task.insert()
            .values(
                task_kind="FOLLOWUP_QUESTIONS",
                input_hash=sha256(b"followup-independent-window-input").digest(),
                output_schema_definition_id=original["output_schema_definition_id"],
                model_version=original["model_version"],
                prompt_version=original["prompt_version"],
                cache_key=sha256(b"followup-independent-window-cache").digest(),
                status="SUCCESS",
                attempt_count=original["attempt_count"],
                finished_at=original["finished_at"],
            )
            .returning(s.model_task.c.model_task_id)
        )
        assert second_task_id is not None
        session.execute(
            s.node_question_set.update()
            .where(
                s.node_question_set.c.question_set_id == int(rows[1]["question_set_id"])
            )
            .values(model_task_id=second_task_id)
        )

        for window in (
            TimeWindow.RECENT_90_DAYS,
            TimeWindow.RECENT_1_YEAR,
        ):
            selected = queries.question_set(session, context_id, window.value)
            assert selected is not None
            assert selected["time_window"] == window.value
