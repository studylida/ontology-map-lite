"""Durable effective-input identity regressions for #215 NODE_CONTEXT."""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from test_initial_publication_phase_b_postgres import (
    _activate_context_contract,
    _engine,
    _evidence_only_batch,
)

from ontology_map import node_context_execution
from ontology_map.db import initial_publication as publication
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.model_studio import CallLimits
from ontology_map.node_context_generation_contracts import NodeContextProposal
from ontology_map.node_context_runner import run_node_context

DATABASE_URL = os.environ.get("ONTOLOGY_MAP_INITIAL_PUBLICATION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="isolated migrated PostgreSQL URL was not supplied",
)


@pytest.fixture(autouse=True)
def _isolate_database() -> None:
    if not DATABASE_URL:
        yield
        return
    names = ", ".join(f'"{table.name}"' for table in s.metadata.sorted_tables)
    engine = _engine()
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
            )
        yield
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
            )


def _task_identity(
    session: Session,
    task_id: int,
) -> tuple[bytes, bytes, str, str, int]:
    row = session.execute(
        sa.select(
            s.model_task.c.input_hash,
            s.model_task.c.cache_key,
            s.model_task.c.model_version,
            s.model_task.c.prompt_version,
            s.model_task.c.output_schema_definition_id,
        ).where(s.model_task.c.model_task_id == task_id)
    ).one()
    return (
        bytes(row.input_hash),
        bytes(row.cache_key),
        str(row.model_version),
        str(row.prompt_version),
        int(row.output_schema_definition_id),
    )


def test_result_affecting_setting_change_creates_new_logical_context_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()

    with Session(engine) as session, session.begin():
        _activate_context_contract(session)
        batch_id = _evidence_only_batch(session, node_id)
        publication.start_initial_publication(session, batch_id)
        publication.ensure_search_document(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
        old_prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
        old_task = publication.ensure_node_context_task(session, old_prepared)

    result = run_node_context(
        engine,
        old_task.model_task_id,
        "node-context-identity-old",
        promotion_batch_id=batch_id,
        node_id=node_id,
        prepare_provider=lambda _prepared: (
            lambda: NodeContextProposal(context_text="기존 실행 설정의 맥락입니다.")
        ),
    )
    assert result.task_status == "SUCCESS"

    with Session(engine) as session:
        old_identity = _task_identity(session, old_task.model_task_id)
        old_status = session.scalar(
            sa.select(s.model_task.c.status).where(
                s.model_task.c.model_task_id == old_task.model_task_id
            )
        )
    assert old_status == "SUCCESS"

    original = node_context_execution.NODE_CONTEXT_LIMITS
    changed_limits = CallLimits(
        max_input_tokens=original.max_input_tokens,
        max_output_tokens=original.max_output_tokens - 1,
        max_request_bytes=original.max_request_bytes,
    )
    monkeypatch.setattr(
        node_context_execution,
        "NODE_CONTEXT_LIMITS",
        changed_limits,
    )

    with Session(engine) as session, session.begin():
        changed_prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
        changed_task = publication.ensure_node_context_task(session, changed_prepared)
        repeated_prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )
        repeated_task = publication.ensure_node_context_task(session, repeated_prepared)

    with Session(engine) as session:
        changed_identity = _task_identity(session, changed_task.model_task_id)

    assert changed_prepared.promotion_batch_id == old_prepared.promotion_batch_id
    assert (
        changed_prepared.node_search_document_id == old_prepared.node_search_document_id
    )
    assert (
        changed_prepared.search_document_input_hash
        == old_prepared.search_document_input_hash
    )
    assert changed_prepared.agent_input == old_prepared.agent_input
    assert changed_prepared.input_hash != old_prepared.input_hash
    assert changed_identity[0] != old_identity[0]
    assert changed_identity[1] != old_identity[1]
    assert changed_identity[2:] == old_identity[2:]
    assert changed_task.model_task_id != old_task.model_task_id
    assert changed_task.status == "PENDING"

    assert repeated_prepared == changed_prepared
    assert repeated_task.model_task_id == changed_task.model_task_id
    assert repeated_task.status == "PENDING"
