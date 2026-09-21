"""PostgreSQL regression for #215 publication-visible alias selection."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import initial_publication as publication
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema as s
from ontology_map.db.fixture import load_hbf_fixture
from ontology_map.db.session import get_engine

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
    body = f"#215 alias-visibility evidence {marker}"
    group_id = session.scalar(
        s.evidence_group.insert().returning(s.evidence_group.c.evidence_group_id)
    )
    assert group_id is not None
    document_id = session.scalar(
        s.source_document.insert()
        .values(
            evidence_group_id=group_id,
            source_key=f"publication215-alias-visibility:{marker}",
            version_no=1,
            canonical_url=f"https://example.com/publication215/alias/{marker}",
            publisher_name="issue 215 alias visibility regression",
            title="issue 215 alias visibility evidence",
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


def _add_alias(
    session: Session,
    *,
    batch_id: int,
    node_id: int,
    alias_text: str,
) -> int:
    alias_id = session.scalar(
        s.node_alias.insert()
        .values(
            node_id=node_id,
            alias_text=alias_text,
            language="en",
            is_preferred=False,
        )
        .returning(s.node_alias.c.node_alias_id)
    )
    assert alias_id is not None
    provenance.record_node_alias_changed(session, batch_id, int(alias_id))
    return int(alias_id)


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


def test_unready_alias_does_not_leak_into_other_generation_or_context() -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()
    hidden_alias = f"HBF-unready-{uuid4().hex}"

    with Session(engine) as session:
        historical_preferred = session.scalar(
            sa.select(s.node_alias.c.alias_text).where(
                s.node_alias.c.node_id == node_id,
                s.node_alias.c.is_preferred,
            )
        )
        assert historical_preferred is not None

    with Session(engine) as session, session.begin():
        batch_a = _batch(session)
        _add_alias(
            session,
            batch_id=batch_a,
            node_id=node_id,
            alias_text=hidden_alias,
        )
        provenance.mark_promotion_committed(session, batch_a)
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_a)

    with Session(engine) as session, session.begin():
        batch_b = _evidence_only_batch(session, node_id)
    with Session(engine) as session, session.begin():
        publication.start_initial_publication(session, batch_b)
        document_id = publication.ensure_search_document(
            session,
            promotion_batch_id=batch_b,
            node_id=node_id,
        )
        prepared = publication.prepare_node_context(
            session,
            promotion_batch_id=batch_b,
            node_id=node_id,
        )

    with Session(engine) as session:
        identity_text = session.scalar(
            sa.select(s.node_search_document.c.identity_text).where(
                s.node_search_document.c.node_search_document_id == document_id
            )
        )
        assert identity_text is not None
        assert hidden_alias not in identity_text
        assert str(historical_preferred) in identity_text

    assert hidden_alias not in prepared.agent_input.identity_text
    assert hidden_alias not in prepared.agent_input.knowledge_text
    assert prepared.agent_input.preferred_alias == str(historical_preferred)


def test_current_generation_alias_is_publication_visible() -> None:
    _created, nodes = load_hbf_fixture()
    node_id = nodes["hbf"]
    engine = _engine()
    current_alias = f"HBF-current-{uuid4().hex}"

    with Session(engine) as session, session.begin():
        batch_id = _batch(session)
        _add_alias(
            session,
            batch_id=batch_id,
            node_id=node_id,
            alias_text=current_alias,
        )
        provenance.mark_promotion_committed(session, batch_id)

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

    with Session(engine) as session:
        identity_text = session.scalar(
            sa.select(s.node_search_document.c.identity_text).where(
                s.node_search_document.c.node_search_document_id == document_id
            )
        )
    assert identity_text is not None
    assert current_alias in identity_text
    assert current_alias in prepared.agent_input.identity_text
