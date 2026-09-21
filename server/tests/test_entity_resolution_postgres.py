"""Opt-in tests against the REAL migrated PostgreSQL schema, never SQLite.

Set ONTOLOGY_MAP_ER_TEST_DATABASE_URL to an isolated, already-migrated,
loopback PostgreSQL database whose name ends in _er128_test. All data added
here is synthetic and rolled back. No schema/fixture/published data is reset.
"""

import os
from hashlib import sha256
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import entity_resolution as service
from ontology_map.db import entity_resolution as db
from ontology_map.db import promotion_provenance as provenance
from ontology_map.entity_resolution_contracts import (
    EntityMention,
    ExternalIdentifier,
    SourceRange,
)

DATABASE_URL = os.environ.get("ONTOLOGY_MAP_ER_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="isolated migrated PostgreSQL URL was not supplied",
)


def execute(session, sql, **values):
    return session.execute(sa.text(sql), values)


def returning(session, sql, **values):
    return int(execute(session, sql, **values).scalar_one())


@pytest.fixture
def database():
    url = sa.engine.make_url(DATABASE_URL)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    assert url.database and url.database.endswith("_er128_test")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection) as session:
                seed(session)
                yield session
        finally:
            outer.rollback()
    engine.dispose()


def seed(session):
    for code in ("COMPANY", "PERSON", "TECHNOLOGY", "EVENT", "TOPIC"):
        execute(
            session,
            """
            INSERT INTO node_type
                (node_type_code, display_name, creation_rule, is_active)
            VALUES (:code, :code, '공개 원문 근거와 대표 alias가 필요하다.', true)
            ON CONFLICT (node_type_code) DO NOTHING
        """,
            code=code,
        )
    # Only this isolated rollback scope is affected by the active-type setup.
    execute(
        session,
        """
        UPDATE node_type SET is_active = true
        WHERE node_type_code IN ('COMPANY', 'PERSON', 'TECHNOLOGY', 'EVENT', 'TOPIC')
    """,
    )
    policy_id = returning(
        session,
        """
        INSERT INTO lint_policy_version (version_no, validator_version, is_active)
        SELECT coalesce(max(version_no), 0) + 1, 'er128-test-v1', false
        FROM lint_policy_version RETURNING lint_policy_version_id
    """,
    )
    session.info["er_policy"] = policy_id
    session.info["er_batch"] = batch(session, committed=True)


def batch(session, *, committed=False):
    if committed:
        return returning(
            session,
            """
            INSERT INTO promotion_batch
                (lint_policy_version_id, promotion_status, committed_at)
            VALUES (:policy, 'COMMITTED', CURRENT_TIMESTAMP)
            RETURNING promotion_batch_id
        """,
            policy=session.info["er_policy"],
        )
    return returning(
        session,
        """
        INSERT INTO promotion_batch (lint_policy_version_id)
        VALUES (:policy) RETURNING promotion_batch_id
    """,
        policy=session.info["er_policy"],
    )


def node(session, name, code="COMPANY", state="EVIDENCE_VERIFIED"):
    type_id = execute(
        session,
        """
        SELECT node_type_id FROM node_type WHERE node_type_code = :code
    """,
        code=code,
    ).scalar_one()
    node_id = db._insert_node(session, session.info["er_batch"], type_id)
    execute(
        session,
        """
        UPDATE knowledge_item SET current_state = :state
        WHERE knowledge_item_id = :node_id
    """,
        state=state,
        node_id=node_id,
    )
    execute(
        session,
        """
        INSERT INTO node_alias (node_id, alias_text, language, is_preferred)
        VALUES (:node_id, :name, 'ko', true)
    """,
        node_id=node_id,
        name=name,
    )
    return node_id


def source_mention(session, name, **changes):
    body = f"{name}는 기존회사와 협력한다."
    group_id = returning(
        session,
        """
        INSERT INTO evidence_group DEFAULT VALUES RETURNING evidence_group_id
    """,
    )
    document_id = returning(
        session,
        """
        INSERT INTO source_document (
            evidence_group_id, source_key, version_no, canonical_url,
            publisher_name, title, original_language, normalized_body, body_hash,
            published_precision, modified_precision, last_checked_at, last_check_status
        ) VALUES (
            :group_id, :key, 1, 'https://example.invalid/er128',
            '합성 시험 발행처', '합성 시험 문서', 'ko', :body, :body_hash,
            'UNKNOWN', 'UNKNOWN', CURRENT_TIMESTAMP, 'SUCCESS'
        ) RETURNING source_document_id
    """,
        group_id=group_id,
        key=f"er128:{uuid4()}",
        body=body,
        body_hash=sha256(body.encode()).digest(),
    )
    return EntityMention(
        mention_id="m1",
        text=name,
        node_type="COMPANY",
        source_ranges=(
            SourceRange(
                source_document_id=document_id, start_char=0, end_char=len(body)
            ),
        ),
        **changes,
    )


def propose(decision, node_id=None):
    return lambda messages: {"decision": decision, "node_id": node_id}


def relation_claim(session, promotion_id, binding, other_id, statement):
    # Synthetic, explicitly supported COMPANY <-> COMPANY knowledge. This
    # exercises the ER transaction boundary, not extraction/ontology quality.
    execute(
        session,
        """
        INSERT INTO relation_type (relation_code) VALUES ('COLLABORATES_WITH')
        ON CONFLICT (relation_code) DO NOTHING
    """,
    )
    relation_type = execute(
        session,
        """
        SELECT relation_type_id FROM relation_type
        WHERE relation_code = 'COLLABORATES_WITH'
    """,
    ).scalar_one()
    revision = execute(
        session,
        """
        SELECT relation_type_revision_id FROM relation_type_revision
        WHERE relation_type_id = :type_id AND is_active
    """,
        type_id=relation_type,
    ).scalar_one_or_none()
    if revision is None:
        revision = returning(
            session,
            """
            INSERT INTO relation_type_revision
                (relation_type_id, version_no, display_name,
                 directionality, is_active)
            SELECT :type_id, coalesce(max(version_no), 0) + 1,
                   '협력', 'SYMMETRIC', true
            FROM relation_type_revision WHERE relation_type_id = :type_id
            RETURNING relation_type_revision_id
        """,
            type_id=relation_type,
        )
    type_id = execute(
        session,
        """
        SELECT node_type_id FROM node WHERE node_id = :node_id
    """,
        node_id=other_id,
    ).scalar_one()
    execute(
        session,
        """
        INSERT INTO relation_endpoint_rule
            (relation_type_revision_id, source_node_type_id, target_node_type_id)
        SELECT :revision, :type_id, :type_id WHERE NOT EXISTS (
            SELECT 1 FROM relation_endpoint_rule
            WHERE relation_type_revision_id = :revision
              AND source_node_type_id = :type_id AND target_node_type_id = :type_id
        )
    """,
        revision=revision,
        type_id=type_id,
    )
    relation_id = returning(
        session,
        """
        INSERT INTO knowledge_item (item_kind, current_state, promotion_batch_id)
        VALUES ('RELATION', 'EVIDENCE_VERIFIED', :batch_id)
        RETURNING knowledge_item_id
    """,
        batch_id=promotion_id,
    )
    low, high = sorted((binding.node_id, other_id))
    execute(
        session,
        """
        INSERT INTO relation
            (relation_id, source_node_id, target_node_id,
             relation_type_revision_id, relation_identity_key)
        VALUES (:relation_id, :low, :high, :revision, :key)
    """,
        relation_id=relation_id,
        low=low,
        high=high,
        revision=revision,
        key=sha256(f"er128-test:{revision}:{low}:{high}".encode()).digest(),
    )
    claim_id = returning(
        session,
        """
        INSERT INTO knowledge_item (item_kind, current_state, promotion_batch_id)
        VALUES ('CLAIM', 'EVIDENCE_VERIFIED', :batch_id) RETURNING knowledge_item_id
    """,
        batch_id=promotion_id,
    )
    execute(
        session,
        """
        INSERT INTO claim (claim_id, statement_text, language, modality,
                           asserted_from_precision, asserted_to_precision)
        VALUES (:claim_id, :statement, 'ko', 'FACT', 'UNKNOWN', 'UNKNOWN')
    """,
        claim_id=claim_id,
        statement=statement,
    )
    for observation_id in binding.observation_ids:
        execute(
            session,
            """
            INSERT INTO claim_observation (claim_id, observation_id)
            VALUES (:claim_id, :observation_id)
        """,
            claim_id=claim_id,
            observation_id=observation_id,
        )
    execute(
        session,
        """
        INSERT INTO claim_relation (claim_id, relation_id, stance)
        VALUES (:claim_id, :relation_id, 'SUPPORT')
    """,
        claim_id=claim_id,
        relation_id=relation_id,
    )


def test_lookup_includes_committed_not_ready_and_on_hold_nodes(database):
    name = f"Acme{uuid4().hex}"
    live = node(database, name)
    held = node(database, name, state="ON_HOLD")
    target = source_mention(database, name)
    found = db.name_candidates(database, target)
    assert {item.candidate.node_id for item in found.nodes} == {live, held}
    assert {item.candidate.node_id: item.usable for item in found.nodes} == {
        live: True,
        held: False,
    }
    assert not found.truncated


def test_exact_alias_precedes_same_type_fts_and_cap_counts_canonical_nodes(database):
    name = f"Acme{uuid4().hex}"
    exact = node(database, name)
    for index in range(5):
        node(database, f"{name} Labs {index}")
    # Nonexact PERSON FTS matches must not fill COMPANY candidate slots.
    node(database, f"{name} Person", code="PERSON")
    found = db.name_candidates(database, source_mention(database, name))
    assert found.truncated and len(found.nodes) == 5
    assert found.nodes[0].candidate.node_id == exact
    assert all(item.candidate.node_type == "COMPANY" for item in found.nodes)


def test_multiple_redirect_aliases_consume_only_one_candidate_slot(database):
    name = f"Old{uuid4().hex}"
    canonical = node(database, f"New{uuid4().hex}")
    for _ in range(6):
        original = node(database, name)
        execute(
            database,
            """
            INSERT INTO node_merge
                (source_node_id, canonical_node_id, merge_reason, merged_at)
            VALUES (:original, :canonical, '합성 중복 정정 이력', CURRENT_TIMESTAMP)
        """,
            original=original,
            canonical=canonical,
        )
    found = db.name_candidates(database, source_mention(database, name))
    assert not found.truncated and len(found.nodes) == 1
    assert found.nodes[0].candidate.node_id == canonical
    assert name in found.nodes[0].candidate.aliases


def test_external_identifier_is_code_only_same_or_type_conflict(database):
    name = f"Exact{uuid4().hex}"
    canonical = node(database, name)
    value = uuid4().hex
    execute(
        database,
        """
        INSERT INTO external_identifier (node_id, identifier_system, identifier_value)
        VALUES (:node_id, 'WIKIDATA', :value)
    """,
        node_id=canonical,
        value=value,
    )
    target = source_mention(
        database,
        name,
        external_identifiers=(
            ExternalIdentifier(identifier_system="WIKIDATA", identifier_value=value),
        ),
    )

    def forbidden(messages):
        raise AssertionError("exact identifier must not call Agent")

    result = service.resolve_mention(database, target, forbidden)
    assert (result.decision, result.node_id) == ("SAME", canonical)
    changed = target.model_copy(update={"node_type": "PERSON"})
    conflict = service.resolve_mention(database, changed, forbidden)
    assert conflict.decision == "UNRESOLVED"


def test_new_node_alias_and_evidenced_claim_share_pending_transaction(database):
    name = f"New{uuid4().hex}"
    other = node(database, "기존회사")
    target = source_mention(database, name)
    result = service.resolve_mention(database, target, propose("NEW"))
    assert result.decision == "NEW"
    promotion_id = batch(database)
    with service.resolved_nodes_for_promotion(
        database, promotion_id, (result,), frozenset({"m1"})
    ) as bindings:
        saved_id = bindings["m1"].node_id
        relation_claim(
            database,
            promotion_id,
            bindings["m1"],
            other,
            f"{name}는 기존회사와 협력한다.",
        )
    changes = provenance.changes_for_batch(database, promotion_id)
    assert [change.change_kind for change in changes] == [
        "NODE_ALIAS_CHANGED",
        "NODE_ALIAS_EVIDENCE_ADDED",
    ]
    assert (
        execute(
            database,
            """
        SELECT promotion_batch_id FROM knowledge_item
        WHERE knowledge_item_id = :node_id
    """,
            node_id=saved_id,
        ).scalar_one()
        == promotion_id
    )
    assert (
        execute(
            database,
            """
        SELECT promotion_status FROM promotion_batch
        WHERE promotion_batch_id = :batch_id
    """,
            batch_id=promotion_id,
        ).scalar_one()
        == "PENDING"
    )  # Owner still decides commit.


def test_existing_alias_reuse_records_only_new_evidence(database):
    name = f"Existing{uuid4().hex}"
    canonical = node(database, name)
    target = source_mention(database, name)
    result = service.resolve_mention(database, target, propose("SAME", canonical))
    assert (result.decision, result.node_id) == ("SAME", canonical)
    promotion_id = batch(database)

    with service.resolved_nodes_for_promotion(
        database, promotion_id, (result,), frozenset({"m1"})
    ):
        pass
    first = provenance.changes_for_batch(database, promotion_id)
    assert [change.change_kind for change in first] == ["NODE_ALIAS_EVIDENCE_ADDED"]

    with service.resolved_nodes_for_promotion(
        database, promotion_id, (result,), frozenset({"m1"})
    ):
        pass
    assert provenance.changes_for_batch(database, promotion_id) == first


@pytest.mark.parametrize("failure", ["orphan", "writer"])
def test_failed_promotion_rolls_back_new_rows_and_keeps_prior_knowledge(
    database, failure
):
    name = f"Rollback{uuid4().hex}"
    prior = node(database, "기존회사")
    result = service.resolve_mention(
        database, source_mention(database, name), propose("NEW")
    )
    before = {
        table: execute(database, f"SELECT count(*) FROM {table}").scalar_one()
        for table in (
            "knowledge_item",
            "node",
            "node_alias",
            "observation",
            "node_alias_evidence",
        )
    }
    expected = ValueError if failure == "orphan" else RuntimeError
    with pytest.raises(expected):
        # A real PostgreSQL savepoint isolates this promotion from seed data;
        # the outer rollback fixture also prevents durable test data.
        with database.begin_nested():
            promotion_id = batch(database)
            with service.resolved_nodes_for_promotion(
                database, promotion_id, (result,), frozenset({"m1"})
            ) as bindings:
                if failure == "writer":
                    relation_claim(
                        database,
                        promotion_id,
                        bindings["m1"],
                        prior,
                        f"{name}는 기존회사와 협력한다.",
                    )
                    raise RuntimeError("synthetic consumer write failure")
    for table, count in before.items():
        assert execute(database, f"SELECT count(*) FROM {table}").scalar_one() == count
    assert (
        execute(
            database, "SELECT node_id FROM node WHERE node_id = :node_id", node_id=prior
        ).scalar_one()
        == prior
    )


def test_same_alias_evidence_is_idempotent_and_preferred_name_is_preserved(database):
    name = f"Repeat{uuid4().hex}"
    selected = node(database, f"{name} Labs")
    target = source_mention(database, name)
    for _ in range(2):
        result = service.resolve_mention(database, target, propose("SAME", selected))
        with service.resolved_nodes_for_promotion(
            database, batch(database), (result,), frozenset({"m1"})
        ):
            pass
    assert (
        execute(
            database,
            """
        SELECT count(*) FROM node_alias WHERE node_id = :node_id AND alias_text = :name
    """,
            node_id=selected,
            name=name,
        ).scalar_one()
        == 1
    )
    assert (
        execute(
            database,
            """
        SELECT count(*) FROM node_alias_evidence e JOIN node_alias a
        ON a.node_alias_id = e.node_alias_id
        WHERE a.node_id = :node_id AND a.alias_text = :name
    """,
            node_id=selected,
            name=name,
        ).scalar_one()
        == 1
    )
    assert (
        execute(
            database,
            """
        SELECT alias_text FROM node_alias WHERE node_id = :node_id AND is_preferred
    """,
            node_id=selected,
        ).scalar_one()
        == f"{name} Labs"
    )
