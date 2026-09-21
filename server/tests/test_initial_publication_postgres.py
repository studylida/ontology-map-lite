"""Issue #215 initial publication integration against migrated PostgreSQL."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import followup_generation as followup_product
from ontology_map import insight_generation as insight_product
from ontology_map import node_context_generation as context_product
from ontology_map.db import followup_generation as followup_db
from ontology_map.db import initial_publication as publication
from ontology_map.db import insight_generation as insight_db
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.session import get_engine
from ontology_map.followup_generation_contracts import (
    FollowupQuestionCandidate,
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


@pytest.fixture(autouse=True)
def _isolate_database() -> None:
    if not DATABASE_URL:
        yield
        return
    names = ", ".join(f'"{table.name}"' for table in s.metadata.sorted_tables)
    with _engine().begin() as connection:
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
    yield
    with _engine().begin() as connection:
        connection.execute(sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


def _fixture_nodes() -> dict[str, int]:
    _created, node_ids = load_hbf_fixture()
    return node_ids


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


def _commit(session: Session, batch_id: int) -> None:
    provenance.mark_promotion_committed(session, batch_id)


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


def _new_observation(session: Session) -> int:
    marker = uuid4().hex
    body = f"#215 restart evidence {marker}"
    group_id = session.scalar(
        s.evidence_group.insert().returning(s.evidence_group.c.evidence_group_id)
    )
    assert group_id is not None
    document_id = session.scalar(
        s.source_document.insert()
        .values(
            evidence_group_id=group_id,
            source_key=f"publication215:{marker}",
            version_no=1,
            canonical_url=f"https://example.com/publication215/{marker}",
            publisher_name="issue 215 regression",
            title="issue 215 regression evidence",
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


def _new_node(session: Session, batch_id: int) -> int:
    node_type_id = session.scalar(
        sa.select(s.node_type.c.node_type_id)
        .where(s.node_type.c.node_type_code == "COMPANY")
        .limit(1)
    )
    assert node_type_id is not None
    node_id = session.scalar(
        s.knowledge_item.insert()
        .values(
            item_kind="NODE",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(s.knowledge_item.c.knowledge_item_id)
    )
    assert node_id is not None
    session.execute(s.node.insert().values(node_id=node_id, node_type_id=node_type_id))
    session.execute(
        s.node_alias.insert().values(
            node_id=node_id,
            alias_text=f"publication215-new-{uuid4().hex}",
            language="ko",
            is_preferred=True,
        )
    )
    return int(node_id)


def _new_relation(
    session: Session,
    batch_id: int,
    source_node_id: int,
    target_node_id: int,
) -> int:
    revision_id = session.scalar(
        sa.select(s.relation_type_revision.c.relation_type_revision_id).limit(1)
    )
    assert revision_id is not None
    relation_id = session.scalar(
        s.knowledge_item.insert()
        .values(
            item_kind="RELATION",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(s.knowledge_item.c.knowledge_item_id)
    )
    assert relation_id is not None
    source_node_id, target_node_id = sorted((source_node_id, target_node_id))
    session.execute(
        s.relation.insert().values(
            relation_id=relation_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type_revision_id=revision_id,
            relation_identity_key=sha256(uuid4().bytes).digest(),
        )
    )
    return int(relation_id)


def _new_string_attribute_revision(session: Session, target_node_id: int) -> int:
    attribute_id = session.scalar(
        s.attribute.insert()
        .values(attribute_code=f"PUBLICATION215_{uuid4().hex}")
        .returning(s.attribute.c.attribute_id)
    )
    assert attribute_id is not None
    target_type_id = session.scalar(
        sa.select(s.node.c.node_type_id).where(s.node.c.node_id == target_node_id)
    )
    assert target_type_id is not None
    revision_id = session.scalar(
        s.attribute_revision.insert()
        .values(
            attribute_id=attribute_id,
            version_no=1,
            display_name="회귀 속성",
            target_node_type_id=target_type_id,
            allowed_value_kind="STRING",
            is_active=False,
        )
        .returning(s.attribute_revision.c.attribute_revision_id)
    )
    assert revision_id is not None
    return int(revision_id)


def _new_multitarget_claim(
    session: Session,
    batch_id: int,
    *,
    relation_id: int,
    attribute_target: int,
    event_target: int,
    observation_id: int,
) -> int:
    claim_id = session.scalar(
        s.knowledge_item.insert()
        .values(
            item_kind="CLAIM",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(s.knowledge_item.c.knowledge_item_id)
    )
    assert claim_id is not None
    session.execute(
        s.claim.insert().values(
            claim_id=claim_id,
            statement_text="여러 직접 의미 대상을 갖는 #215 회귀 Claim",
            language="ko",
            modality="FACT",
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    session.execute(
        s.claim_observation.insert().values(
            claim_id=claim_id,
            observation_id=observation_id,
        )
    )
    session.execute(
        s.claim_relation.insert().values(
            claim_id=claim_id,
            relation_id=relation_id,
            stance="SUPPORT",
        )
    )
    revision_id = _new_string_attribute_revision(session, attribute_target)
    session.execute(
        s.claim_attribute_value.insert().values(
            claim_id=claim_id,
            target_node_id=attribute_target,
            attribute_revision_id=revision_id,
            value_kind="STRING",
            string_value="회귀 값",
            date_from_precision="UNKNOWN",
            date_to_precision="UNKNOWN",
        )
    )
    session.execute(
        s.event_temporal_basis.insert().values(
            event_node_id=event_target,
            claim_id=claim_id,
        )
    )
    return int(claim_id)


def _historical_relation_claim(
    session: Session,
    relation_id: int,
) -> int:
    batch_id = _batch(session)
    claim_id = session.scalar(
        s.knowledge_item.insert()
        .values(
            item_kind="CLAIM",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        .returning(s.knowledge_item.c.knowledge_item_id)
    )
    assert claim_id is not None
    observation_id = _new_observation(session)
    session.execute(
        s.claim.insert().values(
            claim_id=claim_id,
            statement_text="직접 relation 하나만 가진 과거 Claim",
            language="ko",
            modality="FACT",
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    session.execute(
        s.claim_observation.insert().values(
            claim_id=claim_id,
            observation_id=observation_id,
        )
    )
    session.execute(
        s.claim_relation.insert().values(
            claim_id=claim_id,
            relation_id=relation_id,
            stance="SUPPORT",
        )
    )
    _commit(session, batch_id)
    session.execute(
        s.promotion_batch.update()
        .where(s.promotion_batch.c.promotion_batch_id == batch_id)
        .values(
            publication_status="READY",
            ready_at=sa.func.current_timestamp(),
        )
    )
    return int(claim_id)


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
    now = datetime.now(UTC)
    task_id = session.scalar(
        s.model_task.insert()
        .values(
            task_kind=task_kind,
            input_hash=sha256(b"input:" + nonce).digest(),
            output_schema_definition_id=contract_id,
            model_version=model_version,
            prompt_version=prompt_version,
            cache_key=sha256(b"cache:" + nonce).digest(),
            status="RUNNING",
            attempt_count=0,
            lease_owner="issue-215-regression",
            lease_expires_at=now + timedelta(minutes=10),
        )
        .returning(s.model_task.c.model_task_id)
    )
    assert task_id is not None
    return int(task_id)


def _mark_context_task_running(session: Session, task_id: int) -> None:
    session.execute(
        s.model_task.update()
        .where(s.model_task.c.model_task_id == task_id)
        .values(
            status="RUNNING",
            lease_owner="issue-215-regression",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )


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
        duplicate = publication.ensure_node_context_task(session, prepared)
        assert duplicate.model_task_id == task.model_task_id

    with Session(engine) as session, session.begin():
        _mark_context_task_running(session, task.model_task_id)
        result = publication.apply_node_context(
            session,
            model_task_id=task.model_task_id,
            prepared=prepared,
            proposal=NodeContextProposal(
                context_text="HBF의 공개 지식 맥락을 설명하는 짧은 문장"
            ),
            finished_at=datetime.now(UTC),
        )
        assert result.status == "SUCCESS"
        assert result.node_context_id is not None
        context_id = int(result.node_context_id)
    return document_id, context_id, task.model_task_id


def _complete_derived_empty(
    engine: sa.Engine,
    *,
    batch_id: int,
    node_id: int,
    contracts: dict[str, int],
) -> int:
    with Session(engine) as session:
        work = publication.prepare_derived_work(
            session,
            promotion_batch_id=batch_id,
            node_id=node_id,
        )

    with Session(engine) as session, session.begin():
        task_id = _make_running_task(
            session,
            task_kind="FOLLOWUP_QUESTIONS",
            contract_id=contracts["FOLLOWUP_QUESTIONS"],
            model_version=followup_product.MODEL_VERSION,
            prompt_version=followup_product.PROMPT_VERSION,
        )
        result = followup_db.apply_followup(
            session,
            model_task_id=task_id,
            prepared=work.followup_bundle,
            proposal=FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(questions=()),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
            finished_at=datetime.now(UTC),
        )
        assert result.status == "SUCCESS"
        assert result.stored_count == 0

    with Session(engine) as session, session.begin():
        insight_task_id = _make_running_task(
            session,
            task_kind="NODE_INSIGHT",
            contract_id=contracts["NODE_INSIGHT"],
            model_version=insight_product.MODEL_VERSION,
            prompt_version=insight_product.PROMPT_VERSION,
        )
        result = insight_db.apply_insight_bundle(
            session,
            model_task_id=insight_task_id,
            prepared=work.insight_bundle,
            proposal=InsightBundleProposal(
                recent_90_days=InsightWindowProposal(report=None),
                recent_1_year=InsightWindowProposal(report=None),
            ),
            finished_at=datetime.now(UTC),
        )
        assert result.status == "SUCCESS"
        assert result.report_ids == (None, None)
    return insight_task_id


def _alias_change_batch(session: Session, node_id: int) -> int:
    batch_id = _batch(session)
    alias_id = session.scalar(
        s.node_alias.insert()
        .values(
            node_id=node_id,
            alias_text=f"HBF-publication-{uuid4().hex}",
            language="en",
            is_preferred=False,
        )
        .returning(s.node_alias.c.node_alias_id)
    )
    assert alias_id is not None
    provenance.record_node_alias_changed(session, batch_id, int(alias_id))
    _commit(session, batch_id)
    return batch_id


def test_db_only_projection_covers_new_items_and_six_provenance_kinds() -> None:
    nodes = _fixture_nodes()
    engine = _engine()
    with Session(engine) as session, session.begin():
        batch_id = _batch(session)
        new_node_id = _new_node(session, batch_id)
        new_relation_id = _new_relation(
            session,
            batch_id,
            new_node_id,
            nodes["hbf"],
        )
        observation_id = _new_observation(session)
        _new_multitarget_claim(
            session,
            batch_id,
            relation_id=new_relation_id,
            attribute_target=nodes["sandisk"],
            event_target=nodes["fms_2026"],
            observation_id=observation_id,
        )

        alias_id = session.scalar(
            s.node_alias.insert()
            .values(
                node_id=nodes["hbf"],
                alias_text=f"HBF-provenance-{uuid4().hex}",
                language="en",
                is_preferred=False,
            )
            .returning(s.node_alias.c.node_alias_id)
        )
        assert alias_id is not None
        provenance.record_node_alias_changed(session, batch_id, int(alias_id))
        provenance.add_node_alias_evidence(
            session,
            batch_id,
            int(alias_id),
            observation_id,
        )

        sk_hbf = _relation_between(session, nodes["sk_hynix"], nodes["hbf"])
        existing_claim = _claim_for_relation(session, sk_hbf)
        provenance.add_claim_observation(
            session,
            batch_id,
            existing_claim,
            observation_id,
        )
        hbf_ucie = _relation_between(session, nodes["hbf"], nodes["ucie"])
        provenance.add_claim_relation(
            session,
            batch_id,
            existing_claim,
            hbf_ucie,
            "SUPPORT",
        )
        revision_id = _new_string_attribute_revision(session, nodes["sandisk"])
        provenance.add_claim_attribute_value(
            session,
            batch_id,
            claim_id=existing_claim,
            target_node_id=nodes["sandisk"],
            attribute_revision_id=revision_id,
            value_kind="STRING",
            string_value="기존 Claim 값",
        )
        provenance.add_event_temporal_basis(
            session,
            batch_id,
            nodes["fms_2026"],
            existing_claim,
        )
        _commit(session, batch_id)

    with Session(engine) as session:
        affected = set(publication.affected_node_ids(session, batch_id))
    assert affected == {*nodes.values(), new_node_id}

    with Session(engine) as session, session.begin():
        first = publication.start_initial_publication(session, batch_id)
    with Session(engine) as session, session.begin():
        second = publication.start_initial_publication(session, batch_id)
    assert first.started is True
    assert second.started is False
    assert second.affected_node_ids == first.affected_node_ids


def test_evidence_only_projection_is_direct_and_start_rollback_is_atomic() -> None:
    nodes = _fixture_nodes()
    engine = _engine()
    with Session(engine) as session, session.begin():
        relation_id = _relation_between(session, nodes["sk_hynix"], nodes["hbf"])
        claim_id = _historical_relation_claim(session, relation_id)
        batch_id = _batch(session)
        provenance.add_claim_observation(
            session,
            batch_id,
            claim_id,
            _new_observation(session),
        )
        _commit(session, batch_id)

    with Session(engine) as session:
        assert set(publication.affected_node_ids(session, batch_id)) == {
            nodes["sk_hynix"],
            nodes["hbf"],
        }
        assert nodes["ucie"] not in publication.affected_node_ids(session, batch_id)

    with pytest.raises(RuntimeError, match="force rollback"):
        with Session(engine) as session, session.begin():
            publication.start_initial_publication(session, batch_id)
            raise RuntimeError("force rollback")

    with Session(engine) as session:
        status = session.scalar(
            sa.select(s.promotion_batch.c.publication_status).where(
                s.promotion_batch.c.promotion_batch_id == batch_id
            )
        )
        count = session.scalar(
            sa.select(sa.func.count())
            .select_from(s.publication_affected_node)
            .where(s.publication_affected_node.c.promotion_batch_id == batch_id)
        )
        assert status == "NOT_STARTED"
        assert count == 0

    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_id)


def test_concurrent_start_has_one_membership_generation() -> None:
    _fixture_nodes()
    engine = _engine()
    with Session(engine) as session, session.begin():
        batch_id = _batch(session)
        node_id = _new_node(session, batch_id)
        _commit(session, batch_id)

    barrier = Barrier(2)

    def start_once() -> bool:
        with Session(engine) as session, session.begin():
            barrier.wait()
            return publication.start_initial_publication(session, batch_id).started

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _value: start_once(), range(2)))
    assert sorted(results) == [False, True]

    with Session(engine) as session:
        rows = session.execute(
            sa.select(s.publication_affected_node.c.node_id).where(
                s.publication_affected_node.c.promotion_batch_id == batch_id
            )
        ).all()
    assert rows == [(node_id,)]


def test_stale_node_context_becomes_validation_blocked() -> None:
    nodes = _fixture_nodes()
    engine = _engine()
    with Session(engine) as session, session.begin():
        _activate_generation_contracts(session)
        batch_id = _alias_change_batch(session, nodes["hbf"])
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_id)
        publication.ensure_search_document(
            session,
            promotion_batch_id=batch_id,
            node_id=nodes["hbf"],
        )
        prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=batch_id,
            node_id=nodes["hbf"],
        )
        task = publication.ensure_node_context_task(session, prepared)

    with Session(engine) as session, session.begin():
        other_batch = _batch(session)
        alias_id = session.scalar(
            s.node_alias.insert()
            .values(
                node_id=nodes["hbf"],
                alias_text=f"HBF-stale-{uuid4().hex}",
                language="en",
                is_preferred=False,
            )
            .returning(s.node_alias.c.node_alias_id)
        )
        assert alias_id is not None
        provenance.record_node_alias_changed(session, other_batch, int(alias_id))
        _commit(session, other_batch)
        session.execute(
            s.promotion_batch.update()
            .where(s.promotion_batch.c.promotion_batch_id == other_batch)
            .values(
                publication_status="READY",
                ready_at=sa.func.current_timestamp(),
            )
        )

    with Session(engine) as session, session.begin():
        _mark_context_task_running(session, task.model_task_id)
        result = publication.apply_node_context(
            session,
            model_task_id=task.model_task_id,
            prepared=prepared,
            proposal=NodeContextProposal(context_text="stale context"),
            finished_at=datetime.now(UTC),
        )
        assert result.status == "VALIDATION_BLOCKED"
        assert result.node_context_id is None

    with Session(engine) as session:
        task_status = session.scalar(
            sa.select(s.model_task.c.status).where(
                s.model_task.c.model_task_id == task.model_task_id
            )
        )
        pointer = session.scalar(
            sa.select(s.publication_affected_node.c.node_context_id).where(
                s.publication_affected_node.c.promotion_batch_id == batch_id,
                s.publication_affected_node.c.node_id == nodes["hbf"],
            )
        )
    assert task_status == "VALIDATION_BLOCKED"
    assert pointer is None


def test_ready_uses_real_empty_finalizers_and_new_context_per_generation() -> None:
    nodes = _fixture_nodes()
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
        first_batch = _alias_change_batch(session, nodes["hbf"])
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, first_batch)
    first_document, first_context, first_task = _finish_context(
        engine,
        batch_id=first_batch,
        node_id=nodes["hbf"],
    )
    insight_task_id = _complete_derived_empty(
        engine,
        batch_id=first_batch,
        node_id=nodes["hbf"],
        contracts=contracts,
    )

    with Session(engine) as session:
        readiness = publication.publication_readiness(session, first_batch)
        assert readiness.ready, readiness.reasons
    with Session(engine) as session, session.begin():
        assert publication.mark_publication_ready(session, first_batch) is True
        assert publication.mark_publication_ready(session, first_batch) is False

    with Session(engine) as session, session.begin():
        relation_id = _relation_between(session, nodes["sk_hynix"], nodes["hbf"])
        claim_id = _claim_for_relation(session, relation_id)
        second_batch = _batch(session)
        provenance.add_claim_observation(
            session,
            second_batch,
            claim_id,
            _new_observation(session),
        )
        _commit(session, second_batch)
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, second_batch)
        second_document = publication.ensure_search_document(
            session,
            promotion_batch_id=second_batch,
            node_id=nodes["hbf"],
        )
        prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=second_batch,
            node_id=nodes["hbf"],
        )
        second_task = publication.ensure_node_context_task(session, prepared)

    assert second_document == first_document
    assert second_task.model_task_id != first_task

    with Session(engine) as session:
        current = session.execute(
            sa.select(
                s.promotion_batch.c.publication_status,
                s.promotion_batch.c.ready_at,
            ).where(s.promotion_batch.c.promotion_batch_id == first_batch)
        ).one()
        still_ready = set(
            int(value)
            for value in session.scalars(
                sa.select(s.promotion_batch.c.promotion_batch_id).where(
                    s.promotion_batch.c.publication_status == "READY"
                )
            )
        )
        selected = session.execute(
            sa.select(
                s.publication_affected_node.c.node_search_document_id,
                s.publication_affected_node.c.node_context_id,
                s.publication_affected_node.c.node_insight_model_task_id,
            ).where(
                s.publication_affected_node.c.promotion_batch_id == first_batch,
                s.publication_affected_node.c.node_id == nodes["hbf"],
            )
        ).one()

    assert current.publication_status == "READY"
    assert current.ready_at is not None
    assert previous_ready <= still_ready
    assert selected.node_search_document_id == first_document
    assert selected.node_context_id == first_context
    assert selected.node_insight_model_task_id == insight_task_id


def test_validation_blocked_and_final_failed_are_not_normal_empty() -> None:
    nodes = _fixture_nodes()
    engine = _engine()
    with Session(engine) as session, session.begin():
        contracts = _activate_generation_contracts(session)
        batch_id = _alias_change_batch(session, nodes["hbf"])
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_id)
    _finish_context(engine, batch_id=batch_id, node_id=nodes["hbf"])

    with Session(engine) as session:
        work = publication.prepare_derived_work(
            session,
            promotion_batch_id=batch_id,
            node_id=nodes["hbf"],
        )

    with Session(engine) as session, session.begin():
        blocked_task = _make_running_task(
            session,
            task_kind="FOLLOWUP_QUESTIONS",
            contract_id=contracts["FOLLOWUP_QUESTIONS"],
            model_version=followup_product.MODEL_VERSION,
            prompt_version=followup_product.PROMPT_VERSION,
        )
        blocked = followup_db.apply_followup(
            session,
            model_task_id=blocked_task,
            prepared=work.followup_bundle,
            proposal=FollowupQuestionsProposal(
                recent_90_days=FollowupWindowProposal(
                    questions=(
                        FollowupQuestionCandidate(
                            display_order=1,
                            question_text="근거 없는 질문인가요?",
                            answer_text="근거가 없어 저장할 수 없습니다.",
                            claims=(),
                        ),
                    )
                ),
                recent_1_year=FollowupWindowProposal(questions=()),
            ),
            finished_at=datetime.now(UTC),
        )
        assert blocked.status == "VALIDATION_BLOCKED"

    with Session(engine) as session, session.begin():
        final_failed = _make_running_task(
            session,
            task_kind="FOLLOWUP_QUESTIONS",
            contract_id=contracts["FOLLOWUP_QUESTIONS"],
            model_version=followup_product.MODEL_VERSION,
            prompt_version=followup_product.PROMPT_VERSION,
        )
        session.execute(
            s.model_task.update()
            .where(s.model_task.c.model_task_id == final_failed)
            .values(
                status="FINAL_FAILED",
                finished_at=datetime.now(UTC),
                lease_owner=None,
                lease_expires_at=None,
            )
        )

    with Session(engine) as session:
        readiness = publication.publication_readiness(session, batch_id)
    assert readiness.ready is False
    assert any("NODE_INSIGHT" in reason for reason in readiness.reasons)
    assert any("FOLLOWUP" in reason for reason in readiness.reasons)


def test_cutover_guard_blocks_legacy_not_started_until_explicit_start() -> None:
    _fixture_nodes()
    engine = _engine()
    with Session(engine) as session, session.begin():
        batch_id = _batch(session)
        node_id = _new_node(session, batch_id)
        _commit(session, batch_id)

    with Session(engine) as session:
        with pytest.raises(RuntimeError, match=r"COMMITTED\+NOT_STARTED"):
            publication.assert_initial_publication_enable_safe(session)

    with Session(engine) as session, session.begin():
        started = publication.start_initial_publication(session, batch_id)
        assert started.affected_node_ids == (node_id,)

    with Session(engine) as session:
        publication.assert_initial_publication_enable_safe(session)
