"""Actual reference lookup and idempotency, in an isolated rollback-only DB."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import extraction_tasks as queue
from ontology_map.db import schema
from ontology_map.extraction_contracts import KnowledgeProposals

URL = os.environ.get("ONTOLOGY_MAP_KE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="isolated migrated KE PostgreSQL URL not supplied"
)


@dataclass
class ReferenceData:
    session: Session
    document_id: int
    contract_id: int
    policy_id: int
    execution: queue.ExecutionInput


def reference_data(session):
    # These changes are visible only inside this isolated test transaction.
    # They are not a product seed or a fixture upgrade.
    session.execute(
        sa.update(schema.output_schema_definition)
        .where(schema.output_schema_definition.c.task_kind == queue.TASK_KIND)
        .values(is_active=False)
    )
    version = session.scalar(
        sa.select(
            sa.func.coalesce(
                sa.func.max(schema.output_schema_definition.c.version_no), 0
            )
            + 1
        ).where(schema.output_schema_definition.c.task_kind == queue.TASK_KIND)
    )
    contract_id = session.scalar(
        sa.insert(schema.output_schema_definition)
        .values(
            task_kind=queue.TASK_KIND,
            version_no=version,
            schema_json=KnowledgeProposals.model_json_schema(),
            is_active=True,
        )
        .returning(schema.output_schema_definition.c.output_schema_definition_id)
    )
    session.execute(sa.update(schema.lint_policy_version).values(is_active=False))
    policy_version = session.scalar(
        sa.select(
            sa.func.coalesce(sa.func.max(schema.lint_policy_version.c.version_no), 0)
            + 1
        )
    )
    policy_id = session.scalar(
        sa.insert(schema.lint_policy_version)
        .values(
            version_no=policy_version,
            validator_version="ke127-input-test",
            is_active=True,
            activated_at=datetime.now(UTC),
        )
        .returning(schema.lint_policy_version.c.lint_policy_version_id)
    )
    session.execute(
        sa.text("""
        INSERT INTO node_type
          (node_type_code, display_name, creation_rule, is_active)
        VALUES ('COMPANY', '회사', '합성 시험용 원문 근거', true)
        ON CONFLICT (node_type_code) DO NOTHING
    """)
    )
    session.execute(
        sa.update(schema.node_type)
        .where(schema.node_type.c.node_type_code == "COMPANY")
        .values(is_active=True)
    )
    group_id = session.scalar(
        sa.insert(schema.evidence_group).returning(
            schema.evidence_group.c.evidence_group_id
        )
    )
    body = "합성회사에서 기술을 발표했다."
    document_id = session.scalar(
        sa.insert(schema.source_document)
        .values(
            evidence_group_id=group_id,
            source_key=f"ke127-input:{uuid4()}",
            version_no=1,
            canonical_url="https://example.invalid/ke127-input",
            publisher_name="합성 시험 발행처",
            title="합성 입력 문서",
            original_language="ko",
            normalized_body=body,
            body_hash=sha256(body.encode()).digest(),
            published_precision="UNKNOWN",
            modified_precision="UNKNOWN",
            last_checked_at=datetime.now(UTC),
            last_check_status="SUCCESS",
        )
        .returning(schema.source_document.c.source_document_id)
    )
    return ReferenceData(
        session,
        document_id,
        contract_id,
        policy_id,
        queue.ExecutionInput(
            validator_version="ke127-input-test",
            runtime_settings={"max_candidates": 4, "structure": False},
        ),
    )


@pytest.fixture
def references():
    url = sa.engine.make_url(URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_ke127_test")
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with Session(bind=connection) as session:
                    yield reference_data(session)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def enqueue(data, execution=None):
    return queue.enqueue_extraction(
        data.session, data.document_id, execution or data.execution
    )


def task_count(data):
    return data.session.scalar(
        sa.select(sa.func.count())
        .select_from(schema.model_task)
        .where(schema.model_task.c.source_document_id == data.document_id)
    )


@pytest.mark.parametrize("kind", ["missing", "mismatch"])
def test_active_output_contract_is_required_before_task_creation(references, kind):
    data = references
    change = (
        {"is_active": False}
        if kind == "missing"
        else {"schema_json": {"type": "object"}}
    )
    data.session.execute(
        sa.update(schema.output_schema_definition)
        .where(
            schema.output_schema_definition.c.output_schema_definition_id
            == data.contract_id
        )
        .values(**change)
    )
    with pytest.raises(queue.ReferenceNotReady, match="EXTRACTION_CONTRACT"):
        enqueue(data)
    assert task_count(data) == 0


@pytest.mark.parametrize("kind", ["validator", "policy", "node_types"])
def test_reference_absence_is_not_successful_zero(references, kind):
    data = references
    execution = data.execution
    if kind == "validator":
        execution = execution.model_copy(update={"validator_version": "other"})
    elif kind == "policy":
        data.session.execute(
            sa.update(schema.lint_policy_version).values(is_active=False)
        )
    else:
        data.session.execute(sa.update(schema.node_type).values(is_active=False))
    with pytest.raises(queue.ReferenceNotReady):
        enqueue(data, execution)
    assert task_count(data) == 0


@pytest.mark.parametrize("kind", ["hash", "normalization", "missing"])
def test_source_identity_checks_real_immutable_document(references, kind):
    data = references
    if kind == "missing":
        with pytest.raises(queue.ReferenceNotReady, match="SOURCE_DOCUMENT_MISSING"):
            queue.enqueue_extraction(data.session, -1, data.execution)
    else:
        change = (
            {"body_hash": bytes(32)}
            if kind == "hash"
            else {"normalized_body": "합성\r\n문서"}
        )
        data.session.execute(
            sa.update(schema.source_document)
            .where(schema.source_document.c.source_document_id == data.document_id)
            .values(**change)
        )
        with pytest.raises(queue.ReferenceNotReady, match="SOURCE_"):
            enqueue(data)
    assert task_count(data) == 0


@pytest.mark.parametrize(
    "state",
    [
        "PENDING",
        "RUNNING",
        "RETRY_WAIT",
        "SUCCESS",
        "VALIDATION_BLOCKED",
        "FINAL_FAILED",
    ],
)
def test_same_key_never_resets_existing_state_or_allocates_slot(references, state):
    data = references
    first = enqueue(data)
    now = datetime.now(UTC)
    values = {
        "status": state,
        "finished_at": None,
        "next_attempt_at": None,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    if state == "RUNNING":
        values.update(
            lease_owner="test-owner", lease_expires_at=now + timedelta(minutes=10)
        )
    elif state == "RETRY_WAIT":
        values.update(next_attempt_at=now + timedelta(seconds=30))
    elif state in {"SUCCESS", "VALIDATION_BLOCKED", "FINAL_FAILED"}:
        values.update(finished_at=now)
    data.session.execute(
        sa.update(schema.model_task)
        .where(schema.model_task.c.model_task_id == first.task_id)
        .values(**values)
    )
    statement = sa.select(schema.model_task).where(
        schema.model_task.c.model_task_id == first.task_id
    )
    before = dict(data.session.execute(statement).mappings().one())
    second = enqueue(data)
    after = dict(data.session.execute(statement).mappings().one())
    assert first.created and not second.created
    assert second.task_id == first.task_id and second.status == state
    assert before == after
    assert task_count(data) == 1
    for table in (schema.provider_call_slot, schema.agent_attempt):
        assert (
            data.session.scalar(
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.model_task_id == first.task_id)
            )
            == 0
        )


def test_effective_input_is_deterministic_and_reprocess_does_not_reset(references):
    data = references
    first = enqueue(data)
    reordered = data.execution.model_copy(
        update={"runtime_settings": {"structure": False, "max_candidates": 4}}
    )
    assert enqueue(data, reordered).identity == first.identity
    changed = data.execution.model_copy(update={"corrective_input": "주체를 보존"})
    second = enqueue(data, changed)
    generation = changed.model_copy(update={"execution_generation": "manual-2"})
    third = enqueue(data, generation)
    assert len({first.task_id, second.task_id, third.task_id}) == 3
    assert (
        len(
            {
                first.identity.cache_key,
                second.identity.cache_key,
                third.identity.cache_key,
            }
        )
        == 3
    )
    assert (
        queue.require_current_input(data.session, first.task_id, data.execution)
        == first.identity
    )
    with pytest.raises(queue.InputChanged):
        queue.require_current_input(data.session, first.task_id, generation)


def test_reference_change_requires_new_effective_input_not_old_task_rewrite(references):
    data = references
    first = enqueue(data)
    data.session.execute(
        sa.update(schema.node_type)
        .where(schema.node_type.c.node_type_code == "COMPANY")
        .values(creation_rule="변경된 합성 검증 규칙")
    )
    with pytest.raises(queue.InputChanged, match="EXTRACTION_INPUT_CHANGED"):
        queue.require_current_input(data.session, first.task_id, data.execution)
    second = enqueue(data)
    assert second.created and second.task_id != first.task_id
    assert first.identity.input_hash != second.identity.input_hash
    assert task_count(data) == 2


def test_enqueue_write_uses_callers_transaction(references):
    data = references
    with pytest.raises(RuntimeError, match="ROLLBACK"):
        with data.session.begin_nested():
            enqueue(data)
            raise RuntimeError("ROLLBACK")
    assert task_count(data) == 0
