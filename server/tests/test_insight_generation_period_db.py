from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from test_insight_generation_db import (
    _available_claims,
    _bundle,
    _preparing_publication,
    _report,
    _running_task,
)
from test_relations import rollback_session

from ontology_map.db import insight_generation as db
from ontology_map.db import schema as s
from ontology_map.db.panel_fixture import load_panel_fixture


def _make_claim_background_for_90_days(
    session,
    claim_id: int,
    now: datetime,
) -> None:
    document_ids = (
        sa.select(s.observation.c.source_document_id)
        .join(
            s.claim_observation,
            s.claim_observation.c.observation_id == s.observation.c.observation_id,
        )
        .where(s.claim_observation.c.claim_id == claim_id)
    )
    session.execute(
        s.source_document.update()
        .where(s.source_document.c.source_document_id.in_(document_ids))
        .values(published_at=now - timedelta(days=200), published_precision="DAY")
    )


def test_period_role_invalid_window_blocks_entire_bundle_without_partial_storage() -> (
    None
):
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, _ = _preparing_publication(session, ids["gaon"], now)
        initial = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        background_claim = _available_claims(initial)[0]
        _make_claim_background_for_90_days(session, background_claim, now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        ninety_window = prepared.agent_input.recent_90_days
        period_by_claim = {
            item.claim_id: item.period_role for item in ninety_window.claims
        }
        assert period_by_claim[background_claim] == "BACKGROUND"
        ninety_support = next(
            claim_id
            for claim_id in _available_claims(prepared)
            if claim_id != background_claim
        )
        year_ids = _available_claims(prepared, year=True)
        assert len(year_ids) >= 2
        invalid_ninety = _report(
            [background_claim, ninety_support],
            question="최근 90일 흐름은 무엇인가요?",
        )
        valid_year = _report(
            year_ids,
            question="지난 1년 흐름은 무엇인가요?",
        )
        task_id = _running_task(session, b"period-role-atomic", now)
        prior_pointer = prepared.prior_insight_model_task_id

        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(invalid_ninety, valid_year),
            finished_at=now + timedelta(seconds=1),
        )

        assert result.status == "VALIDATION_BLOCKED"
        assert result.report_ids == (None, None)
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == task_id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight)
                .where(s.node_insight.c.model_task_id == task_id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(
                    s.publication_affected_node.c.node_insight_model_task_id
                ).where(
                    s.publication_affected_node.c.promotion_batch_id == batch_id,
                    s.publication_affected_node.c.node_id == ids["gaon"],
                )
            )
            == prior_pointer
        )


def test_apply_bundle_persists_report_report() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, _ = _preparing_publication(session, ids["gaon"], now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        ninety_ids = _available_claims(prepared)
        year_ids = _available_claims(prepared, year=True)
        task_id = _running_task(session, b"report-report", now)
        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(
                _report(
                    ninety_ids,
                    question="최근 90일 흐름은 무엇인가요?",
                ),
                _report(
                    year_ids,
                    question="지난 1년 흐름은 무엇인가요?",
                ),
            ),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "SUCCESS"
        assert all(report_id is not None for report_id in result.report_ids)
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == task_id)
            )
            == 2
        )


def test_apply_bundle_persists_empty_report() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, _ = _preparing_publication(session, ids["gaon"], now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        year_ids = _available_claims(prepared, year=True)
        task_id = _running_task(session, b"empty-report", now)
        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(
                None,
                _report(
                    year_ids,
                    question="지난 1년 흐름은 무엇인가요?",
                ),
            ),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "SUCCESS"
        assert result.report_ids[0] is None
        assert result.report_ids[1] is not None


def test_apply_bundle_respects_caller_owned_transaction_rollback() -> None:
    _, ids = load_panel_fixture()
    with rollback_session() as session:
        now = datetime.now(UTC)
        batch_id, _ = _preparing_publication(session, ids["gaon"], now)
        prepared = db.prepare_insight_bundle(
            session,
            promotion_batch_id=batch_id,
            node_id=ids["gaon"],
            as_of_at=now,
        )
        task_id = _running_task(session, b"transaction-rollback", now)
        prior_pointer = prepared.prior_insight_model_task_id

        nested = session.begin_nested()
        result = db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,
            proposal=_bundle(None, None),
            finished_at=now + timedelta(seconds=1),
        )
        assert result.status == "SUCCESS"
        nested.rollback()

        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_insight_window)
                .where(s.node_insight_window.c.model_task_id == task_id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(s.model_task.c.status).where(
                    s.model_task.c.model_task_id == task_id
                )
            )
            == "RUNNING"
        )
        assert (
            session.scalar(
                sa.select(
                    s.publication_affected_node.c.node_insight_model_task_id
                ).where(
                    s.publication_affected_node.c.promotion_batch_id == batch_id,
                    s.publication_affected_node.c.node_id == ids["gaon"],
                )
            )
            == prior_pointer
        )
