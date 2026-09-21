"""Actual runner + migrated PostgreSQL + offline calls through the real harness.

Reference data is synthetic, committed in a guarded isolated test database, then
removed/restored. No product fixtures, model IO, or knowledge writes.
"""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import httpx
import pytest
import sqlalchemy as sa
from openai import APIConnectionError
from pydantic import SecretStr
from sqlalchemy.orm import Session
from test_extraction import candidate, limits, ontology, source_document
from test_extraction_tasks_postgres import reference_data

from ontology_map.db import extraction_tasks as inputs
from ontology_map.db import model_tasks as tasks
from ontology_map.db import schema
from ontology_map.extraction_contracts import KnowledgeProposals
from ontology_map.extraction_provider import (
    ModelStudioGenerationAdapter,
    run_model_studio_extraction,
)
from ontology_map.extraction_runner import RuntimeInput, run_extraction
from ontology_map.llm_config import BASE_URL
from ontology_map.model_studio import FLASH

URL = os.environ.get("ONTOLOGY_MAP_KE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="isolated migrated KE PostgreSQL URL not supplied"
)


class OfflineCalls:
    def __init__(self, *, claims=None, verdict="TRUE", projection_verdict=None):
        self.claims = [candidate()] if claims is None else claims
        self.verdict = verdict
        self.projection_verdict = projection_verdict or verdict
        self.events = []
        self.requests = []
        self.on_preflight = lambda: None
        self.on_send = lambda: None
        self.on_helper = lambda role: None

    def call(self, role, prompt, payload, result_schema, call_limits):
        assert role != "generation", "generation bypassed durable boundary"
        self.events.append(role)
        self.on_helper(role)
        assert role == "claim_review"
        answer = {
            "claims": [
                {
                    "candidate_id": item.claim.candidate_id,
                    "support_verdict": self.verdict,
                    "publication_verdict": "PUBLISH",
                    "bindings": [
                        {
                            "binding_id": binding.binding_id,
                            "verdict": self.projection_verdict,
                        }
                        for binding in item.retained_bindings
                    ],
                }
                for item in payload.candidates
            ]
        }
        return result_schema.model_validate(answer)

    def prepare(self, request):
        self.events.append("preflight")
        self.requests.append(request)
        assert request.model == FLASH
        assert request.output_schema is KnowledgeProposals
        self.on_preflight()

        def send():
            self.events.append("generation")
            self.on_send()
            return KnowledgeProposals.model_validate({"claims": self.claims})

        return send


def row(engine, task_id):
    with engine.connect() as c:
        return dict(
            c.execute(
                sa.select(schema.model_task).where(
                    schema.model_task.c.model_task_id == task_id
                )
            )
            .mappings()
            .one()
        )


def count(engine, table, task_id):
    with engine.connect() as c:
        return c.scalar(
            sa.select(sa.func.count())
            .select_from(table)
            .where(table.c.model_task_id == task_id)
        )


def update_task(engine, task_id, **values):
    with engine.begin() as c:
        c.execute(
            sa.update(schema.model_task)
            .where(schema.model_task.c.model_task_id == task_id)
            .values(**values)
        )


@pytest.fixture
def runtime_task():
    url = sa.engine.make_url(URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_ke127_test")
    engine = sa.create_engine(url)
    with Session(engine) as s, s.begin():
        active_contracts = list(
            s.scalars(
                sa.select(
                    schema.output_schema_definition.c.output_schema_definition_id
                ).where(
                    schema.output_schema_definition.c.is_active,
                    schema.output_schema_definition.c.task_kind
                    == "KNOWLEDGE_EXTRACTION",
                )
            )
        )
        active_policies = list(
            s.scalars(
                sa.select(schema.lint_policy_version.c.lint_policy_version_id).where(
                    schema.lint_policy_version.c.is_active
                )
            )
        )
        company = (
            s.execute(
                sa.select(schema.node_type).where(
                    schema.node_type.c.node_type_code == "COMPANY"
                )
            )
            .mappings()
            .one_or_none()
        )
        data = reference_data(s)
        doc = source_document().model_copy(
            update={"document_id": str(data.document_id)}
        )
        s.execute(
            sa.update(schema.source_document)
            .where(schema.source_document.c.source_document_id == data.document_id)
            .values(
                normalized_body=doc.body,
                body_hash=sha256(doc.body.encode()).digest(),
            )
        )
        runtime = RuntimeInput(doc, ontology(), limits())
        execution = data.execution.model_copy(
            update={
                "runtime_settings": {"extraction_runner": runtime.identity_settings()}
            }
        )
        task_id = inputs.enqueue_extraction(s, data.document_id, execution).task_id
        group_id = s.scalar(
            sa.select(schema.source_document.c.evidence_group_id).where(
                schema.source_document.c.source_document_id == data.document_id
            )
        )
    try:
        yield engine, task_id, execution, runtime
    finally:
        with engine.begin() as c:
            for table in (
                schema.agent_attempt,
                schema.provider_call_slot,
                schema.model_task,
            ):
                c.execute(sa.delete(table).where(table.c.model_task_id == task_id))
            c.execute(
                sa.delete(schema.source_document).where(
                    schema.source_document.c.source_document_id == data.document_id
                )
            )
            c.execute(
                sa.delete(schema.evidence_group).where(
                    schema.evidence_group.c.evidence_group_id == group_id
                )
            )
            c.execute(
                sa.delete(schema.output_schema_definition).where(
                    schema.output_schema_definition.c.output_schema_definition_id
                    == data.contract_id
                )
            )
            c.execute(
                sa.delete(schema.lint_policy_version).where(
                    schema.lint_policy_version.c.lint_policy_version_id
                    == data.policy_id
                )
            )
            c.execute(
                sa.update(schema.output_schema_definition)
                .where(
                    schema.output_schema_definition.c.output_schema_definition_id.in_(
                        active_contracts
                    )
                )
                .values(is_active=True)
            )
            c.execute(
                sa.update(schema.lint_policy_version)
                .where(
                    schema.lint_policy_version.c.lint_policy_version_id.in_(
                        active_policies
                    )
                )
                .values(is_active=True)
            )
            if company is None:
                c.execute(
                    sa.delete(schema.node_type).where(
                        schema.node_type.c.node_type_code == "COMPANY"
                    )
                )
            else:
                c.execute(
                    sa.update(schema.node_type)
                    .where(schema.node_type.c.node_type_code == "COMPANY")
                    .values(is_active=company["is_active"])
                )
        engine.dispose()


def run(data, calls):
    engine, task_id, execution, runtime = data
    return run_extraction(
        engine, task_id, "runner-test", execution, runtime, calls, calls.prepare
    )


def knowledge_counts(engine):
    tables = (
        schema.knowledge_item,
        schema.observation,
        schema.claim,
        schema.relation,
        schema.node,
        schema.promotion_batch,
    )
    with engine.connect() as c:
        return [c.scalar(sa.select(sa.func.count()).select_from(t)) for t in tables]


def test_model_studio_send_happens_after_reserved_slot_commit(runtime_task):
    engine, task_id, execution, runtime = runtime_task
    helpers = OfflineCalls()

    def handle(request: httpx.Request) -> httpx.Response:
        assert count(engine, schema.provider_call_slot, task_id) == 1
        assert count(engine, schema.agent_attempt, task_id) == 0
        with engine.connect() as connection:
            state = connection.scalar(
                sa.select(schema.provider_call_slot.c.state).where(
                    schema.provider_call_slot.c.model_task_id == task_id
                )
            )
        assert state == "RESERVED"
        output = KnowledgeProposals.model_validate(
            {"claims": helpers.claims}
        ).model_dump_json()
        return httpx.Response(
            200,
            request=request,
            json={
                "model": FLASH,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": output},
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 100,
                    "total_tokens": 200,
                },
            },
        )

    adapter = ModelStudioGenerationAdapter(
        SecretStr("offline-key"),
        base_url=BASE_URL,
        transport=httpx.MockTransport(handle),
    )
    try:
        result = run_model_studio_extraction(
            engine,
            task_id,
            "runner-test",
            execution,
            runtime,
            helpers,
            adapter,
        )
    finally:
        adapter.close()
    assert result.disposition == "VERIFIED_RUNTIME"
    assert result.task_status == "RUNNING"
    assert count(engine, schema.provider_call_slot, task_id) == 1
    assert count(engine, schema.agent_attempt, task_id) == 1


def test_verified_result_uses_one_product_slot_and_writes_no_knowledge(runtime_task):
    engine, task_id, execution, runtime = runtime_task
    calls = OfflineCalls()
    before = knowledge_counts(engine)

    def before_send():
        # A separate connection sees the reservation's durable COMMIT.
        assert count(engine, schema.provider_call_slot, task_id) == 1
        assert count(engine, schema.agent_attempt, task_id) == 0

    calls.on_send = before_send
    result = run(runtime_task, calls)
    assert result.task_status == "RUNNING"
    assert result.disposition == "VERIFIED_RUNTIME"
    assert [c.candidate_id for c in result.extraction.verified] == ["c1"]
    assert result.lease is not None
    assert calls.events == [
        "preflight",
        "generation",
        "claim_review",
    ]
    assert count(engine, schema.provider_call_slot, task_id) == 1
    assert count(engine, schema.agent_attempt, task_id) == 1
    assert row(engine, task_id)["attempt_count"] == 1
    assert row(engine, task_id)["status"] == "RUNNING"
    assert row(engine, task_id)["finished_at"] is None
    assert knowledge_counts(engine) == before


@pytest.mark.parametrize("case", ["zero", "all_blocked", "no_supported_binding"])
def test_runtime_zero_and_blocked_are_not_product_terminal_states(runtime_task, case):
    engine, task_id, _, _ = runtime_task
    calls = OfflineCalls(
        claims=[] if case == "zero" else None,
        verdict="FALSE" if case == "all_blocked" else "TRUE",
        projection_verdict="FALSE" if case == "no_supported_binding" else None,
    )
    result = run(runtime_task, calls)
    expected = "ZERO_RESULT" if case == "zero" else "ALL_BLOCKED"
    assert result.disposition == expected
    assert result.task_status == row(engine, task_id)["status"] == "RUNNING"
    assert result.extraction.verified == []
    assert count(engine, schema.provider_call_slot, task_id) == 1


@pytest.mark.parametrize("case", ["settings", "current_input"])
def test_input_rejection_precedes_any_model_or_slot(runtime_task, case):
    engine, task_id, execution, runtime = runtime_task
    if case == "settings":
        runtime = replace(runtime, include_structure=True)
    else:
        execution = execution.model_copy(update={"corrective_input": "changed input"})
    calls = OfflineCalls()
    result = run((engine, task_id, execution, runtime), calls)
    assert result.task_status == "FINAL_FAILED"
    assert calls.events == []
    assert count(engine, schema.provider_call_slot, task_id) == 0


def test_preflight_error_is_terminal_without_product_slot(runtime_task):
    engine, task_id, _, _ = runtime_task
    calls = OfflineCalls()

    def reject():
        raise ValueError("private local credential error")

    calls.on_preflight = reject
    result = run(runtime_task, calls)
    assert result.task_status == "FINAL_FAILED"
    assert result.disposition == "FAILED"
    assert calls.events == ["preflight"]
    assert count(engine, schema.provider_call_slot, task_id) == 0
    assert "private" not in repr(result)


def test_confirmed_timeout_records_retry_without_runner_loop(runtime_task):
    engine, task_id, _, _ = runtime_task
    calls = OfflineCalls()

    def timeout():
        request = httpx.Request("POST", "https://example.invalid")
        response = httpx.Response(504, request=request)
        raise httpx.HTTPStatusError(
            "confirmed provider timeout", request=request, response=response
        )

    calls.on_send = timeout
    first = run(runtime_task, calls)
    assert first.task_status == "RETRY_WAIT"
    assert calls.events == ["preflight", "generation"]
    assert row(engine, task_id)["attempt_count"] == 1
    calls.on_send = lambda: None
    assert run(runtime_task, calls).disposition == "NOT_CLAIMED"
    update_task(
        engine,
        task_id,
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    second = run(runtime_task, calls)
    assert second.disposition == "VERIFIED_RUNTIME"
    assert row(engine, task_id)["attempt_count"] == 2
    assert calls.events.count("generation") == 2


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("private ambiguous wire failure"),
        httpx.ReadTimeout("private lost reply timeout"),
        httpx.ReadError("private lost reply"),
        APIConnectionError(
            request=httpx.Request("POST", "https://example.invalid"),
            message="private ambiguous SDK connection failure",
        ),
    ],
)
def test_unconfirmed_generation_leaves_reservation_for_reclaim(runtime_task, error):
    engine, task_id, _, _ = runtime_task
    calls = OfflineCalls()

    def unknown():
        raise error

    calls.on_send = unknown
    result = run(runtime_task, calls)
    assert result.disposition == "AWAITING_RECLAIM"
    assert result.task_status == row(engine, task_id)["status"] == "RUNNING"
    assert count(engine, schema.provider_call_slot, task_id) == 1
    assert count(engine, schema.agent_attempt, task_id) == 0
    assert row(engine, task_id)["attempt_count"] == 0
    with engine.connect() as c:
        assert (
            c.scalar(
                sa.select(schema.provider_call_slot.c.state).where(
                    schema.provider_call_slot.c.model_task_id == task_id
                )
            )
            == "RESERVED"
        )
    assert "private" not in repr(result)


def test_helper_transport_failure_is_not_a_product_attempt(runtime_task):
    engine, task_id, _, _ = runtime_task
    calls = OfflineCalls()

    def helper_failure(role):
        if role == "claim_review":
            raise httpx.ReadTimeout("private helper payload")

    calls.on_helper = helper_failure
    result = run(runtime_task, calls)
    assert result.task_status == "RETRY_WAIT"
    assert count(engine, schema.agent_attempt, task_id) == 1
    with engine.connect() as c:
        assert (
            c.scalar(
                sa.select(schema.agent_attempt.c.outcome).where(
                    schema.agent_attempt.c.model_task_id == task_id
                )
            )
            == "SUCCESS"
        )


def test_return_discards_payload_when_helper_outlives_lease(runtime_task):
    engine, task_id, _, _ = runtime_task
    calls = OfflineCalls()

    def lose_lease(role):
        if role == "claim_review":
            update_task(
                engine,
                task_id,
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            with Session(engine) as s, s.begin():
                assert tasks.claim_task(s, task_id, "new-owner") is not None

    calls.on_helper = lose_lease
    result = run(runtime_task, calls)
    assert result.disposition == "LEASE_LOST"
    assert result.extraction is None and result.lease is None
    assert row(engine, task_id)["lease_owner"].startswith("new-owner:")


def test_malformed_generation_is_contract_error_not_fake_success(runtime_task):
    engine, task_id, _, _ = runtime_task
    calls = OfflineCalls(claims=[{"invalid": "private model output"}])
    result = run(runtime_task, calls)
    assert result.task_status == "RETRY_WAIT"
    assert calls.events == ["preflight", "generation"]
    with engine.connect() as c:
        assert (
            c.scalar(
                sa.select(schema.agent_attempt.c.outcome).where(
                    schema.agent_attempt.c.model_task_id == task_id
                )
            )
            == "OUTPUT_CONTRACT_ERROR"
        )


def test_existing_success_is_noop_without_helper_or_preflight(runtime_task):
    engine, task_id, _, _ = runtime_task
    update_task(engine, task_id, status="SUCCESS", finished_at=datetime.now(UTC))
    calls = OfflineCalls()
    result = run(runtime_task, calls)
    assert result.disposition == "NOT_CLAIMED" and result.task_status == "SUCCESS"
    assert calls.events == []
    assert count(engine, schema.provider_call_slot, task_id) == 0
