"""Focused PostgreSQL regressions for issue #215 Phase B only.

The provider runner is owned by #127 and is intentionally absent here. Tests that
exercise the NODE_CONTEXT finalizer establish only its RUNNING-task precondition;
they do not fabricate agent_attempt SUCCESS rows or provider accounting.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import node_context_generation as context_product
from ontology_map.db import initial_publication as publication
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.initial_publication_contracts import NodeContextTaskError
from ontology_map.db.session import get_engine
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


@pytest.fixture(autouse=True)
def _isolate_database() -> None:
    if not DATABASE_URL:
        yield
        return
    names = ", ".join(f'"{table.name}"' for table in s.metadata.sorted_tables)
    with _engine().begin() as connection:
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    yield


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
    body = f"#215 Phase B evidence-only observation {marker}"
    group_id = session.scalar(
        s.evidence_group.insert().returning(s.evidence_group.c.evidence_group_id)
    )
    assert group_id is not None
    document_id = session.scalar(
        s.source_document.insert()
        .values(
            evidence_group_id=group_id,
            source_key=f"publication215-phase-b:{marker}",
            version_no=1,
            canonical_url=f"https://example.com/publication215/phase-b/{marker}",
            publisher_name="issue 215 Phase B regression",
            title="issue 215 Phase B evidence",
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


def _claim_for_node(session: Session, node_id: int) -> int:
    claim_id = session.scalar(
        sa.select(s.claim_relation.c.claim_id)
        .join(
            s.relation,
            s.relation.c.relation_id == s.claim_relation.c.relation_id,
        )
        .where(
            sa.or_(
                s.relation.c.source_node_id == node_id,
                s.relation.c.target_node_id == node_id,
            )
        )
        .order_by(s.claim_relation.c.claim_id)
        .limit(1)
    )
    assert claim_id is not None
    return int(claim_id)


def _evidence_only_batch(session: Session, node_id: int) -> int:
    batch_id = _batch(session)
    provenance.add_claim_observation(
        session,
        batch_id,
        _claim_for_node(session, node_id),
        _new_observation(session),
    )
    provenance.mark_promotion_committed(session, batch_id)
    return batch_id


def _activate_context_contract(session: Session) -> int:
    session.execute(
        s.output_schema_definition.update()
        .where(
            s.output_schema_definition.c.task_kind == "NODE_CONTEXT",
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
            ).where(s.output_schema_definition.c.task_kind == "NODE_CONTEXT")
        )
        or 0
    )
    contract_id = session.scalar(
        s.output_schema_definition.insert()
        .values(
            task_kind="NODE_CONTEXT",
            version_no=current_version + 1,
            schema_json=context_product.output_schema(),
            is_active=True,
        )
        .returning(s.output_schema_definition.c.output_schema_definition_id)
    )
    assert contract_id is not None
    return int(contract_id)


def _ready_context_snapshot(
    session: Session, node_id: int
) -> tuple[tuple[object, ...], ...]:
    rows = session.execute(
        sa.select(
            s.node_context.c.node_context_id,
            s.node_context.c.node_search_document_id,
            s.node_context.c.model_task_id,
            s.node_context.c.language,
            s.node_context.c.context_text,
            s.node_context.c.created_at,
        )
        .join(
            s.publication_affected_node,
            sa.and_(
                s.publication_affected_node.c.node_id == s.node_context.c.node_id,
                s.publication_affected_node.c.node_context_id
                == s.node_context.c.node_context_id,
            ),
        )
        .join(
            s.promotion_batch,
            s.promotion_batch.c.promotion_batch_id
            == s.publication_affected_node.c.promotion_batch_id,
        )
        .where(
            s.node_context.c.node_id == node_id,
            s.promotion_batch.c.publication_status == "READY",
        )
        .order_by(s.node_context.c.node_context_id)
    ).all()
    return tuple(tuple(row) for row in rows)


def _set_running_finalizer_precondition(session: Session, task_id: int) -> None:
    """Establish only the external runner state required by the finalizer contract."""
    session.execute(
        s.model_task.update()
        .where(
            s.model_task.c.model_task_id == task_id,
            s.model_task.c.status == "PENDING",
        )
        .values(
            status="RUNNING",
            lease_owner="phase-b-finalizer-boundary",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )


def test_search_document_reuse_and_generation_scoped_task_dedupe() -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()

    with Session(engine) as session, session.begin():
        _activate_context_contract(session)
        first_batch = _evidence_only_batch(session, node_id)

    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, first_batch)
        first_document = publication.ensure_search_document(
            session,
            promotion_batch_id=first_batch,
            node_id=node_id,
        )
        repeated_document = publication.ensure_search_document(
            session,
            promotion_batch_id=first_batch,
            node_id=node_id,
        )
        first_prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=first_batch,
            node_id=node_id,
        )
        first_task = publication.ensure_node_context_task(session, first_prepared)
        repeated_task = publication.ensure_node_context_task(session, first_prepared)

    assert repeated_document == first_document
    assert repeated_task.model_task_id == first_task.model_task_id
    assert repeated_task.status == first_task.status == "PENDING"

    with Session(engine) as session:
        old_ready_contexts = _ready_context_snapshot(session, node_id)
        assert old_ready_contexts
        old_ready_task_ids = {int(row[2]) for row in old_ready_contexts}
        assert first_task.model_task_id not in old_ready_task_ids

        document = session.execute(
            sa.select(
                s.node_search_document.c.identity_text,
                s.node_search_document.c.knowledge_text,
                s.node_search_document.c.input_hash,
                s.node_search_document.c.generator_version,
            ).where(s.node_search_document.c.node_search_document_id == first_document)
        ).one()
        basis = tuple(
            int(value)
            for value in session.scalars(
                sa.select(s.search_document_basis.c.knowledge_item_id)
                .where(
                    s.search_document_basis.c.node_search_document_id == first_document
                )
                .order_by(s.search_document_basis.c.knowledge_item_id)
            )
        )
        assert first_prepared.agent_input.identity_text == document.identity_text
        assert first_prepared.agent_input.knowledge_text == document.knowledge_text
        assert first_prepared.agent_input.basis_ids == basis
        assert set(first_prepared.agent_input.model_dump()) == {
            "node_id",
            "node_type",
            "preferred_alias",
            "identity_text",
            "knowledge_text",
            "basis_ids",
        }

        first_task_row = session.execute(
            sa.select(
                s.model_task.c.status,
                s.model_task.c.attempt_count,
                s.model_task.c.lease_owner,
                s.model_task.c.lease_expires_at,
            ).where(s.model_task.c.model_task_id == first_task.model_task_id)
        ).one()
        attempt_count = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.agent_attempt)
                .where(s.agent_attempt.c.model_task_id == first_task.model_task_id)
            )
            or 0
        )
        assert first_task_row.status == "PENDING"
        assert first_task_row.attempt_count == 0
        assert first_task_row.lease_owner is None
        assert first_task_row.lease_expires_at is None
        assert attempt_count == 0

    with Session(engine) as session, session.begin():
        second_batch = _evidence_only_batch(session, node_id)

    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, second_batch)
        second_document = publication.ensure_search_document(
            session,
            promotion_batch_id=second_batch,
            node_id=node_id,
        )
        second_prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=second_batch,
            node_id=node_id,
        )
        second_task = publication.ensure_node_context_task(session, second_prepared)
        second_task_again = publication.ensure_node_context_task(
            session, second_prepared
        )

    assert second_document == first_document
    assert second_prepared.node_search_document_id == first_document
    assert second_prepared.search_document_input_hash == (
        first_prepared.search_document_input_hash
    )
    assert second_prepared.input_hash != first_prepared.input_hash
    assert second_task.model_task_id != first_task.model_task_id
    assert second_task_again.model_task_id == second_task.model_task_id

    with Session(engine) as session:
        identity_count = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_search_document)
                .where(
                    s.node_search_document.c.node_id == node_id,
                    s.node_search_document.c.input_hash == document.input_hash,
                    s.node_search_document.c.generator_version
                    == document.generator_version,
                )
            )
            or 0
        )
        assert identity_count == 1
        assert _ready_context_snapshot(session, node_id) == old_ready_contexts


def test_context_finalizer_inserts_new_context_and_preserves_ready_history() -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()

    with Session(engine) as session:
        old_ready_contexts = _ready_context_snapshot(session, node_id)
        assert old_ready_contexts
        old_ready_ids = {int(row[0]) for row in old_ready_contexts}

    with Session(engine) as session, session.begin():
        _activate_context_contract(session)
        batch_id = _evidence_only_batch(session, node_id)

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

    with Session(engine) as session:
        search_before = session.execute(
            sa.select(
                s.node_search_document.c.identity_text,
                s.node_search_document.c.knowledge_text,
                s.node_search_document.c.input_hash,
                s.node_search_document.c.generator_version,
            ).where(s.node_search_document.c.node_search_document_id == document_id)
        ).one()
        attempt_rows_before = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.agent_attempt)
                .where(s.agent_attempt.c.model_task_id == task.model_task_id)
            )
            or 0
        )
        assert attempt_rows_before == 0

    with Session(engine) as session, session.begin():
        _set_running_finalizer_precondition(session, task.model_task_id)
        result = publication.apply_node_context(
            session,
            model_task_id=task.model_task_id,
            prepared=prepared,
            proposal=NodeContextProposal(
                context_text="HBF를 둘러싼 공개 관계와 검증된 근거를 짧게 설명합니다."
            ),
            finished_at=datetime.now(UTC),
        )
        assert result.status == "SUCCESS"
        assert result.node_context_id is not None
        context_id = int(result.node_context_id)

    assert context_id not in old_ready_ids

    with Session(engine) as session:
        selected = session.execute(
            sa.select(
                s.publication_affected_node.c.node_search_document_id,
                s.publication_affected_node.c.node_context_id,
                s.node_context.c.node_id,
                s.node_context.c.node_search_document_id.label("context_document_id"),
                s.node_context.c.model_task_id,
                s.node_context.c.language,
                s.node_context.c.context_text,
            )
            .join(
                s.node_context,
                sa.and_(
                    s.node_context.c.node_context_id
                    == s.publication_affected_node.c.node_context_id,
                    s.node_context.c.node_id == s.publication_affected_node.c.node_id,
                    s.node_context.c.node_search_document_id
                    == s.publication_affected_node.c.node_search_document_id,
                ),
            )
            .where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == node_id,
            )
        ).one()
        assert selected.node_search_document_id == document_id
        assert selected.node_context_id == context_id
        assert selected.node_id == node_id
        assert selected.context_document_id == document_id
        assert selected.model_task_id == task.model_task_id
        assert selected.language == "ko"
        assert selected.context_text == (
            "HBF를 둘러싼 공개 관계와 검증된 근거를 짧게 설명합니다."
        )

        context_count = int(
            session.scalar(
                sa.select(sa.func.count())
                .select_from(s.node_context)
                .where(s.node_context.c.model_task_id == task.model_task_id)
            )
            or 0
        )
        assert context_count == 1
        assert _ready_context_snapshot(session, node_id) == old_ready_contexts

        search_after = session.execute(
            sa.select(
                s.node_search_document.c.identity_text,
                s.node_search_document.c.knowledge_text,
                s.node_search_document.c.input_hash,
                s.node_search_document.c.generator_version,
            ).where(s.node_search_document.c.node_search_document_id == document_id)
        ).one()
        assert search_after == search_before

    with Session(engine) as session, session.begin():
        same_task = publication.ensure_node_context_task(session, prepared)
        assert same_task.model_task_id == task.model_task_id
        assert same_task.status == "SUCCESS"
        with pytest.raises(NodeContextTaskError, match="must be RUNNING"):
            publication.apply_node_context(
                session,
                model_task_id=task.model_task_id,
                prepared=prepared,
                proposal=NodeContextProposal(context_text="중복 저장하면 안 됩니다."),
                finished_at=datetime.now(UTC),
            )

    with Session(engine) as session:
        assert _ready_context_snapshot(session, node_id) == old_ready_contexts
        assert (
            int(
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(s.node_context)
                    .where(s.node_context.c.model_task_id == task.model_task_id)
                )
                or 0
            )
            == 1
        )
