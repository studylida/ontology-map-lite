"""Deterministic #24 search-document preparation for issue #215."""

from hashlib import sha256
from struct import pack
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ontology_map.db import schema as s
from ontology_map.db.initial_publication_contracts import (
    PublicationStateError,
    SearchDocumentPreparationError,
    SearchDocumentSnapshot,
)
from ontology_map.db.publication_grounding import (
    ReferenceTopicIdentity,
    ReferenceTopicIntegrityError,
    reference_topic_identities,
)

SEARCH_DOCUMENT_GENERATOR_VERSION = "node-search-document-215-v1"


def _ids_statement(sql: str, name: str = "ids") -> sa.TextClause:
    return sa.text(sql).bindparams(sa.bindparam(name, expanding=True))


def _frame_text(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return pack(">Q", len(encoded)) + encoded


def _search_document_hash(
    node_id: int,
    identity_text: str,
    knowledge_text: str,
    basis_ids: tuple[int, ...],
) -> bytes:
    payload = (
        b"NSD1"
        + pack(">q", node_id)
        + _frame_text(identity_text)
        + _frame_text(knowledge_text)
        + pack(">Q", len(basis_ids))
        + b"".join(pack(">q", value) for value in basis_ids)
    )
    return sha256(payload).digest()


def _usable_item_ids(
    session: Session,
    ids: set[int],
    *,
    current_batch_id: int,
) -> set[int]:
    if not ids:
        return set()
    values = session.execute(
        _ids_statement(
            """
            SELECT k.knowledge_item_id
            FROM knowledge_item k
            JOIN promotion_batch pb
              ON pb.promotion_batch_id = k.promotion_batch_id
            WHERE k.knowledge_item_id IN :ids
              AND k.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
              AND pb.promotion_status = 'COMMITTED'
              AND (
                k.promotion_batch_id = :current_batch_id
                OR pb.publication_status = 'READY'
              )
              AND NOT EXISTS (
                SELECT 1
                FROM lint_finding f
                JOIN lint_policy_rule r
                  ON r.lint_policy_rule_id = f.lint_policy_rule_id
                WHERE f.knowledge_item_id = k.knowledge_item_id
                  AND f.resolved_at IS NULL
                  AND r.severity = 'BLOCKING'
              )
            """
        ),
        {"ids": sorted(ids), "current_batch_id": current_batch_id},
    ).scalars()
    return {int(value) for value in values}


def _publication_visible_alias_rows(
    session: Session,
    node_ids: list[int],
    *,
    current_batch_id: int,
    preferred_only: bool = False,
) -> list[dict[str, Any]]:
    """Return aliases visible to one publication generation.

    Historical aliases have no NODE_ALIAS_CHANGED provenance and stay visible.
    Once that provenance exists, every recorded alias change must belong either
    to the current generation or to a previously READY publication. This keeps
    another NOT_STARTED/PREPARING/FAILED promotion from leaking canonical alias
    state through search or NODE_CONTEXT preparation.
    """
    if not node_ids:
        return []
    rows = (
        session.execute(
            _ids_statement(
                """
                SELECT a.node_alias_id, a.node_id, a.alias_text,
                       a.language, a.is_preferred
                FROM node_alias a
                WHERE a.node_id IN :ids
                  AND (:preferred_only = false OR a.is_preferred)
                  AND NOT EXISTS (
                    SELECT 1
                    FROM promotion_canonical_change change
                    JOIN promotion_batch change_batch
                      ON change_batch.promotion_batch_id = change.promotion_batch_id
                    WHERE change.change_kind = 'NODE_ALIAS_CHANGED'
                      AND change.node_alias_id = a.node_alias_id
                      AND NOT (
                        change.promotion_batch_id = :current_batch_id
                        OR (
                          change_batch.promotion_status = 'COMMITTED'
                          AND change_batch.publication_status = 'READY'
                        )
                      )
                  )
                ORDER BY a.node_id, a.is_preferred DESC,
                         a.alias_text, a.language, a.node_alias_id
                """
            ),
            {
                "ids": sorted(set(node_ids)),
                "current_batch_id": current_batch_id,
                "preferred_only": preferred_only,
            },
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def publication_visible_preferred_alias(
    session: Session,
    *,
    promotion_batch_id: int,
    node_id: int,
) -> str:
    """Return the single preferred alias visible to this publication generation."""
    rows = _publication_visible_alias_rows(
        session,
        [node_id],
        current_batch_id=promotion_batch_id,
        preferred_only=True,
    )
    if len(rows) != 1:
        raise SearchDocumentPreparationError(
            "publication search document requires exactly one visible preferred alias"
        )
    return str(rows[0]["alias_text"])


def _supported_topic_relation_ids(
    session: Session,
    relation_ids: set[int],
    *,
    current_batch_id: int,
) -> set[int]:
    if not relation_ids:
        return set()
    support_rows = (
        session.execute(
            _ids_statement(
                """
                SELECT DISTINCT cr.relation_id, cr.claim_id
                FROM claim_relation cr
                WHERE cr.relation_id IN :ids AND cr.stance = 'SUPPORT'
                  AND EXISTS (
                      SELECT 1 FROM claim_observation co
                      WHERE co.claim_id = cr.claim_id
                  )
                """
            ),
            {"ids": sorted(relation_ids)},
        )
        .mappings()
        .all()
    )
    usable_claim_ids = _usable_item_ids(
        session,
        {int(row["claim_id"]) for row in support_rows},
        current_batch_id=current_batch_id,
    )
    return {
        int(row["relation_id"])
        for row in support_rows
        if int(row["claim_id"]) in usable_claim_ids
    }


def _public_relation_endpoint(
    row: RowMapping, usable: set[int], reference_ids: set[int]
) -> bool:
    source_id = int(row["source_node_id"])
    target_id = int(row["target_node_id"])
    if (
        int(row["relation_id"]) not in usable
        or source_id not in usable | reference_ids
        or target_id not in usable | reference_ids
    ):
        return False
    if source_id in reference_ids or target_id in reference_ids:
        return row["relation_code"] == "HAS_TOPIC" and target_id in reference_ids
    return True


def _evidence_backed_topic_relations(
    session: Session,
    rows: list[RowMapping],
    reference_ids: set[int],
    *,
    current_batch_id: int,
) -> list[RowMapping]:
    topic_relation_ids = {
        int(row["relation_id"])
        for row in rows
        if row["relation_code"] == "HAS_TOPIC"
        and int(row["target_node_id"]) in reference_ids
    }
    supported = _supported_topic_relation_ids(
        session, topic_relation_ids, current_batch_id=current_batch_id
    )
    return [
        row
        for row in rows
        if int(row["relation_id"]) not in topic_relation_ids
        or int(row["relation_id"]) in supported
    ]


def _validated_reference_topics(
    session: Session, node_ids: set[int]
) -> dict[int, ReferenceTopicIdentity]:
    try:
        return reference_topic_identities(session, node_ids)
    except ReferenceTopicIntegrityError as error:
        raise SearchDocumentPreparationError(str(error)) from error


def build_search_document_snapshot(
    session: Session,
    *,
    promotion_batch_id: int,
    node_id: int,
) -> SearchDocumentSnapshot:
    """Build identity, knowledge text and exact knowledge-item lineage."""
    node_row = (
        session.execute(
            sa.text(
                """
                SELECT n.node_id, nt.display_name AS node_type_display_name
                FROM node n
                JOIN node_type nt ON nt.node_type_id = n.node_type_id
                WHERE n.node_id = :node_id
                """
            ),
            {"node_id": node_id},
        )
        .mappings()
        .one_or_none()
    )
    if node_row is None:
        raise SearchDocumentPreparationError("affected node does not exist")

    relation_rows = (
        session.execute(
            sa.text(
                """
                SELECT r.relation_id, r.source_node_id, r.target_node_id,
                       rev.display_name AS relation_name,
                       rt.relation_code
                FROM relation r
                JOIN relation_type_revision rev
                  ON rev.relation_type_revision_id = r.relation_type_revision_id
                JOIN relation_type rt ON rt.relation_type_id = rev.relation_type_id
                WHERE r.source_node_id = :node_id OR r.target_node_id = :node_id
                ORDER BY r.relation_id
                """
            ),
            {"node_id": node_id},
        )
        .mappings()
        .all()
    )
    relation_ids = {int(row["relation_id"]) for row in relation_rows}
    neighbor_ids = {
        int(row["target_node_id"])
        if int(row["source_node_id"]) == node_id
        else int(row["source_node_id"])
        for row in relation_rows
    }
    reference_topics = _validated_reference_topics(session, neighbor_ids)

    usable = _usable_item_ids(
        session,
        {node_id, *relation_ids, *neighbor_ids},
        current_batch_id=promotion_batch_id,
    )
    if node_id not in usable:
        raise SearchDocumentPreparationError(
            "affected node is not publication-usable in this generation"
        )

    reference_ids = set(reference_topics)
    candidate_relations = [
        row
        for row in relation_rows
        if _public_relation_endpoint(row, usable, reference_ids)
    ]
    selected_relations = _evidence_backed_topic_relations(
        session, candidate_relations, reference_ids, current_batch_id=promotion_batch_id
    )
    selected_relation_ids = {int(row["relation_id"]) for row in selected_relations}
    selected_neighbor_ids = {
        int(row["target_node_id"])
        if int(row["source_node_id"]) == node_id
        else int(row["source_node_id"])
        for row in selected_relations
    }
    selected_basis_neighbor_ids = selected_neighbor_ids & usable

    claim_rows = (
        session.execute(
            _ids_statement(
                """
                SELECT DISTINCT c.claim_id, c.statement_text
                FROM claim c
                WHERE (
                    EXISTS (
                        SELECT 1
                        FROM claim_relation cr
                        WHERE cr.claim_id = c.claim_id
                          AND cr.relation_id IN :relation_ids
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM claim_attribute_value v
                        WHERE v.claim_id = c.claim_id
                          AND v.target_node_id = :node_id
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM event_temporal_basis e
                        WHERE e.claim_id = c.claim_id
                          AND e.event_node_id = :node_id
                    )
                )
                  AND EXISTS (
                    SELECT 1
                    FROM claim_observation co
                    WHERE co.claim_id = c.claim_id
                  )
                ORDER BY c.claim_id
                """,
                name="relation_ids",
            ),
            {
                "relation_ids": sorted(selected_relation_ids),
                "node_id": node_id,
            },
        )
        .mappings()
        .all()
    )
    claim_ids = {int(row["claim_id"]) for row in claim_rows}
    usable_claim_ids = _usable_item_ids(
        session,
        claim_ids,
        current_batch_id=promotion_batch_id,
    )
    selected_claims = [
        row for row in claim_rows if int(row["claim_id"]) in usable_claim_ids
    ]

    alias_rows = _publication_visible_alias_rows(
        session,
        [node_id],
        current_batch_id=promotion_batch_id,
    )
    preferred = [str(row["alias_text"]) for row in alias_rows if row["is_preferred"]]
    if len(preferred) != 1:
        raise SearchDocumentPreparationError(
            "publication search document requires exactly one visible preferred alias"
        )

    aliases: list[str] = []
    seen_aliases: set[str] = set()
    for alias_row in alias_rows:
        alias = str(alias_row["alias_text"])
        if alias not in seen_aliases:
            aliases.append(alias)
            seen_aliases.add(alias)
    if not aliases:
        raise SearchDocumentPreparationError("node has no publication-visible alias")

    relation_node_ids = {
        value
        for row in selected_relations
        for value in (int(row["source_node_id"]), int(row["target_node_id"]))
    }
    normal_name_ids = sorted(relation_node_ids - reference_topics.keys())
    name_rows = _publication_visible_alias_rows(
        session,
        normal_name_ids or [node_id],
        current_batch_id=promotion_batch_id,
        preferred_only=True,
    )
    names = {int(row["node_id"]): str(row["alias_text"]) for row in name_rows}
    names.update(
        {
            reference.node_id: reference.canonical_display_name
            for reference in reference_topics.values()
            if reference.node_id in relation_node_ids
        }
    )
    if relation_node_ids - names.keys():
        raise SearchDocumentPreparationError(
            "direct relation endpoint lacks a publication-visible identity"
        )

    identity_text = "\n".join(aliases)
    knowledge_lines = [f"유형: {node_row['node_type_display_name']}"]
    for relation_row in selected_relations:
        source_id = int(relation_row["source_node_id"])
        target_id = int(relation_row["target_node_id"])
        relation_name = str(relation_row["relation_name"])
        knowledge_lines.append(
            f"관계: {names[source_id]} · {relation_name} · {names[target_id]}"
        )
    for claim_row in selected_claims:
        knowledge_lines.append(f"주장: {claim_row['statement_text']}")
    knowledge_text = "\n".join(knowledge_lines)

    basis_ids = tuple(
        sorted(
            {
                node_id,
                *selected_relation_ids,
                *selected_basis_neighbor_ids,
                *(int(row["claim_id"]) for row in selected_claims),
            }
        )
    )
    return SearchDocumentSnapshot(
        node_id=node_id,
        identity_text=identity_text,
        knowledge_text=knowledge_text,
        basis_ids=basis_ids,
        input_hash=_search_document_hash(
            node_id,
            identity_text,
            knowledge_text,
            basis_ids,
        ),
    )


def _lock_publication_node(
    session: Session,
    promotion_batch_id: int,
    node_id: int,
) -> dict[str, Any]:
    row = (
        session.execute(
            sa.text(
                """
                SELECT pan.promotion_batch_id, pan.node_id,
                       pan.node_search_document_id, pan.node_context_id,
                       pan.node_insight_model_task_id,
                       pb.promotion_status, pb.publication_status,
                       pb.committed_at
                FROM publication_affected_node pan
                JOIN promotion_batch pb
                  ON pb.promotion_batch_id = pan.promotion_batch_id
                WHERE pan.promotion_batch_id = :batch_id
                  AND pan.node_id = :node_id
                FOR UPDATE OF pan, pb
                """
            ),
            {"batch_id": promotion_batch_id, "node_id": node_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise PublicationStateError("publication node membership does not exist")
    if (
        row["promotion_status"] != "COMMITTED"
        or row["publication_status"] != "PREPARING"
    ):
        raise PublicationStateError("publication node is not in PREPARING generation")
    return dict(row)


def ensure_search_document(
    session: Session,
    *,
    promotion_batch_id: int,
    node_id: int,
    generator_version: str = SEARCH_DOCUMENT_GENERATOR_VERSION,
) -> int:
    """Create/reuse the #24 deterministic artifact and pin this generation."""
    if not session.in_transaction():
        raise PublicationStateError("caller-owned transaction is required")

    publication = _lock_publication_node(session, promotion_batch_id, node_id)
    snapshot = build_search_document_snapshot(
        session,
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
    )
    inserted = session.execute(
        sa.text(
            """
            INSERT INTO node_search_document (
                node_id, identity_text, knowledge_text, input_hash,
                generator_version
            ) VALUES (
                :node_id, :identity_text, :knowledge_text, :input_hash,
                :generator_version
            )
            ON CONFLICT (node_id, input_hash, generator_version) DO NOTHING
            RETURNING node_search_document_id
            """
        ),
        {
            "node_id": node_id,
            "identity_text": snapshot.identity_text,
            "knowledge_text": snapshot.knowledge_text,
            "input_hash": snapshot.input_hash,
            "generator_version": generator_version,
        },
    ).scalar_one_or_none()

    if inserted is None:
        existing = (
            session.execute(
                sa.text(
                    """
                    SELECT node_search_document_id, identity_text, knowledge_text
                    FROM node_search_document
                    WHERE node_id = :node_id
                      AND input_hash = :input_hash
                      AND generator_version = :generator_version
                    """
                ),
                {
                    "node_id": node_id,
                    "input_hash": snapshot.input_hash,
                    "generator_version": generator_version,
                },
            )
            .mappings()
            .one()
        )
        if (
            str(existing["identity_text"]) != snapshot.identity_text
            or str(existing["knowledge_text"]) != snapshot.knowledge_text
        ):
            raise SearchDocumentPreparationError(
                "deterministic search document identity collision"
            )
        document_id = int(existing["node_search_document_id"])
        existing_basis = tuple(
            int(value)
            for value in session.execute(
                sa.text(
                    """
                    SELECT knowledge_item_id
                    FROM search_document_basis
                    WHERE node_search_document_id = :document_id
                    ORDER BY knowledge_item_id
                    """
                ),
                {"document_id": document_id},
            ).scalars()
        )
        if existing_basis != snapshot.basis_ids:
            raise SearchDocumentPreparationError(
                "reused search document has inconsistent basis lineage"
            )
    else:
        document_id = int(inserted)
        if snapshot.basis_ids:
            session.execute(
                s.search_document_basis.insert(),
                [
                    {
                        "node_search_document_id": document_id,
                        "knowledge_item_id": knowledge_item_id,
                    }
                    for knowledge_item_id in snapshot.basis_ids
                ],
            )

    selected = publication["node_search_document_id"]
    if selected is None:
        updated = session.scalar(
            s.publication_affected_node.update()
            .where(
                s.publication_affected_node.c.promotion_batch_id == promotion_batch_id,
                s.publication_affected_node.c.node_id == node_id,
                s.publication_affected_node.c.node_search_document_id.is_(None),
            )
            .values(node_search_document_id=document_id)
            .returning(s.publication_affected_node.c.node_id)
        )
        if updated is None:
            raise PublicationStateError("search-document pointer changed concurrently")
    elif int(selected) != document_id:
        raise SearchDocumentPreparationError(
            "PREPARING generation is pinned to a stale search document"
        )
    return document_id


__all__ = [
    "SEARCH_DOCUMENT_GENERATOR_VERSION",
    "build_search_document_snapshot",
    "ensure_search_document",
    "publication_visible_preferred_alias",
]
