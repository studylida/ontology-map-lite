"""Focused PostgreSQL integration regressions for issue #215 Phase C.

These tests consume the merged #129/#68 prepare/apply product boundaries. They
establish only the RUNNING task precondition needed to exercise finalizers;
they do not fabricate agent_attempt SUCCESS rows, provider calls or retry
accounting. Actual durable provider execution remains owned by #127.
"""

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
from ontology_map.db import followup_generation as followup_db
from ontology_map.db import initial_publication as publication
from ontology_map.db import initial_publication_start as publication_start_db
from ontology_map.db import insight_generation as insight_db
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.session import get_engine
from ontology_map.followup_generation_contracts import (
    FollowupQuestionsProposal,
    FollowupWindowProposal,
)
from ontology_map.insight_generation_contracts import (
    InsightBundleProposal,
    InsightWindowProposal,
)
from ontology_map.node_context_generation_contracts import NodeContextProposal

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


def _new_observation(session: Session) -> int:
    marker = uuid4().hex
    body = f"#215 Phase C evidence {marker}"
    group_id = session.scalar(
        s.evidence_group.insert().returning(s.evidence_group.c.evidence_group_id)
    )
    assert group_id is not None
    document_id = session.scalar(
        s.source_document.insert()
        .values(
            evidence_group_id=group_id,
            source_key=f"publication215-phase-c:{marker}",
            version_no=1,
            canonical_url=f"https://example.com/publication215/phase-c/{marker}",
            publisher_name="issue 215 Phase C regression",
            title="issue 215 Phase C evidence",
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


def _single_node_alias_batch(session: Session, node_id: int) -> int:
    batch_id = _batch(session)
    alias_id = session.scalar(
        s.node_alias.insert()
        .values(
            node_id=node_id,
            alias_text=f"HBF-phase-c-{uuid4().hex}",
            language="en",
            is_preferred=False,
        )
        .returning(s.node_alias.c.node_alias_id)
    )
    assert alias_id is not None
    provenance.record_node_alias_changed(session, batch_id, int(alias_id))
    provenance.mark_promotion_committed(session, batch_id)
    return batch_id


def _single_node_alias_evidence_batch(session: Session, node_id: int) -> int:
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


def _two_node_evidence_batch(
    session: Session,
    left_node_id: int,
    right_node_id: int,
) -> int:
    relation_id = _relation_between(session, left_node_id, right_node_id)
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


def _activate_contract(
    session: Session,
    task_kind: str,
    schema_json: dict[str, object],
) -> int:
    session.execute(
        s.output_schema_definition.update()
        .where(
            s.output_schema_definition.c.task_kind == task_kind,
            s.output_schema_definition.c.is_active,
        )
        .values(is_active=False)
    )
    current_version = int(
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
            version_no=current_version + 1,
            schema_json=schema_json,
            is_active=True,
        )
        .returning(s.output_schema_definition.c.output_schema_definition_id)
    )
    assert contract_id is not None
    return int(contract_id)


def _activate_generation_contracts(session: Session) -> dict[str, int]:
    return {
        "NODE_CONTEXT": _activate_contract(
            session,
            "NODE_CONTEXT",
            context_product.output_schema(),
        ),
        "FOLLOWUP_QUESTIONS": _activate_contract(
            session,
            "FOLLOWUP_QUESTIONS",
            followup_product.output_schema(),
        ),
        "NODE_INSIGHT": _activate_contract(
            session,
            "NODE_INSIGHT",
            insight_product.output_schema(),
        ),
    }


def _make_running_task(
    session: Session,
    *,
    task_kind: str,
    contract_id: int,
    model_version: str,
    prompt_version: str,
) -> int:
    nonce = uuid4().hex.encode()
    task_id = session.scalar(
        s.model_task.insert()
        .values(
            task_kind=task_kind,
            input_hash=sha256(b"phase-c-input:" + nonce).digest(),
            output_schema_definition_id=contract_id,
            model_version=model_version,
            prompt_version=prompt_version,
            cache_key=sha256(b"phase-c-cache:" + nonce).digest(),
            status="RUNNING",
            lease_owner="phase-c-finalizer-boundary",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        .returning(s.model_task.c.model_task_id)
    )
    assert task_id is not None
    return int(task_id)


def _finish_context(
    engine: sa.Engine,
    *,
    batch_id: int,
    node_id: int,
) -> tuple[int, int, int]:
    with Session(engine) as session, session.begin():
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

    with Session(engine) as session, session.begin():
        session.execute(
            s.model_task.update()
            .where(
                s.model_task.c.model_task_id == task.model_task_id,
                s.model_task.c.status == "PENDING",
            )
            .values(
                status="RUNNING",
                lease_owner="phase-c-context-finalizer",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        result = publication.apply_node_context(
            session,
            model_task_id=task.model_task_id,
            prepared=prepared,
            proposal=NodeContextProposal(
                context_text="HBF의 공개 지식과 직접 관계를 짧게 설명합니다."
            ),
            finished_at=datetime.now(UTC),
        )
        assert result.status == "SUCCESS"
        assert result.node_context_id is not None
        context_id = int(result.node_context_id)
    return document_id, context_id, task.model_task_id


def _apply_empty_followup(
    engine: sa.Engine,
    *,
    prepared: object,
    contract_id: int,
) -> int:
    with Session(engine) as session, session.begin():
        task_id = _make_running_task(
            session,
            task_kind="FOLLOWUP_QUESTIONS",
            contract_id=contract_id,
            model_version=followup_product.MODEL_VERSION,
            prompt_version=followup_product.PROMPT_VERSION,
        )
        result = followup_db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=prepared,  # type: ignore[arg-type]
            proposal=FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
            finished_at=datetime.now(UTC),
        )
        assert result.status == "SUCCESS"
        assert result.stored_count == 0
    return task_id


def _apply_empty_insight(
    engine: sa.Engine,
    *,
    prepared: object,
    contract_id: int,
) -> int:
    with Session(engine) as session, session.begin():
        task_id = _make_running_task(
            session,
            task_kind="NODE_INSIGHT",
            contract_id=contract_id,
            model_version=insight_product.MODEL_VERSION,
            prompt_version=insight_product.PROMPT_VERSION,
        )
        result = insight_db.apply_insight_bundle(
            session,
            model_task_id=task_id,
            prepared=prepared,  # type: ignore[arg-type]
            proposal=InsightBundleProposal(
                recent_90_days=InsightWindowProposal(report=None),
                recent_1_year=InsightWindowProposal(report=None),
            ),
            finished_at=datetime.now(UTC),
        )
        assert result.status == "SUCCESS"
        assert result.report_ids == (None, None)
    return task_id


def _complete_node_empty(
    engine: sa.Engine,
    *,
    batch_id: int,
    node_id: int,
    contracts: dict[str, int],
) -> tuple[int, int, int, int, int]:
    document_id, context_id, context_task_id = _finish_context(
        engine,
        batch_id=batch_id,
        node_id=node_id,
    )
    with Session(engine) as session:
        work = publication.prepare_derived_work(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
    followup = _apply_empty_followup(
        engine,
        prepared=work.followup_bundle,
        contract_id=contracts["FOLLOWUP_QUESTIONS"],
    )
    insight_task = _apply_empty_insight(
        engine,
        prepared=work.insight_bundle,
        contract_id=contracts["NODE_INSIGHT"],
    )
    return (
        document_id,
        context_id,
        context_task_id,
        followup,
        insight_task,
    )


def test_normal_empty_products_complete_one_generation_and_ready() -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()
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
        contracts = _activate_generation_contracts(session)
        batch_id = _single_node_alias_batch(session, node_id)
    with Session(engine) as session, session.begin():
        started = publication.start_initial_publication(session, batch_id)
        assert started.affected_node_ids == (node_id,)

    ids = _complete_node_empty(
        engine,
        batch_id=batch_id,
        node_id=node_id,
        contracts=contracts,
    )
    document_id, context_id, *_task_ids = ids

    with Session(engine) as session:
        readiness = publication.publication_readiness(session, batch_id)
        question_sets = session.execute(
            sa.select(
                s.node_question_set.c.time_window,
                s.node_question_set.c.model_task_id,
            )
            .where(s.node_question_set.c.node_context_id == context_id)
            .order_by(s.node_question_set.c.time_window)
        ).all()
        question_count = int(
            session.scalar(sa.select(sa.func.count()).select_from(s.node_question)) or 0
        )
        insight_windows = session.execute(
            sa.select(
                s.node_insight_window.c.time_window,
                s.node_insight_window.c.node_insight_id,
            )
            .where(
                s.node_insight_window.c.node_id == node_id,
                s.node_insight_window.c.node_search_document_id == document_id,
            )
            .order_by(s.node_insight_window.c.time_window)
        ).all()
        attempt_count = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.agent_attempt)
                .where(s.agent_attempt.c.model_task_id.in_(list(_task_ids)))
            )
            or 0
        )
    assert readiness.ready, readiness.reasons
    assert {str(row.time_window) for row in question_sets} == {
        "RECENT_90_DAYS",
        "RECENT_1_YEAR",
    }
    assert len({int(row.model_task_id) for row in question_sets}) == 1
    assert question_count == 0
    assert {str(row.time_window) for row in insight_windows} == {
        "RECENT_90_DAYS",
        "RECENT_1_YEAR",
    }
    assert all(row.node_insight_id is None for row in insight_windows)
    assert attempt_count == 0

    with Session(engine) as session, session.begin():
        assert publication.mark_publication_ready(session, batch_id)
        assert not publication.mark_publication_ready(session, batch_id)

    with Session(engine) as session:
        status = session.scalar(
            sa.select(s.promotion_batch.c.publication_status).where(
                s.promotion_batch.c.promotion_batch_id == batch_id
            )
        )
        still_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
    assert status == "READY"
    assert previous_ready <= still_ready


def test_legacy_two_task_followup_shape_remains_ready() -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()
    with Session(engine) as session, session.begin():
        contracts = _activate_generation_contracts(session)
        batch_id = _single_node_alias_batch(session, node_id)
        publication.start_initial_publication(session, batch_id)

    _document_id, context_id, _context_task_id, followup_task_id, _insight_task_id = (
        _complete_node_empty(
            engine,
            batch_id=batch_id,
            node_id=node_id,
            contracts=contracts,
        )
    )
    with Session(engine) as session, session.begin():
        legacy_task_id = _make_running_task(
            session,
            task_kind="FOLLOWUP_QUESTIONS",
            contract_id=contracts["FOLLOWUP_QUESTIONS"],
            model_version=followup_product.MODEL_VERSION,
            prompt_version="followup-questions-129-v2",
        )
        session.execute(
            s.model_task.update()
            .where(s.model_task.c.model_task_id == followup_task_id)
            .values(prompt_version="followup-questions-129-v2")
        )
        session.execute(
            s.model_task.update()
            .where(s.model_task.c.model_task_id == legacy_task_id)
            .values(
                status="SUCCESS",
                lease_owner=None,
                lease_expires_at=None,
                finished_at=datetime.now(UTC),
            )
        )
        session.execute(
            s.node_question_set.update()
            .where(
                s.node_question_set.c.node_context_id == context_id,
                s.node_question_set.c.time_window == "RECENT_1_YEAR",
            )
            .values(model_task_id=legacy_task_id)
        )

    with Session(engine) as session:
        assert publication.publication_readiness(session, batch_id).ready


def test_one_followup_window_missing_blocks_ready_until_completed() -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()
    with Session(engine) as session, session.begin():
        contracts = _activate_generation_contracts(session)
        batch_id = _single_node_alias_batch(session, node_id)
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_id)
    _finish_context(engine, batch_id=batch_id, node_id=node_id)

    with Session(engine) as session:
        work = publication.prepare_derived_work(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
    _apply_empty_followup(
        engine,
        prepared=work.followup_bundle,
        contract_id=contracts["FOLLOWUP_QUESTIONS"],
    )
    with Session(engine) as session, session.begin():
        session.execute(
            s.node_question_set.delete().where(
                s.node_question_set.c.node_context_id
                == work.followup_bundle.node_context_id,
                s.node_question_set.c.time_window == "RECENT_1_YEAR",
            )
        )
    _apply_empty_insight(
        engine,
        prepared=work.insight_bundle,
        contract_id=contracts["NODE_INSIGHT"],
    )

    with Session(engine) as session:
        readiness = publication.publication_readiness(session, batch_id)
    assert not readiness.ready
    assert any("FOLLOWUP window bundle" in reason for reason in readiness.reasons)
    with pytest.raises(publication.PublicationNotReady):
        with Session(engine) as session, session.begin():
            publication.mark_publication_ready(session, batch_id)

    with Session(engine) as session, session.begin():
        session.execute(
            s.node_question_set.insert().from_select(
                ["node_context_id", "model_task_id", "time_window", "as_of_at"],
                sa.select(
                    s.node_question_set.c.node_context_id,
                    s.node_question_set.c.model_task_id,
                    sa.literal("RECENT_1_YEAR"),
                    s.node_question_set.c.as_of_at,
                ).where(
                    s.node_question_set.c.node_context_id
                    == work.followup_bundle.node_context_id,
                    s.node_question_set.c.time_window == "RECENT_90_DAYS",
                ),
            )
        )
    with Session(engine) as session:
        assert publication.publication_readiness(session, batch_id).ready


def test_multi_node_batch_requires_every_frozen_member_complete() -> None:
    _created, nodes = load_hbf_fixture()
    engine = _engine()
    left = nodes["sk_hynix"]
    right = nodes["hbf"]
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
        contracts = _activate_generation_contracts(session)
        batch_id = _two_node_evidence_batch(session, left, right)
    with Session(engine) as session, session.begin():
        started = publication.start_initial_publication(session, batch_id)
    assert set(started.affected_node_ids) == {left, right}

    _complete_node_empty(
        engine,
        batch_id=batch_id,
        node_id=left,
        contracts=contracts,
    )
    with Session(engine) as session:
        readiness = publication.publication_readiness(session, batch_id)
        still_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
    assert not readiness.ready
    assert any(f"node {right}:" in reason for reason in readiness.reasons)
    assert previous_ready <= still_ready
    with pytest.raises(publication.PublicationNotReady):
        with Session(engine) as session, session.begin():
            publication.mark_publication_ready(session, batch_id)


def test_old_generation_context_cannot_fill_new_generation() -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()
    with Session(engine) as session, session.begin():
        contracts = _activate_generation_contracts(session)
        first_batch = _single_node_alias_batch(session, node_id)
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, first_batch)
    first_ids = _complete_node_empty(
        engine,
        batch_id=first_batch,
        node_id=node_id,
        contracts=contracts,
    )
    first_document, first_context, *_ = first_ids
    with Session(engine) as session, session.begin():
        assert publication.mark_publication_ready(session, first_batch)

    with Session(engine) as session, session.begin():
        second_batch = _single_node_alias_evidence_batch(session, node_id)
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, second_batch)
        second_document = publication.ensure_search_document(
            session,
            promotion_batch_id=second_batch,
            node_id=node_id,
        )
    assert second_document == first_document

    with Session(engine) as session, session.begin():
        session.execute(
            s.publication_affected_node.update()
            .where(
                s.publication_affected_node.c.promotion_batch_id == second_batch,
                s.publication_affected_node.c.node_id == node_id,
            )
            .values(node_context_id=first_context)
        )

    with Session(engine) as session:
        readiness = publication.publication_readiness(session, second_batch)
    assert not readiness.ready
    assert any(
        "NODE_CONTEXT is not current SUCCESS" in reason for reason in readiness.reasons
    )


def test_failure_retry_and_readiness_never_reproject_after_preparing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()
    with Session(engine) as session, session.begin():
        batch_id = _single_node_alias_batch(session, node_id)
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_id)

    def forbidden_projection(_session: Session, _batch_id: int) -> tuple[int, ...]:
        raise AssertionError("PREPARING lifecycle must not re-project provenance")

    monkeypatch.setattr(publication_start_db, "affected_node_ids", forbidden_projection)

    with Session(engine) as session:
        readiness = publication.publication_readiness(session, batch_id)
        assert not readiness.ready
    with Session(engine) as session, session.begin():
        assert publication.mark_initial_publication_failed(
            session,
            batch_id,
            "phase-c frozen membership regression",
        )
    with Session(engine) as session, session.begin():
        assert publication.retry_failed_initial_publication(session, batch_id)
    with Session(engine) as session:
        assert publication_start_db.membership(session, batch_id) == (node_id,)
