"""#110 product policy on a separate, synthetic PostgreSQL 18.6 database."""

import os
from datetime import UTC, datetime
from hashlib import sha256

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import extraction_promotion, product_lint, schema

URL = os.environ.get("ONTOLOGY_MAP_LINT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="isolated lint database not supplied")


@pytest.fixture
def engine():
    assert URL is not None
    url = sa.engine.make_url(URL)
    assert url.host in {"127.0.0.1", "localhost", "::1"}
    assert url.database and url.database.endswith("_lint_test")
    engine = sa.create_engine(url, isolation_level="REPEATABLE READ")
    names = ", ".join(f'"{table.name}"' for table in schema.metadata.sorted_tables)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
            )
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
            )
        engine.dispose()


def _insert(session: Session, table: sa.Table, **values):
    return int(
        session.execute(
            table.insert()
            .values(**values)
            .returning(next(iter(table.primary_key.columns)))
        ).scalar_one()
    )


def _seed_graph(session: Session) -> tuple[int, int, int, int, int]:
    policy_id = product_lint.ensure_product_policy(session)
    batch_id = _insert(
        session, schema.promotion_batch, lint_policy_version_id=policy_id
    )
    session.execute(
        schema.promotion_batch.update()
        .where(schema.promotion_batch.c.promotion_batch_id == batch_id)
        .values(promotion_status="COMMITTED", committed_at=datetime.now(UTC))
    )
    company = _insert(
        session,
        schema.node_type,
        node_type_code="COMPANY",
        display_name="회사",
        creation_rule="synthetic",
        is_active=True,
    )
    node_ids = []
    for _ in range(2):
        node_id = _insert(
            session,
            schema.knowledge_item,
            item_kind="NODE",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=batch_id,
        )
        session.execute(
            schema.node.insert().values(node_id=node_id, node_type_id=company)
        )
        node_ids.append(node_id)
    relation_type = _insert(
        session, schema.relation_type, relation_code="TEST_RELATION"
    )
    revision = _insert(
        session,
        schema.relation_type_revision,
        relation_type_id=relation_type,
        version_no=1,
        display_name="시험",
        directionality="DIRECTED",
        is_active=True,
    )
    session.execute(
        schema.relation_endpoint_rule.insert().values(
            relation_type_revision_id=revision,
            source_node_type_id=company,
            target_node_type_id=company,
        )
    )
    relation_id = _insert(
        session,
        schema.knowledge_item,
        item_kind="RELATION",
        current_state="EVIDENCE_VERIFIED",
        promotion_batch_id=batch_id,
    )
    session.execute(
        schema.relation.insert().values(
            relation_id=relation_id,
            source_node_id=node_ids[0],
            target_node_id=node_ids[1],
            relation_type_revision_id=revision,
            relation_identity_key=sha256(b"test-relation").digest(),
        )
    )
    claim_id = _insert(
        session,
        schema.knowledge_item,
        item_kind="CLAIM",
        current_state="EVIDENCE_VERIFIED",
        promotion_batch_id=batch_id,
    )
    session.execute(
        schema.claim.insert().values(
            claim_id=claim_id,
            statement_text="두 회사가 협력했다.",
            language="ko",
            modality="FACT",
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    session.execute(
        schema.claim_relation.insert().values(
            claim_id=claim_id, relation_id=relation_id, stance="SUPPORT"
        )
    )
    group_id = _insert(session, schema.evidence_group)
    body = "두 회사가 협력했다."
    document_id = _insert(
        session,
        schema.source_document,
        evidence_group_id=group_id,
        source_key="synthetic",
        version_no=1,
        canonical_url="https://example.invalid/source",
        publisher_name="시험",
        title="시험",
        original_language="ko",
        normalized_body=body,
        body_hash=sha256(body.encode()).digest(),
        published_precision="UNKNOWN",
        modified_precision="UNKNOWN",
        last_checked_at=datetime.now(UTC),
        last_check_status="SUCCESS",
    )
    observation_id = _insert(
        session,
        schema.observation,
        source_document_id=document_id,
        start_char=0,
        end_char=len(body),
        quote_text=body,
        quote_hash=sha256(body.encode()).digest(),
        observed_at=datetime.now(UTC),
    )
    session.execute(
        schema.claim_observation.insert().values(
            claim_id=claim_id, observation_id=observation_id
        )
    )
    return node_ids[0], relation_id, claim_id, observation_id, revision


def test_policy_bootstrap_is_idempotent_and_rejects_definition_drift(engine):
    with Session(engine) as session, session.begin():
        policy_id = product_lint.ensure_product_policy(session)
        assert product_lint.ensure_product_policy(session) == policy_id
        assert product_lint.require_product_policy(session)[0] == policy_id
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(schema.lint_rule))
            == 6
        )
        session.execute(
            schema.lint_rule.update()
            .where(schema.lint_rule.c.rule_code == "RELATION_SUPPORTED")
            .values(description="drift")
        )
        with pytest.raises(ValueError, match="PRODUCT_LINT_RULE_MISMATCH"):
            product_lint.require_product_policy(session)


def test_persisted_findings_block_and_clear_only_after_successful_scan(engine):
    with Session(engine) as session, session.begin():
        node_id, relation_id, claim_id, observation_id, revision = _seed_graph(session)
        assert product_lint.evaluate_persisted_graph(session) == set()
        product_lint.run_full_graph(session)
        assert extraction_promotion._usable_knowledge(session, relation_id, "RELATION")

        session.execute(
            schema.claim_relation.update()
            .where(schema.claim_relation.c.claim_id == claim_id)
            .values(stance="DISPUTE")
        )
        assert not extraction_promotion.relation_has_supported_claim(
            session, relation_id
        )
        product_lint.run_full_graph(session)
        assert not extraction_promotion._usable_knowledge(
            session, relation_id, "RELATION"
        )
        assert (
            "RELATION_SUPPORTED",
            relation_id,
        ) in product_lint.evaluate_persisted_graph(session)

        session.execute(
            schema.claim_relation.update()
            .where(schema.claim_relation.c.claim_id == claim_id)
            .values(stance="SUPPORT")
        )
        session.execute(
            schema.observation.update()
            .where(schema.observation.c.observation_id == observation_id)
            .values(quote_hash=sha256(b"wrong").digest())
        )
        problems = product_lint.evaluate_persisted_graph(session)
        assert ("OBSERVATION_SOURCE_INTEGRITY", claim_id) in problems
        assert ("EVIDENCE_TRACE_COMPLETE", node_id) in problems
        assert ("RELATION_SUPPORTED", relation_id) in problems
        product_lint.run_full_graph(session)
        assert not extraction_promotion._usable_knowledge(session, claim_id, "CLAIM")

        session.execute(
            schema.observation.update()
            .where(schema.observation.c.observation_id == observation_id)
            .values(quote_hash=sha256("두 회사가 협력했다.".encode()).digest())
        )
        product_lint.run_full_graph(session)
        assert extraction_promotion._usable_knowledge(session, relation_id, "RELATION")
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(schema.lint_finding)
                .where(schema.lint_finding.c.resolved_at.is_(None))
            )
            == 0
        )

        extra = _insert(
            session,
            schema.knowledge_item,
            item_kind="CLAIM",
            current_state="EVIDENCE_VERIFIED",
            promotion_batch_id=session.scalar(
                sa.select(schema.knowledge_item.c.promotion_batch_id).where(
                    schema.knowledge_item.c.knowledge_item_id == claim_id
                )
            ),
        )
        session.execute(
            schema.claim.insert().values(
                claim_id=extra,
                statement_text="대상 없음",
                language="ko",
                modality="FACT",
                asserted_from_precision="UNKNOWN",
                asserted_to_precision="UNKNOWN",
            )
        )
        assert {
            ("EVIDENCE_TRACE_COMPLETE", extra),
            ("CLAIM_SEMANTIC_TARGET", extra),
        } <= product_lint.evaluate_persisted_graph(session)

        session.execute(
            schema.relation_endpoint_rule.delete().where(
                schema.relation_endpoint_rule.c.relation_type_revision_id == revision
            )
        )
        assert (
            "STORED_REVISION_VALID",
            relation_id,
        ) in product_lint.evaluate_persisted_graph(session)

        person_type = _insert(
            session,
            schema.node_type,
            node_type_code="PERSON",
            display_name="사람",
            creation_rule="synthetic",
            is_active=False,
        )
        attribute = _insert(session, schema.attribute, attribute_code="TEST_ATTRIBUTE")
        attribute_revision = _insert(
            session,
            schema.attribute_revision,
            attribute_id=attribute,
            version_no=1,
            display_name="시험 속성",
            target_node_type_id=person_type,
            allowed_value_kind="STRING",
            is_active=False,
        )
        session.execute(
            schema.claim_attribute_value.insert().values(
                claim_id=claim_id,
                target_node_id=node_id,
                attribute_revision_id=attribute_revision,
                value_kind="STRING",
                string_value="근거 있음",
                date_from_precision="UNKNOWN",
                date_to_precision="UNKNOWN",
            )
        )
        assert (
            "STORED_REVISION_VALID",
            claim_id,
        ) in product_lint.evaluate_persisted_graph(session)
