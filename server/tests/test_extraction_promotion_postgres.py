"""B-3 canonical promotion regression on a dedicated migrated PostgreSQL DB."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from test_extraction import limits
from test_extraction_tasks_postgres import reference_data

from ontology_map.db import extraction_promotion as promotion_db
from ontology_map.db import extraction_tasks as inputs
from ontology_map.db import model_tasks as tasks
from ontology_map.db import product_lint, schema
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db.topic_references import (
    ensure_topic_reference,
    set_topic_reference_active,
)
from ontology_map.entity_resolution_contracts import (
    ResolutionBatchInput,
    ResolutionInput,
)
from ontology_map.extraction import ExtractionResult
from ontology_map.extraction_contracts import (
    AttributeRule,
    ClaimProposal,
    Ontology,
    RelationProposal,
    RelationRule,
    SourceDocument,
    SourceSpan,
    digest,
)
from ontology_map.extraction_promotion import finalize_extraction
from ontology_map.extraction_runner import RunnerResult, RuntimeInput

URL = os.environ.get("ONTOLOGY_MAP_KE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="isolated migrated #127 PostgreSQL URL not supplied"
)


@dataclass(frozen=True)
class B3Case:
    engine: Engine
    task_id: int
    execution: inputs.ExecutionInput
    runtime: RuntimeInput
    claims: tuple[ClaimProposal, ...]
    lease: tasks.Lease


def _truncate(engine: Engine) -> None:
    names = ", ".join(f'"{table.name}"' for table in schema.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


def _document(document_id: int) -> SourceDocument:
    quotes = (
        "한빛과 푸른은 공동 개발을 진행했다.",
        "한빛 발표회는 2026년 9월 1일 열렸고 한빛의 메모리 대역폭은 100 GB/s였다.",
    )
    body = "\n".join(quotes)
    spans: list[SourceSpan] = []
    start = 0
    for index, quote in enumerate(quotes):
        spans.append(
            SourceSpan(
                source_id=f"s{index}",
                start=start,
                end=start + len(quote),
                quote=quote,
                quote_hash=digest(quote),
                paragraph_id=f"p{index}",
            )
        )
        start += len(quote) + 1
    return SourceDocument(
        document_id=str(document_id),
        body=body,
        body_hash=digest(body),
        sources=tuple(spans),
    )


def _node_type(session: Session, code: str) -> int:
    existing = session.scalar(
        sa.select(schema.node_type.c.node_type_id).where(
            schema.node_type.c.node_type_code == code
        )
    )
    if existing is not None:
        session.execute(
            sa.update(schema.node_type)
            .where(schema.node_type.c.node_type_id == existing)
            .values(is_active=True)
        )
        return int(existing)
    return int(
        session.execute(
            sa.insert(schema.node_type)
            .values(
                node_type_code=code,
                display_name=code,
                creation_rule=f"#127 B-3 synthetic {code} creation rule",
                is_active=True,
            )
            .returning(schema.node_type.c.node_type_id)
        ).scalar_one()
    )


def _relation(session: Session, company_type_id: int) -> int:
    relation_type_id = int(
        session.execute(
            sa.insert(schema.relation_type)
            .values(relation_code="COLLABORATES_WITH")
            .returning(schema.relation_type.c.relation_type_id)
        ).scalar_one()
    )
    revision_id = int(
        session.execute(
            sa.insert(schema.relation_type_revision)
            .values(
                relation_type_id=relation_type_id,
                version_no=1,
                display_name="협력",
                directionality="SYMMETRIC",
                is_active=True,
            )
            .returning(schema.relation_type_revision.c.relation_type_revision_id)
        ).scalar_one()
    )
    session.execute(
        sa.insert(schema.relation_endpoint_rule).values(
            relation_type_revision_id=revision_id,
            source_node_type_id=company_type_id,
            target_node_type_id=company_type_id,
        )
    )
    return revision_id


def _attribute(session: Session, company_type_id: int) -> int:
    attribute_id = int(
        session.execute(
            sa.insert(schema.attribute)
            .values(attribute_code="MAX_MEMORY_BANDWIDTH")
            .returning(schema.attribute.c.attribute_id)
        ).scalar_one()
    )
    revision_id = int(
        session.execute(
            sa.insert(schema.attribute_revision)
            .values(
                attribute_id=attribute_id,
                version_no=1,
                display_name="최대 메모리 대역폭",
                target_node_type_id=company_type_id,
                allowed_value_kind="NUMBER",
                is_active=True,
            )
            .returning(schema.attribute_revision.c.attribute_revision_id)
        ).scalar_one()
    )
    session.execute(
        sa.insert(schema.attribute_revision_allowed_unit).values(
            attribute_revision_id=revision_id,
            allowed_value_kind="NUMBER",
            unit_code="GB_PER_S",
        )
    )
    return revision_id


def _claims(*, conflicting_event: bool = False) -> tuple[ClaimProposal, ...]:
    mentions = [
        {
            "mention_id": "m-company-a",
            "text": "한빛",
            "node_type": "COMPANY",
            "source_ids": ["s0"],
            "topic_name": None,
        },
        {
            "mention_id": "m-company-b",
            "text": "푸른",
            "node_type": "COMPANY",
            "source_ids": ["s0"],
            "topic_name": None,
        },
    ]
    event_mention = {
        "mention_id": "m-event",
        "text": "한빛 발표회",
        "node_type": "EVENT",
        "source_ids": ["s1"],
        "topic_name": None,
    }
    bindings = [
        {
            "kind": "RELATION",
            "binding_id": "r1",
            "code": "COLLABORATES_WITH",
            "source_mention": "m-company-a",
            "target_mention": "m-company-b",
            "stance": "SUPPORT",
        },
        {
            "kind": "ATTRIBUTE",
            "binding_id": "a1",
            "code": "MAX_MEMORY_BANDWIDTH",
            "target_mention": "m-company-a",
            "value": {"kind": "NUMBER", "value": Decimal("100"), "unit": "GB_PER_S"},
        },
        {
            "kind": "EVENT_TIME",
            "binding_id": "e1",
            "event_mention": "m-event",
            "start": {"value": datetime(2026, 9, 1, tzinfo=UTC), "precision": "DAY"},
            "end": {"value": None, "precision": "UNKNOWN"},
        },
    ]
    if conflicting_event:
        bindings.append(
            {
                "kind": "EVENT_TIME",
                "binding_id": "e2",
                "event_mention": "m-event",
                "start": {
                    "value": datetime(2026, 9, 2, tzinfo=UTC),
                    "precision": "DAY",
                },
                "end": {"value": None, "precision": "UNKNOWN"},
            }
        )
    first = ClaimProposal.model_validate(
        {
            "candidate_id": "c-multi",
            "statement": (
                "한빛과 푸른은 공동 개발을 진행했고 한빛의 최대 메모리 "
                "대역폭은 100 GB/s이며 한빛 발표회는 2026년 9월 1일 열렸다."
            ),
            "modality": "FACT",
            "source_ids": ["s0", "s1"],
            "mentions": [*mentions, event_mention],
            "bindings": bindings,
        }
    )
    second = ClaimProposal.model_validate(
        {
            "candidate_id": "c-relation",
            "statement": "한빛과 푸른은 공동 개발을 진행했다.",
            "modality": "FACT",
            "source_ids": ["s0"],
            "mentions": mentions,
            "bindings": [
                {
                    "kind": "RELATION",
                    "binding_id": "r2",
                    "code": "COLLABORATES_WITH",
                    "source_mention": "m-company-a",
                    "target_mention": "m-company-b",
                    "stance": "SUPPORT",
                }
            ],
        }
    )
    return first, second


def _seed(
    engine: Engine, *, conflicting_event: bool = False, product_policy: bool = False
) -> B3Case:
    with Session(engine) as session, session.begin():
        data = reference_data(session)
        if product_policy:
            session.execute(
                sa.delete(schema.lint_policy_version).where(
                    schema.lint_policy_version.c.lint_policy_version_id
                    == data.policy_id
                )
            )
            product_lint.ensure_product_policy(session)
        company_type_id = _node_type(session, "COMPANY")
        _node_type(session, "EVENT")
        relation_revision = _relation(session, company_type_id)
        attribute_revision = _attribute(session, company_type_id)
        document = _document(data.document_id)
        session.execute(
            sa.update(schema.source_document)
            .where(schema.source_document.c.source_document_id == data.document_id)
            .values(
                normalized_body=document.body,
                body_hash=sha256(document.body.encode()).digest(),
            )
        )
        ontology = Ontology(
            node_types=("COMPANY", "EVENT"),
            topics=(),
            relations=(
                RelationRule(
                    code="COLLABORATES_WITH",
                    version_no=1,
                    revision_id=relation_revision,
                    description="두 회사의 공동 행위",
                    direction="SYMMETRIC",
                    endpoints=(("COMPANY", "COMPANY"),),
                ),
            ),
            attributes=(
                AttributeRule(
                    code="MAX_MEMORY_BANDWIDTH",
                    version_no=1,
                    revision_id=attribute_revision,
                    description="최대 메모리 대역폭",
                    node_type="COMPANY",
                    value_kind="NUMBER",
                    units=("GB_PER_S",),
                ),
            ),
        )
        runtime = RuntimeInput(document, ontology, limits())
        execution = data.execution.model_copy(
            update={
                "validator_version": (
                    product_lint.VALIDATOR_VERSION
                    if product_policy
                    else data.execution.validator_version
                ),
                "runtime_settings": {"extraction_runner": runtime.identity_settings()},
            }
        )
        task_id = inputs.enqueue_extraction(
            session, data.document_id, execution
        ).task_id
    with Session(engine) as session, session.begin():
        lease = tasks.claim_task(session, task_id, "b3-test")
        assert lease is not None
    with Session(engine) as session, session.begin():
        slot = tasks.reserve_slot(session, lease)
        assert slot is not None
    with Session(engine) as session, session.begin():
        assert (
            tasks.record_terminal(
                session,
                slot,
                tasks.TerminalResult("SUCCESS", datetime.now(UTC)),
            )
            == "RUNNING"
        )
    return B3Case(
        engine=engine,
        task_id=task_id,
        execution=execution,
        runtime=runtime,
        claims=_claims(conflicting_event=conflicting_event),
        lease=lease,
    )


@pytest.fixture
def b3_case() -> B3Case:
    url = sa.engine.make_url(URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_ke127_test")
    engine = sa.create_engine(url)
    _truncate(engine)
    try:
        yield _seed(engine)
    finally:
        engine.dispose()


def _runner(
    case: B3Case, claims: tuple[ClaimProposal, ...] | None = None
) -> RunnerResult:
    values = case.claims if claims is None else claims
    extraction = ExtractionResult(generated=list(values), verified=list(values))
    return RunnerResult("RUNNING", "VERIFIED_RUNTIME", case.lease, extraction)


def _batch(
    payload: ResolutionBatchInput,
    decide: Callable[[ResolutionInput], dict[str, object]],
) -> object:
    return {
        "resolutions": [
            {"mention_id": item.mention_id, **decide(item.input)}
            for item in payload.mentions
        ]
    }


def _new(payload: ResolutionBatchInput) -> object:
    return _batch(payload, lambda _item: {"decision": "NEW", "node_id": None})


def _count(engine: Engine, table: sa.Table) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(sa.select(sa.func.count()).select_from(table)) or 0
        )


def _provenance_kinds(engine: Engine, batch_id: int) -> set[str]:
    with Session(engine) as session:
        return {
            item.change_kind for item in provenance.changes_for_batch(session, batch_id)
        }


def test_new_multi_target_claim_promotes_atomically_and_reuses_relation(
    b3_case: B3Case,
) -> None:
    case = b3_case
    result = finalize_extraction(
        case.engine,
        _runner(case),
        case.execution,
        case.runtime,
        _new,
    )
    assert result.disposition == result.task_status == "SUCCESS"
    assert result.promotion_batch_id is not None
    assert result.accepted_claims == ("c-multi", "c-relation")
    assert _count(case.engine, schema.node) == 3
    assert _count(case.engine, schema.relation) == 1
    assert _count(case.engine, schema.claim) == 2
    assert _count(case.engine, schema.observation) == 2
    assert _count(case.engine, schema.claim_observation) == 3
    assert _count(case.engine, schema.claim_relation) == 2
    assert _count(case.engine, schema.claim_attribute_value) == 1
    assert _count(case.engine, schema.event_temporal_extent) == 1
    assert _count(case.engine, schema.event_temporal_basis) == 1
    with case.engine.connect() as connection:
        batch = (
            connection.execute(
                sa.select(schema.promotion_batch).where(
                    schema.promotion_batch.c.promotion_batch_id
                    == result.promotion_batch_id
                )
            )
            .mappings()
            .one()
        )
        task = (
            connection.execute(
                sa.select(schema.model_task).where(
                    schema.model_task.c.model_task_id == case.task_id
                )
            )
            .mappings()
            .one()
        )
    assert batch["promotion_status"] == "COMMITTED"
    assert batch["publication_status"] == "NOT_STARTED"
    assert task["status"] == "SUCCESS" and task["finished_at"] is not None
    kinds = _provenance_kinds(case.engine, result.promotion_batch_id)
    assert {
        "NODE_ALIAS_CHANGED",
        "NODE_ALIAS_EVIDENCE_ADDED",
        "CLAIM_OBSERVATION_ADDED",
        "CLAIM_RELATION_ADDED",
        "CLAIM_ATTRIBUTE_VALUE_ADDED",
        "EVENT_TEMPORAL_BASIS_ADDED",
    } <= kinds


def test_new_relation_without_support_blocks_only_dependent_claim(
    b3_case: B3Case,
) -> None:
    case = b3_case
    dispute = case.claims[1].model_copy(
        update={
            "bindings": [
                case.claims[1].bindings[0].model_copy(update={"stance": "DISPUTE"})
            ]
        }
    )
    independent = case.claims[0].model_copy(
        update={
            "bindings": [
                binding
                for binding in case.claims[0].bindings
                if not isinstance(binding, RelationProposal)
            ],
            "mentions": [
                mention
                for mention in case.claims[0].mentions
                if mention.mention_id != "m-company-b"
            ],
        }
    )
    result = finalize_extraction(
        case.engine,
        _runner(case, (independent, dispute)),
        case.execution,
        case.runtime,
        _new,
    )
    assert result.disposition == "SUCCESS"
    assert result.accepted_claims == ("c-multi",)
    assert result.excluded_claims == ("c-relation",)
    assert _count(case.engine, schema.relation) == 0
    assert _count(case.engine, schema.claim) == 1


@pytest.mark.parametrize("stance", ["SUPPORT", "DISPUTE"])
def test_blocking_existing_relation_excludes_only_dependent_claim(stance: str) -> None:
    assert URL is not None
    engine = sa.create_engine(URL)
    _truncate(engine)
    try:
        case = _seed(engine, product_policy=True)
        first = finalize_extraction(
            engine,
            _runner(case, (case.claims[1],)),
            case.execution,
            case.runtime,
            _new,
        )
        assert first.disposition == "SUCCESS"
        with Session(engine) as session, session.begin():
            relation_id = int(session.scalar(sa.select(schema.relation.c.relation_id)))
            existing = dict(
                session.execute(
                    sa.select(
                        schema.node_alias.c.alias_text, schema.node_alias.c.node_id
                    )
                ).all()
            )
            policy_id, rules = product_lint.require_product_policy(session)
            now = datetime.now(UTC)
            run_id = int(
                session.execute(
                    schema.lint_run.insert()
                    .values(
                        lint_policy_version_id=policy_id,
                        status="SUCCESS",
                        started_at=now,
                        completed_at=now,
                    )
                    .returning(schema.lint_run.c.lint_run_id)
                ).scalar_one()
            )
            session.execute(
                schema.lint_finding.insert().values(
                    finding_key=sha256(
                        f"{policy_id}:RELATION_SUPPORTED:{relation_id}".encode()
                    ).digest(),
                    knowledge_item_id=relation_id,
                    lint_policy_rule_id=rules["RELATION_SUPPORTED"],
                    first_detected_run_id=run_id,
                    latest_detected_run_id=run_id,
                    first_detected_at=now,
                    last_detected_at=now,
                    message="RELATION_SUPPORTED",
                )
            )
            assert not promotion_db._usable_knowledge(session, relation_id, "RELATION")
            assert promotion_db.relation_has_supported_claim(session, relation_id)

        independent = case.claims[0].model_copy(
            update={
                "bindings": [
                    binding
                    for binding in case.claims[0].bindings
                    if not isinstance(binding, RelationProposal)
                ],
                "mentions": [
                    mention
                    for mention in case.claims[0].mentions
                    if mention.mention_id != "m-company-b"
                ],
            }
        )
        dependent = case.claims[1].model_copy(
            update={
                "statement": "한빛과 푸른은 공동 개발했다.",
                "bindings": [
                    case.claims[1].bindings[0].model_copy(update={"stance": stance})
                ],
            }
        )
        execution = case.execution.model_copy(
            update={"execution_generation": "blocked-relation-followup"}
        )
        with Session(engine) as session, session.begin():
            task_id = inputs.enqueue_extraction(
                session, int(case.runtime.document.document_id), execution
            ).task_id
        with Session(engine) as session, session.begin():
            lease = tasks.claim_task(session, task_id, "blocked-relation-test")
            assert lease is not None
        with Session(engine) as session, session.begin():
            slot = tasks.reserve_slot(session, lease)
            assert slot is not None
        with Session(engine) as session, session.begin():
            assert (
                tasks.record_terminal(
                    session, slot, tasks.TerminalResult("SUCCESS", datetime.now(UTC))
                )
                == "RUNNING"
            )
        followup = B3Case(engine, task_id, execution, case.runtime, case.claims, lease)

        def propose(payload: ResolutionBatchInput) -> object:
            def decide(item: ResolutionInput) -> dict[str, object]:
                if item.mention_text in existing:
                    return {
                        "decision": "SAME",
                        "node_id": existing[item.mention_text],
                    }
                return {"decision": "NEW", "node_id": None}

            return _batch(payload, decide)

        result = finalize_extraction(
            engine,
            _runner(followup, (independent, dependent)),
            execution,
            case.runtime,
            propose,
        )
        assert result.disposition == result.task_status == "SUCCESS"
        assert result.promotion_batch_id is not None
        assert result.accepted_claims == ("c-multi",)
        assert result.excluded_claims == ("c-relation",)
        assert _count(engine, schema.claim) == 2
        assert _count(engine, schema.relation) == 1
        assert _count(engine, schema.promotion_batch) == 2
    finally:
        engine.dispose()


def test_only_unsupported_new_relation_is_validation_blocked(
    b3_case: B3Case,
) -> None:
    case = b3_case
    dispute = case.claims[1].model_copy(
        update={
            "bindings": [
                case.claims[1].bindings[0].model_copy(update={"stance": "DISPUTE"})
            ]
        }
    )
    result = finalize_extraction(
        case.engine,
        _runner(case, (dispute,)),
        case.execution,
        case.runtime,
        _new,
    )
    assert result.disposition == result.task_status == "VALIDATION_BLOCKED"
    assert result.accepted_claims == ()
    assert result.excluded_claims == ("c-relation",)
    assert _count(case.engine, schema.promotion_batch) == 0


def test_exact_reprocess_is_canonical_noop_without_duplicate_rows(
    b3_case: B3Case,
) -> None:
    case = b3_case
    first = finalize_extraction(
        case.engine,
        _runner(case),
        case.execution,
        case.runtime,
        _new,
    )
    assert first.disposition == "SUCCESS"
    before = {
        table.name: _count(case.engine, table)
        for table in (
            schema.promotion_batch,
            schema.knowledge_item,
            schema.node,
            schema.relation,
            schema.claim,
            schema.observation,
            schema.claim_observation,
            schema.claim_relation,
            schema.claim_attribute_value,
            schema.event_temporal_basis,
            provenance.promotion_canonical_change,
        )
    }
    execution = case.execution.model_copy(
        update={"execution_generation": "reprocess-2"}
    )
    with Session(case.engine) as session, session.begin():
        task_id = inputs.enqueue_extraction(
            session, int(case.runtime.document.document_id), execution
        ).task_id
    with Session(case.engine) as session, session.begin():
        lease = tasks.claim_task(session, task_id, "b3-reprocess")
        assert lease is not None
    with Session(case.engine) as session, session.begin():
        slot = tasks.reserve_slot(session, lease)
        assert slot is not None
    with Session(case.engine) as session, session.begin():
        tasks.record_terminal(
            session, slot, tasks.TerminalResult("SUCCESS", datetime.now(UTC))
        )

    def same(payload: ResolutionBatchInput) -> object:
        def decide(item: ResolutionInput) -> dict[str, object]:
            assert item.candidates
            return {"decision": "SAME", "node_id": item.candidates[0].node_id}

        return _batch(payload, decide)

    rerun = RunnerResult(
        "RUNNING",
        "VERIFIED_RUNTIME",
        lease,
        ExtractionResult(generated=list(case.claims), verified=list(case.claims)),
    )
    second = finalize_extraction(
        case.engine,
        rerun,
        execution,
        case.runtime,
        same,
    )
    assert second.disposition == second.task_status == "SUCCESS"
    assert second.promotion_batch_id is None
    after = {
        table.name: _count(case.engine, table)
        for table in (
            schema.promotion_batch,
            schema.knowledge_item,
            schema.node,
            schema.relation,
            schema.claim,
            schema.observation,
            schema.claim_observation,
            schema.claim_relation,
            schema.claim_attribute_value,
            schema.event_temporal_basis,
            provenance.promotion_canonical_change,
        )
    }
    assert after == before


def test_unresolved_dependency_drops_only_dependent_binding(b3_case: B3Case) -> None:
    case = b3_case

    def propose(payload: ResolutionBatchInput) -> object:
        return _batch(
            payload,
            lambda item: {
                "decision": ("UNRESOLVED" if item.mention_text == "푸른" else "NEW"),
                "node_id": None,
            },
        )

    result = finalize_extraction(
        case.engine,
        _runner(case, (case.claims[0],)),
        case.execution,
        case.runtime,
        propose,
    )
    assert result.disposition == result.task_status == "SUCCESS"
    assert result.accepted_claims == ("c-multi",)
    assert result.excluded_claims == ()
    assert result.promotion_batch_id is not None
    assert _count(case.engine, schema.claim) == 1
    assert _count(case.engine, schema.relation) == 0
    assert _count(case.engine, schema.claim_relation) == 0
    assert _count(case.engine, schema.claim_attribute_value) == 1
    assert _count(case.engine, schema.event_temporal_basis) == 1


def _existing_company(engine: Engine, text: str) -> int:
    with Session(engine) as session, session.begin():
        policy = session.scalar(
            sa.select(schema.lint_policy_version.c.lint_policy_version_id).where(
                schema.lint_policy_version.c.is_active
            )
        )
        batch_id = int(
            session.execute(
                sa.insert(schema.promotion_batch)
                .values(
                    lint_policy_version_id=int(policy),
                    promotion_status="COMMITTED",
                    committed_at=datetime.now(UTC),
                )
                .returning(schema.promotion_batch.c.promotion_batch_id)
            ).scalar_one()
        )
        company_type = int(
            session.scalar(
                sa.select(schema.node_type.c.node_type_id).where(
                    schema.node_type.c.node_type_code == "COMPANY"
                )
            )
        )
        node_id = int(
            session.execute(
                sa.insert(schema.knowledge_item)
                .values(
                    item_kind="NODE",
                    current_state="EVIDENCE_VERIFIED",
                    promotion_batch_id=batch_id,
                )
                .returning(schema.knowledge_item.c.knowledge_item_id)
            ).scalar_one()
        )
        session.execute(
            sa.insert(schema.node).values(node_id=node_id, node_type_id=company_type)
        )
        session.execute(
            sa.insert(schema.node_alias).values(
                node_id=node_id,
                alias_text=text,
                language="ko",
                is_preferred=True,
            )
        )
        return node_id


def test_existing_node_alias_mutation_uses_official_provenance(
    b3_case: B3Case,
) -> None:
    case = b3_case
    existing = _existing_company(case.engine, "한빛")

    def propose(payload: ResolutionBatchInput) -> object:
        def decide(item: ResolutionInput) -> dict[str, object]:
            if item.mention_text == "한빛":
                assert [candidate.node_id for candidate in item.candidates] == [
                    existing
                ]
                return {"decision": "SAME", "node_id": existing}
            return {"decision": "NEW", "node_id": None}

        return _batch(payload, decide)

    result = finalize_extraction(
        case.engine,
        _runner(case, (case.claims[1],)),
        case.execution,
        case.runtime,
        propose,
    )
    assert result.disposition == result.task_status == "SUCCESS"
    assert result.promotion_batch_id is not None
    changes = _provenance_kinds(case.engine, result.promotion_batch_id)
    assert "NODE_ALIAS_EVIDENCE_ADDED" in changes
    assert "CLAIM_OBSERVATION_ADDED" in changes
    assert "CLAIM_RELATION_ADDED" in changes


def test_existing_event_without_extent_fails_closed_and_rolls_back_provenance(
    b3_case: B3Case,
) -> None:
    case = b3_case
    with Session(case.engine) as session, session.begin():
        policy = session.scalar(
            sa.select(schema.lint_policy_version.c.lint_policy_version_id).where(
                schema.lint_policy_version.c.is_active
            )
        )
        batch_id = int(
            session.execute(
                sa.insert(schema.promotion_batch)
                .values(
                    lint_policy_version_id=int(policy),
                    promotion_status="COMMITTED",
                    committed_at=datetime.now(UTC),
                )
                .returning(schema.promotion_batch.c.promotion_batch_id)
            ).scalar_one()
        )
        event_type = int(
            session.scalar(
                sa.select(schema.node_type.c.node_type_id).where(
                    schema.node_type.c.node_type_code == "EVENT"
                )
            )
        )
        event_node_id = int(
            session.execute(
                sa.insert(schema.knowledge_item)
                .values(
                    item_kind="NODE",
                    current_state="EVIDENCE_VERIFIED",
                    promotion_batch_id=batch_id,
                )
                .returning(schema.knowledge_item.c.knowledge_item_id)
            ).scalar_one()
        )
        session.execute(
            sa.insert(schema.node).values(
                node_id=event_node_id, node_type_id=event_type
            )
        )
        session.execute(
            sa.insert(schema.node_alias).values(
                node_id=event_node_id,
                alias_text="한빛 발표회",
                language="ko",
                is_preferred=True,
            )
        )

    before_batches = _count(case.engine, schema.promotion_batch)
    before_items = _count(case.engine, schema.knowledge_item)
    before_alias_evidence = _count(case.engine, schema.node_alias_evidence)
    before_provenance = _count(case.engine, provenance.promotion_canonical_change)

    def propose(payload: ResolutionBatchInput) -> object:
        def decide(item: ResolutionInput) -> dict[str, object]:
            if item.mention_text == "한빛 발표회":
                assert [candidate.node_id for candidate in item.candidates] == [
                    event_node_id
                ]
                return {"decision": "SAME", "node_id": event_node_id}
            return {"decision": "NEW", "node_id": None}

        return _batch(payload, decide)

    result = finalize_extraction(
        case.engine,
        _runner(case, (case.claims[0],)),
        case.execution,
        case.runtime,
        propose,
    )
    assert result.disposition == result.task_status == "FINAL_FAILED"
    assert _count(case.engine, schema.promotion_batch) == before_batches
    assert _count(case.engine, schema.knowledge_item) == before_items
    assert _count(case.engine, schema.event_temporal_extent) == 0
    assert _count(case.engine, schema.node_alias_evidence) == before_alias_evidence
    assert (
        _count(case.engine, provenance.promotion_canonical_change) == before_provenance
    )


def test_conflict_rolls_back_nodes_relation_claim_and_batch() -> None:
    url = sa.engine.make_url(URL)
    engine = sa.create_engine(url)
    _truncate(engine)
    try:
        case = _seed(engine, conflicting_event=True)
        result = finalize_extraction(
            engine,
            _runner(case, (case.claims[0],)),
            case.execution,
            case.runtime,
            _new,
        )
        assert result.disposition == result.task_status == "FINAL_FAILED"
        assert _count(engine, schema.promotion_batch) == 0
        assert _count(engine, schema.knowledge_item) == 0
        assert _count(engine, schema.observation) == 0
        assert _count(engine, provenance.promotion_canonical_change) == 0
    finally:
        engine.dispose()


def _new_reprocess(
    case: B3Case, generation: str, worker: str
) -> tuple[inputs.ExecutionInput, tasks.Lease]:
    execution = case.execution.model_copy(update={"execution_generation": generation})
    with Session(case.engine) as session, session.begin():
        task_id = inputs.enqueue_extraction(
            session, int(case.runtime.document.document_id), execution
        ).task_id
    with Session(case.engine) as session, session.begin():
        lease = tasks.claim_task(session, task_id, worker)
        assert lease is not None
    with Session(case.engine) as session, session.begin():
        slot = tasks.reserve_slot(session, lease)
        assert slot is not None
    with Session(case.engine) as session, session.begin():
        assert (
            tasks.record_terminal(
                session, slot, tasks.TerminalResult("SUCCESS", datetime.now(UTC))
            )
            == "RUNNING"
        )
    return execution, lease


def _same_candidate(payload: ResolutionBatchInput) -> object:
    def decide(item: ResolutionInput) -> dict[str, object]:
        assert item.candidates
        return {"decision": "SAME", "node_id": item.candidates[0].node_id}

    return _batch(payload, decide)


def _paraphrased_relation_claim(case: B3Case) -> ClaimProposal:
    return case.claims[1].model_copy(
        update={
            "candidate_id": "c-relation-paraphrase",
            "statement": "푸른과 한빛은 함께 개발을 진행했다.",
        }
    )


def test_nonexact_claim_creates_new_claim_without_duplicate_model(
    b3_case: B3Case,
) -> None:
    case = b3_case
    first = finalize_extraction(
        case.engine,
        _runner(case),
        case.execution,
        case.runtime,
        _new,
    )
    assert first.disposition == "SUCCESS"
    before_claims = _count(case.engine, schema.claim)
    before_batches = _count(case.engine, schema.promotion_batch)
    execution, lease = _new_reprocess(case, "claim-duplicate", "claim-duplicate")
    claim = _paraphrased_relation_claim(case)
    runner = RunnerResult(
        "RUNNING",
        "VERIFIED_RUNTIME",
        lease,
        ExtractionResult(generated=[claim], verified=[claim]),
    )
    result = finalize_extraction(
        case.engine,
        runner,
        execution,
        case.runtime,
        _same_candidate,
    )
    assert result.disposition == result.task_status == "SUCCESS"
    assert result.promotion_batch_id is not None
    assert result.accepted_claims == ("c-relation-paraphrase",)
    assert _count(case.engine, schema.claim) == before_claims + 1
    assert _count(case.engine, schema.promotion_batch) == before_batches + 1


def test_transient_promotion_rollback_then_retry_has_no_canonical_duplicates(
    b3_case: B3Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = b3_case
    original_commit = provenance.mark_promotion_committed

    class SerializationFailure(Exception):
        sqlstate = "40001"

    def fail_commit(_session: Session, _batch_id: int) -> None:
        raise sa.exc.OperationalError("commit batch", {}, SerializationFailure())

    monkeypatch.setattr(provenance, "mark_promotion_committed", fail_commit)
    first = finalize_extraction(
        case.engine,
        _runner(case),
        case.execution,
        case.runtime,
        _new,
    )
    assert first.disposition == first.task_status == "RETRY_WAIT"
    assert _count(case.engine, schema.promotion_batch) == 0
    assert _count(case.engine, schema.knowledge_item) == 0
    assert _count(case.engine, schema.observation) == 0
    assert _count(case.engine, provenance.promotion_canonical_change) == 0

    monkeypatch.setattr(provenance, "mark_promotion_committed", original_commit)
    with Session(case.engine) as session, session.begin():
        session.execute(
            sa.update(schema.model_task)
            .where(schema.model_task.c.model_task_id == case.task_id)
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        retry_lease = tasks.claim_task(session, case.task_id, "b3-retry")
        assert retry_lease is not None
    with Session(case.engine) as session, session.begin():
        retry_slot = tasks.reserve_slot(session, retry_lease)
        assert retry_slot is not None
    with Session(case.engine) as session, session.begin():
        assert (
            tasks.record_terminal(
                session,
                retry_slot,
                tasks.TerminalResult("SUCCESS", datetime.now(UTC)),
            )
            == "RUNNING"
        )
    retry_runner = RunnerResult(
        "RUNNING",
        "VERIFIED_RUNTIME",
        retry_lease,
        ExtractionResult(generated=list(case.claims), verified=list(case.claims)),
    )
    second = finalize_extraction(
        case.engine,
        retry_runner,
        case.execution,
        case.runtime,
        _new,
    )
    assert second.disposition == second.task_status == "SUCCESS"
    assert _count(case.engine, schema.node) == 3
    assert _count(case.engine, schema.relation) == 1
    assert _count(case.engine, schema.claim) == 2
    assert _count(case.engine, schema.observation) == 2
    assert _count(case.engine, provenance.promotion_canonical_change) > 0


def test_relation_endpoint_change_is_revalidated_before_promotion(
    b3_case: B3Case,
) -> None:
    case = b3_case
    with Session(case.engine) as session, session.begin():
        session.execute(sa.delete(schema.relation_endpoint_rule))
    result = finalize_extraction(
        case.engine,
        _runner(case, (case.claims[1],)),
        case.execution,
        case.runtime,
        _new,
    )
    assert result.disposition == result.task_status == "FINAL_FAILED"
    assert _count(case.engine, schema.promotion_batch) == 0
    assert _count(case.engine, schema.knowledge_item) == 0


def test_allowed_unit_change_is_revalidated_before_promotion(
    b3_case: B3Case,
) -> None:
    case = b3_case
    with Session(case.engine) as session, session.begin():
        revision_id = int(
            session.scalar(
                sa.select(schema.attribute_revision.c.attribute_revision_id).where(
                    schema.attribute_revision.c.is_active
                )
            )
        )
        session.execute(
            sa.delete(schema.attribute_revision_allowed_unit).where(
                schema.attribute_revision_allowed_unit.c.attribute_revision_id
                == revision_id
            )
        )
        session.execute(
            sa.insert(schema.attribute_revision_allowed_unit).values(
                attribute_revision_id=revision_id,
                allowed_value_kind="NUMBER",
                unit_code="TB_PER_S",
            )
        )
    result = finalize_extraction(
        case.engine,
        _runner(case, (case.claims[0],)),
        case.execution,
        case.runtime,
        _new,
    )
    assert result.disposition == result.task_status == "FINAL_FAILED"
    assert _count(case.engine, schema.promotion_batch) == 0
    assert _count(case.engine, schema.knowledge_item) == 0


def test_allowed_unit_is_effective_input(b3_case: B3Case) -> None:
    case = b3_case
    with Session(case.engine) as session, session.begin():
        revision_id = int(
            session.scalar(
                sa.select(schema.attribute_revision.c.attribute_revision_id).where(
                    schema.attribute_revision.c.is_active
                )
            )
        )
        session.execute(
            sa.insert(schema.attribute_revision_allowed_unit).values(
                attribute_revision_id=revision_id,
                allowed_value_kind="NUMBER",
                unit_code="TB_PER_S",
            )
        )
    with Session(case.engine) as session:
        with pytest.raises(inputs.InputChanged, match="EXTRACTION_INPUT_CHANGED"):
            inputs.require_current_input(session, case.task_id, case.execution)


def test_topic_active_identity_is_effective_input_and_final_revalidation() -> None:
    url = sa.engine.make_url(URL)
    engine = sa.create_engine(url)
    _truncate(engine)
    try:
        with Session(engine) as session, session.begin():
            data = reference_data(session)
            _node_type(session, "TOPIC")
            topic = ensure_topic_reference(
                session,
                topic_code="INVESTMENT",
                canonical_display_name="투자",
                is_active=True,
            )
            document = _document(data.document_id)
            session.execute(
                sa.update(schema.source_document)
                .where(schema.source_document.c.source_document_id == data.document_id)
                .values(
                    normalized_body=document.body,
                    body_hash=sha256(document.body.encode()).digest(),
                )
            )
            runtime = RuntimeInput(
                document,
                Ontology(
                    node_types=("COMPANY", "TOPIC"),
                    topics=("투자",),
                    relations=(),
                    attributes=(),
                ),
                limits(),
            )
            execution = data.execution.model_copy(
                update={
                    "runtime_settings": {
                        "extraction_runner": runtime.identity_settings()
                    }
                }
            )
            task_id = inputs.enqueue_extraction(
                session, data.document_id, execution
            ).task_id
        with Session(engine) as session, session.begin():
            lease = tasks.claim_task(session, task_id, "topic-change")
            assert lease is not None
        with Session(engine) as session, session.begin():
            set_topic_reference_active(session, topic.node_id, is_active=False)
        zero = RunnerResult(
            "RUNNING", "ZERO_RESULT", lease, ExtractionResult(generated=[], verified=[])
        )
        result = finalize_extraction(engine, zero, execution, runtime, _new)
        assert result.disposition == result.task_status == "FINAL_FAILED"
        assert _count(engine, schema.promotion_batch) == 0
    finally:
        engine.dispose()
