"""Durable PostgreSQL integration regressions for issue #215 Phase D."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import followup_generation as followup_product
from ontology_map import insight_generation as insight_product
from ontology_map import node_context_generation as context_product
from ontology_map.db import followup_tasks, insight_tasks
from ontology_map.db import initial_publication as publication
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.session import get_engine
from ontology_map.durable_provider import ConfirmedProviderFailure
from ontology_map.followup_generation_contracts import (
    FollowupQuestionsProposal,
    FollowupWindowProposal,
)
from ontology_map.followup_runner import run_followup
from ontology_map.initial_publication_coordinator import run_initial_publication
from ontology_map.insight_generation_contracts import (
    InsightBundleProposal,
    InsightWindowProposal,
)
from ontology_map.insight_runner import run_insight
from ontology_map.node_context_generation_contracts import NodeContextProposal
from ontology_map.node_context_runner import run_node_context

DATABASE_URL = os.environ.get("ONTOLOGY_MAP_INITIAL_PUBLICATION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="isolated migrated PostgreSQL URL was not supplied",
)


def _engine() -> sa.Engine:
    engine = get_engine()
    url = engine.url
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_publication215_test")
    return engine


def _truncate() -> None:
    names = ", ".join(f'"{table.name}"' for table in s.metadata.sorted_tables)
    with _engine().begin() as connection:
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def _isolate_database() -> None:
    if not DATABASE_URL:
        yield
        return
    _truncate()
    yield
    _truncate()


def _count(session: Session, table: sa.Table, *conditions: object) -> int:
    statement = sa.select(sa.func.count()).select_from(table)
    if conditions:
        statement = statement.where(*conditions)
    return int(session.scalar(statement) or 0)


def _batch(session: Session) -> int:
    policy_id = session.scalar(
        sa.select(s.lint_policy_version.c.lint_policy_version_id)
        .where(s.lint_policy_version.c.is_active)
        .limit(1)
    )
    assert policy_id is not None
    batch_id = session.scalar(
        s.promotion_batch.insert()
        .values(lint_policy_version_id=policy_id)
        .returning(s.promotion_batch.c.promotion_batch_id)
    )
    assert batch_id is not None
    return int(batch_id)


def _activate_contract(
    session: Session,
    task_kind: str,
    schema_json: dict[str, object],
) -> None:
    session.execute(
        s.output_schema_definition.update()
        .where(
            s.output_schema_definition.c.task_kind == task_kind,
            s.output_schema_definition.c.is_active,
        )
        .values(is_active=False)
    )
    version = int(
        session.scalar(
            sa.select(
                sa.func.coalesce(
                    sa.func.max(s.output_schema_definition.c.version_no),
                    0,
                )
            ).where(s.output_schema_definition.c.task_kind == task_kind)
        )
        or 0
    )
    contract_id = session.scalar(
        s.output_schema_definition.insert()
        .values(
            task_kind=task_kind,
            version_no=version + 1,
            schema_json=schema_json,
            is_active=True,
        )
        .returning(s.output_schema_definition.c.output_schema_definition_id)
    )
    assert contract_id is not None


def _activate_generation_contracts(session: Session) -> None:
    _activate_contract(session, "NODE_CONTEXT", context_product.output_schema())
    _activate_contract(
        session,
        "FOLLOWUP_QUESTIONS",
        followup_product.output_schema(),
    )
    _activate_contract(session, "NODE_INSIGHT", insight_product.output_schema())


def _new_observation(session: Session) -> int:
    marker = uuid4().hex
    body = f"#215 Phase D evidence {marker}"
    group_id = session.scalar(
        s.evidence_group.insert().returning(s.evidence_group.c.evidence_group_id)
    )
    assert group_id is not None
    document_id = session.scalar(
        s.source_document.insert()
        .values(
            evidence_group_id=group_id,
            source_key=f"publication215-phase-d:{marker}",
            version_no=1,
            canonical_url=f"https://example.com/publication215/phase-d/{marker}",
            publisher_name="issue 215 Phase D regression",
            title="issue 215 Phase D evidence",
            original_language="ko",
            normalized_body=body,
            body_hash=sha256(body.encode()).digest(),
            published_at=datetime.now(UTC) - timedelta(days=1),
            published_precision="DAY",
            modified_precision="UNKNOWN",
            last_checked_at=datetime.now(UTC),
            last_check_status="SUCCESS",
        )
        .returning(s.source_document.c.source_document_id)
    )
    assert document_id is not None
    observation_id = session.scalar(
        s.observation.insert()
        .values(
            source_document_id=document_id,
            start_char=0,
            end_char=len(body),
            quote_text=body,
            quote_hash=sha256(body.encode()).digest(),
            paragraph_number=1,
            observed_at=datetime.now(UTC),
        )
        .returning(s.observation.c.observation_id)
    )
    assert observation_id is not None
    return int(observation_id)


def _relation_between(session: Session, left: int, right: int) -> int:
    relation_id = session.scalar(
        sa.select(s.relation.c.relation_id).where(
            sa.or_(
                sa.and_(
                    s.relation.c.source_node_id == left,
                    s.relation.c.target_node_id == right,
                ),
                sa.and_(
                    s.relation.c.source_node_id == right,
                    s.relation.c.target_node_id == left,
                ),
            )
        )
    )
    assert relation_id is not None
    return int(relation_id)


def _claim_for_relation(session: Session, relation_id: int) -> int:
    claim_id = session.scalar(
        sa.select(s.claim_relation.c.claim_id)
        .where(s.claim_relation.c.relation_id == relation_id)
        .order_by(s.claim_relation.c.claim_id)
        .limit(1)
    )
    assert claim_id is not None
    return int(claim_id)


def _alias_batch(session: Session, node_id: int) -> int:
    batch_id = _batch(session)
    alias_id = session.scalar(
        s.node_alias.insert()
        .values(
            node_id=node_id,
            alias_text=f"HBF-phase-d-{uuid4().hex}",
            language="en",
            is_preferred=False,
        )
        .returning(s.node_alias.c.node_alias_id)
    )
    assert alias_id is not None
    provenance.record_node_alias_changed(session, batch_id, int(alias_id))
    provenance.mark_promotion_committed(session, batch_id)
    return batch_id


def _alias_evidence_batch(session: Session, node_id: int) -> int:
    alias_id = session.scalar(
        sa.select(s.node_alias.c.node_alias_id)
        .where(s.node_alias.c.node_id == node_id, s.node_alias.c.is_preferred)
        .limit(1)
    )
    assert alias_id is not None
    batch_id = _batch(session)
    assert provenance.add_node_alias_evidence(
        session,
        batch_id,
        int(alias_id),
        _new_observation(session),
    )
    provenance.mark_promotion_committed(session, batch_id)
    return batch_id


def _two_node_batch(session: Session, left: int, right: int) -> int:
    relation_id = _relation_between(session, left, right)
    claim_id = _claim_for_relation(session, relation_id)
    batch_id = _batch(session)
    assert provenance.add_claim_observation(
        session,
        batch_id,
        claim_id,
        _new_observation(session),
    )
    provenance.mark_promotion_committed(session, batch_id)
    return batch_id


def _assert_reserved(engine: sa.Engine, task_kind: str) -> None:
    with Session(engine) as session:
        count = session.scalar(
            sa.select(sa.func.count())
            .select_from(
                s.provider_call_slot.join(
                    s.model_task,
                    s.model_task.c.model_task_id
                    == s.provider_call_slot.c.model_task_id,
                )
            )
            .where(
                s.model_task.c.task_kind == task_kind,
                s.provider_call_slot.c.state == "RESERVED",
            )
        )
    assert int(count or 0) == 1


def _providers(engine: sa.Engine, sends: dict[str, list[object]]):
    def context(prepared):
        def send():
            _assert_reserved(engine, "NODE_CONTEXT")
            sends["context"].append(prepared.agent_input.node_id)
            return NodeContextProposal(
                context_text="현재 공개 지식과 직접 관계를 짧게 설명합니다."
            )

        return send

    def followup(prepared):
        def send():
            _assert_reserved(engine, "FOLLOWUP_QUESTIONS")
            sends["followup"].append(prepared.agent_input.node_id)
            return FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            )

        return send

    def insight(prepared):
        def send():
            _assert_reserved(engine, "NODE_INSIGHT")
            sends["insight"].append(prepared.agent_input.node_id)
            return InsightBundleProposal(
                recent_90_days=InsightWindowProposal(report=None),
                recent_1_year=InsightWindowProposal(report=None),
            )

        return send

    return context, followup, insight


def _publication_row(session: Session, batch_id: int, node_id: int):
    return (
        session.execute(
            sa.select(s.publication_affected_node).where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == node_id,
            )
        )
        .mappings()
        .one()
    )


def _selected_task_ids(
    session: Session,
    batch_id: int,
    node_id: int,
) -> tuple[int, ...]:
    row = _publication_row(session, batch_id, node_id)
    context_id = int(row["node_context_id"])
    context_task_id = int(
        session.scalar(
            sa.select(s.node_context.c.model_task_id).where(
                s.node_context.c.node_context_id == context_id
            )
        )
    )
    followup_ids = tuple(
        sorted(
            set(
                int(value)
                for value in session.scalars(
                    sa.select(s.node_question_set.c.model_task_id)
                    .where(s.node_question_set.c.node_context_id == context_id)
                    .order_by(s.node_question_set.c.time_window)
                )
            )
        )
    )
    return (
        context_task_id,
        *followup_ids,
        int(row["node_insight_model_task_id"]),
    )


def _run_context_only(
    engine: sa.Engine,
    batch_id: int,
    node_id: int,
    context_provider,
) -> tuple[int, int, datetime]:
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_id)
        document_id = publication.ensure_search_document(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
        prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
        task = publication.ensure_node_context_task(session, prepared)
    result = run_node_context(
        engine,
        task.model_task_id,
        "phase-d-context",
        promotion_batch_id=batch_id,
        node_id=node_id,
        prepare_provider=context_provider,
    )
    assert result.task_status == "SUCCESS"
    with Session(engine) as session:
        row = _publication_row(session, batch_id, node_id)
        committed_at = session.scalar(
            sa.select(s.promotion_batch.c.committed_at).where(
                s.promotion_batch.c.promotion_batch_id == batch_id
            )
        )
    assert committed_at is not None
    return document_id, int(row["node_context_id"]), committed_at


def test_durable_coordinator_ready_and_reentry_do_not_resend() -> None:
    _created, nodes = load_hbf_fixture()
    engine = _engine()
    node_id = nodes["hbf"]
    sends: dict[str, list[object]] = {
        "context": [],
        "followup": [],
        "insight": [],
    }
    context_provider, followup_provider, insight_provider = _providers(
        engine,
        sends,
    )
    with Session(engine) as session:
        previous_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _alias_batch(session, node_id)
        canonical_count = _count(session, s.knowledge_item)

    first = run_initial_publication(
        engine,
        batch_id,
        "phase-d",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    )
    assert first.ready
    assert sends["context"] == [node_id]
    assert sends["followup"] == [node_id]
    assert sends["insight"] == [node_id]

    with Session(engine) as session:
        task_ids = _selected_task_ids(session, batch_id, node_id)
        assert len(task_ids) == 3
        assert set(
            session.scalars(
                sa.select(s.model_task.c.status).where(
                    s.model_task.c.model_task_id.in_(task_ids)
                )
            )
        ) == {"SUCCESS"}
        assert (
            _count(
                session,
                s.provider_call_slot,
                s.provider_call_slot.c.model_task_id.in_(task_ids),
            )
            == 3
        )
        assert (
            _count(
                session,
                s.agent_attempt,
                s.agent_attempt.c.model_task_id.in_(task_ids),
            )
            == 3
        )
        artifacts_before = (
            _count(session, s.node_context),
            _count(session, s.node_question_set),
            _count(session, s.node_insight_window),
        )

    second = run_initial_publication(
        engine,
        batch_id,
        "phase-d-reentry",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    )
    assert second.ready
    assert sends["context"] == [node_id]
    assert len(sends["followup"]) == 1
    assert sends["insight"] == [node_id]

    with Session(engine) as session:
        assert _selected_task_ids(session, batch_id, node_id) == task_ids
        assert artifacts_before == (
            _count(session, s.node_context),
            _count(session, s.node_question_set),
            _count(session, s.node_insight_window),
        )
        assert _count(session, s.knowledge_item) == canonical_count
        current_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
    assert previous_ready <= current_ready


def test_transient_context_retry_reuses_task_then_reaches_ready() -> None:
    _created, nodes = load_hbf_fixture()
    engine = _engine()
    node_id = nodes["hbf"]
    sends: dict[str, list[object]] = {
        "context": [],
        "followup": [],
        "insight": [],
    }
    _normal_context, followup_provider, insight_provider = _providers(
        engine,
        sends,
    )
    attempts = 0

    def context_provider(prepared):
        def send():
            nonlocal attempts
            _assert_reserved(engine, "NODE_CONTEXT")
            attempts += 1
            sends["context"].append(prepared.agent_input.node_id)
            if attempts == 1:
                raise ConfirmedProviderFailure(
                    "RATE_LIMITED",
                    transient=True,
                    retry_after=timedelta(0),
                )
            return NodeContextProposal(context_text="재시도 후 생성된 짧은 맥락입니다.")

        return send

    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _alias_batch(session, node_id)

    first = run_initial_publication(
        engine,
        batch_id,
        "phase-d-retry",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    )
    assert not first.ready
    with Session(engine) as session:
        context_task_id = int(
            session.scalar(
                sa.select(s.model_task.c.model_task_id)
                .where(s.model_task.c.task_kind == "NODE_CONTEXT")
                .order_by(s.model_task.c.model_task_id.desc())
                .limit(1)
            )
        )
        status = session.scalar(
            sa.select(s.model_task.c.status).where(
                s.model_task.c.model_task_id == context_task_id
            )
        )
    assert status == "RETRY_WAIT"

    second = run_initial_publication(
        engine,
        batch_id,
        "phase-d-retry",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    )
    assert second.ready
    assert attempts == 2
    with Session(engine) as session:
        assert _selected_task_ids(session, batch_id, node_id)[0] == context_task_id
        assert (
            _count(
                session,
                s.provider_call_slot,
                s.provider_call_slot.c.model_task_id == context_task_id,
            )
            == 2
        )
        assert (
            _count(
                session,
                s.agent_attempt,
                s.agent_attempt.c.model_task_id == context_task_id,
            )
            == 2
        )


def test_one_followup_window_missing_blocks_ready() -> None:
    _created, nodes = load_hbf_fixture()
    engine = _engine()
    node_id = nodes["hbf"]
    sends: dict[str, list[object]] = {
        "context": [],
        "followup": [],
        "insight": [],
    }
    context_provider, followup_provider, insight_provider = _providers(
        engine,
        sends,
    )
    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _alias_batch(session, node_id)
    _document_id, context_id, as_of_at = _run_context_only(
        engine,
        batch_id,
        node_id,
        context_provider,
    )
    with Session(engine) as session, session.begin():
        followup_90 = followup_tasks.enqueue_followup(
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
    followup_result = run_followup(
        engine,
        followup_90.task_id,
        "phase-d-missing",
        node_context_id=context_id,
        as_of_at=as_of_at,
        prepare_provider=followup_provider,
    )
    insight_result = run_insight(
        engine,
        insight.task_id,
        "phase-d-missing",
        promotion_batch_id=batch_id,
        node_id=node_id,
        as_of_at=as_of_at,
        prepare_provider=insight_provider,
    )
    assert followup_result.task_status == "SUCCESS"
    assert insight_result.task_status == "SUCCESS"
    with Session(engine) as session, session.begin():
        deleted = session.execute(
            s.node_question_set.delete().where(
                s.node_question_set.c.model_task_id == followup_90.task_id,
                s.node_question_set.c.time_window == "RECENT_1_YEAR",
            )
        )
        assert deleted.rowcount == 1
    with Session(engine) as session:
        readiness = publication.publication_readiness(session, batch_id)
    assert not readiness.ready
    assert any("FOLLOWUP window bundle" in reason for reason in readiness.reasons)


def test_one_window_corrupt_insight_bundle_blocks_ready() -> None:
    _created, nodes = load_hbf_fixture()
    engine = _engine()
    node_id = nodes["hbf"]
    sends: dict[str, list[object]] = {
        "context": [],
        "followup": [],
        "insight": [],
    }
    context_provider, followup_provider, insight_provider = _providers(
        engine,
        sends,
    )
    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _alias_batch(session, node_id)
    _document_id, context_id, as_of_at = _run_context_only(
        engine,
        batch_id,
        node_id,
        context_provider,
    )
    with Session(engine) as session, session.begin():
        queued = followup_tasks.enqueue_followup(
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
    result = run_followup(
        engine,
        queued.task_id,
        "phase-d-corrupt",
        node_context_id=context_id,
        as_of_at=as_of_at,
        prepare_provider=followup_provider,
    )
    assert result.task_status == "SUCCESS"
    insight_result = run_insight(
        engine,
        insight.task_id,
        "phase-d-corrupt",
        promotion_batch_id=batch_id,
        node_id=node_id,
        as_of_at=as_of_at,
        prepare_provider=insight_provider,
    )
    assert insight_result.task_status == "SUCCESS"
    with Session(engine) as session, session.begin():
        deleted = session.execute(
            s.node_insight_window.delete().where(
                s.node_insight_window.c.model_task_id == insight.task_id,
                s.node_insight_window.c.time_window == "RECENT_1_YEAR",
            )
        )
        assert deleted.rowcount == 1
    with Session(engine) as session:
        readiness = publication.publication_readiness(session, batch_id)
    assert not readiness.ready
    assert any(
        "atomic window bundle is incomplete" in reason for reason in readiness.reasons
    )


def test_stale_context_after_provider_attempt_is_validation_blocked() -> None:
    _created, nodes = load_hbf_fixture()
    engine = _engine()
    node_id = nodes["hbf"]
    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _alias_batch(session, node_id)
        publication.start_initial_publication(session, batch_id)
        publication.ensure_search_document(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
        prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
        task = publication.ensure_node_context_task(session, prepared)

    def stale_provider(_prepared):
        def send():
            _assert_reserved(engine, "NODE_CONTEXT")
            with Session(engine) as session, session.begin():
                session.execute(
                    s.node_alias.insert().values(
                        node_id=node_id,
                        alias_text=f"historical-stale-{uuid4().hex}",
                        language="en",
                        is_preferred=False,
                    )
                )
            return NodeContextProposal(context_text="이 결과는 stale이 됩니다.")

        return send

    result = run_node_context(
        engine,
        task.model_task_id,
        "phase-d-stale",
        promotion_batch_id=batch_id,
        node_id=node_id,
        prepare_provider=stale_provider,
    )
    assert result.task_status == "VALIDATION_BLOCKED"
    assert result.disposition == "VALIDATION_BLOCKED"
    with Session(engine) as session:
        pointer = session.scalar(
            sa.select(s.publication_affected_node.c.node_context_id).where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == node_id,
            )
        )
        attempts = _count(
            session,
            s.agent_attempt,
            s.agent_attempt.c.model_task_id == task.model_task_id,
        )
    assert pointer is None
    assert attempts == 1


def test_old_followup_and_insight_cannot_fill_new_generation() -> None:
    _created, nodes = load_hbf_fixture()
    engine = _engine()
    node_id = nodes["hbf"]
    sends: dict[str, list[object]] = {
        "context": [],
        "followup": [],
        "insight": [],
    }
    context_provider, followup_provider, insight_provider = _providers(
        engine,
        sends,
    )
    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        first_batch = _alias_batch(session, node_id)
    assert run_initial_publication(
        engine,
        first_batch,
        "phase-d-old",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    ).ready
    with Session(engine) as session:
        first_row = _publication_row(session, first_batch, node_id)
        first_document = int(first_row["node_search_document_id"])
        first_context = int(first_row["node_context_id"])
        first_insight = int(first_row["node_insight_model_task_id"])
        assert (
            _count(
                session,
                s.node_question_set,
                s.node_question_set.c.node_context_id == first_context,
            )
            == 2
        )

    with Session(engine) as session, session.begin():
        second_batch = _alias_evidence_batch(session, node_id)
    second_document, second_context, _as_of = _run_context_only(
        engine,
        second_batch,
        node_id,
        context_provider,
    )
    assert second_document == first_document
    assert second_context != first_context
    with Session(engine) as session, session.begin():
        session.execute(
            s.publication_affected_node.update()
            .where(
                s.publication_affected_node.c.promotion_batch_id == second_batch,
                s.publication_affected_node.c.node_id == node_id,
            )
            .values(node_insight_model_task_id=first_insight)
        )
    with Session(engine) as session:
        readiness = publication.publication_readiness(session, second_batch)
        new_question_sets = _count(
            session,
            s.node_question_set,
            s.node_question_set.c.node_context_id == second_context,
        )
    assert not readiness.ready
    assert new_question_sets == 0
    assert any("FOLLOWUP window bundle" in r for r in readiness.reasons)
    assert any("atomic window bundle is incomplete" in r for r in readiness.reasons)


def test_multi_node_terminal_failure_blocks_ready_and_preserves_data() -> None:
    _created, nodes = load_hbf_fixture()
    engine = _engine()
    left = nodes["sk_hynix"]
    right = nodes["hbf"]
    sends: dict[str, list[object]] = {
        "context": [],
        "followup": [],
        "insight": [],
    }
    _normal_context, followup_provider, insight_provider = _providers(
        engine,
        sends,
    )

    def context_provider(prepared):
        def send():
            _assert_reserved(engine, "NODE_CONTEXT")
            sends["context"].append(prepared.agent_input.node_id)
            if prepared.agent_input.node_id == right:
                raise ConfirmedProviderFailure("INVALID_REQUEST")
            return NodeContextProposal(context_text="완성된 노드의 짧은 맥락입니다.")

        return send

    with Session(engine) as session:
        previous_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _two_node_batch(session, left, right)
        canonical_after_commit = _count(session, s.knowledge_item)

    result = run_initial_publication(
        engine,
        batch_id,
        "phase-d-multi",
        prepare_node_context_provider=context_provider,
        prepare_followup_provider=followup_provider,
        prepare_insight_provider=insight_provider,
    )
    assert not result.ready
    assert set(result.affected_node_ids) == {left, right}
    with Session(engine) as session:
        right_task = session.scalar(
            sa.select(s.model_task.c.model_task_id)
            .where(
                s.model_task.c.task_kind == "NODE_CONTEXT",
                s.model_task.c.status == "FINAL_FAILED",
            )
            .limit(1)
        )
        assert right_task is not None
        assert _count(session, s.knowledge_item) == canonical_after_commit
        current_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
    assert previous_ready <= current_ready
