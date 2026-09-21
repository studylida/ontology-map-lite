"""Stored-identity queries and transaction-local writes for issue #128.

No publication/search-document filter, model call, hidden commit, schema
change, or node_merge write belongs here.
"""

from collections.abc import Sequence
from hashlib import sha256
from unicodedata import normalize

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import promotion_provenance as provenance
from ontology_map.entity_resolution_contracts import (
    CANDIDATE_LIMIT,
    CandidateSet,
    EntityMention,
    ExternalIdentifier,
    NodeCandidate,
    NodeRecord,
    SourceContext,
    VerifiedContext,
)

# matched(node_id, priority, score) is supplied by each concrete query below.
# Resolve before LIMIT, so multiple aliases/redirects cannot consume five slots.
_RESOLVE_MATCHES = """
, walk(origin, current_id, priority, score, path, cycle) AS (
    SELECT node_id, node_id, priority, score, ARRAY[node_id], false
    FROM matched
    UNION ALL
    SELECT w.origin, m.canonical_node_id, w.priority, w.score,
           w.path || m.canonical_node_id, m.canonical_node_id = ANY(w.path)
    FROM walk w
    JOIN node_merge m ON m.source_node_id = w.current_id
                    AND m.reversed_at IS NULL
    WHERE NOT w.cycle
), terminal AS (
    SELECT w.current_id AS node_id, w.priority, w.score,
           (w.cycle OR n.node_id IS NULL) AS invalid
    FROM walk w
    LEFT JOIN node n ON n.node_id = w.current_id
    WHERE w.cycle OR n.node_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM node_merge m
        WHERE m.source_node_id = w.current_id AND m.reversed_at IS NULL
    )
), deduplicated AS (
    SELECT node_id, min(priority) AS priority,
           max(score) AS score, bool_or(invalid) AS invalid
    FROM terminal GROUP BY node_id
)
SELECT node_id, invalid FROM deduplicated
ORDER BY invalid DESC, priority ASC, score DESC, node_id ASC
"""

# A stored redirect is read, never created. Identity evidence stays on its
# original node; the family query exposes it under the canonical identity.
_FAMILY = """
WITH RECURSIVE family(canonical_id, member_id, path) AS (
    SELECT n.node_id, n.node_id, ARRAY[n.node_id]
    FROM node n WHERE n.node_id IN :ids
    UNION ALL
    SELECT f.canonical_id, m.source_node_id, f.path || m.source_node_id
    FROM family f JOIN node_merge m ON m.canonical_node_id = f.member_id
                                  AND m.reversed_at IS NULL
    WHERE NOT m.source_node_id = ANY(f.path)
)
"""


def _ids_statement(sql: str) -> sa.TextClause:
    return sa.text(sql).bindparams(sa.bindparam("ids", expanding=True))


def verified_context(
    session: Session, mention: EntityMention
) -> tuple[VerifiedContext, ...]:
    document_id = mention.source_ranges[0].source_document_id
    row = (
        session.execute(
            sa.text("""
            SELECT normalized_body, body_hash, original_language
            FROM source_document WHERE source_document_id = :document_id
        """),
            {"document_id": document_id},
        )
        .mappings()
        .one()
    )
    body = str(row["normalized_body"])
    digest = sha256(body.encode("utf-8")).digest()
    if digest != bytes(row["body_hash"]):
        raise ValueError("source document hash mismatch")
    if normalize("NFC", body) != body or "\r" in body:
        raise ValueError("source document is not normalized NFC/LF text")
    contexts: list[VerifiedContext] = []
    for location in mention.source_ranges:
        if location.end_char > len(body):
            raise ValueError("source range exceeds the immutable document")
        text = body[location.start_char : location.end_char]
        contexts.append(
            VerifiedContext(
                source=SourceContext(**location.model_dump(), quote_text=text),
                body_hash=digest,
                quote_hash=sha256(text.encode("utf-8")).digest(),
                language=str(row["original_language"]),
            )
        )
    if not any(mention.text in item.source.quote_text for item in contexts):
        raise ValueError("mention is absent from its own verified context")
    return tuple(contexts)


def _load_records(session: Session, ids: Sequence[int]) -> tuple[NodeRecord, ...]:
    if not ids:
        return ()
    parameters = {"ids": list(ids)}
    rows = (
        session.execute(
            _ids_statement("""
        SELECT n.node_id, n.node_type_id, t.node_type_code,
               a.alias_text AS preferred_alias,
               (k.item_kind = 'NODE'
                AND k.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
                AND b.promotion_status = 'COMMITTED'
                AND NOT EXISTS (
                    SELECT 1 FROM node_merge m WHERE m.source_node_id = n.node_id
                    AND m.reversed_at IS NULL
                ) AND NOT EXISTS (
                    SELECT 1 FROM lint_finding f JOIN lint_policy_rule p
                      ON p.lint_policy_rule_id = f.lint_policy_rule_id
                    WHERE f.knowledge_item_id = n.node_id
                      AND f.resolved_at IS NULL AND p.severity = 'BLOCKING'
                )) AS usable
        FROM node n JOIN node_type t ON t.node_type_id = n.node_type_id
        JOIN knowledge_item k ON k.knowledge_item_id = n.node_id
        JOIN promotion_batch b ON b.promotion_batch_id = k.promotion_batch_id
        LEFT JOIN node_alias a ON a.node_id = n.node_id AND a.is_preferred
        WHERE n.node_id IN :ids
    """),
            parameters,
        )
        .mappings()
        .all()
    )
    aliases: dict[int, list[str]] = {node_id: [] for node_id in ids}
    for row in session.execute(
        _ids_statement(
            _FAMILY
            + """
        SELECT DISTINCT f.canonical_id, a.alias_text
        FROM family f JOIN node_alias a ON a.node_id = f.member_id
        ORDER BY f.canonical_id, a.alias_text
    """
        ),
        parameters,
    ).mappings():
        aliases[int(row["canonical_id"])].append(str(row["alias_text"]))
    identifiers: dict[int, list[ExternalIdentifier]] = {node_id: [] for node_id in ids}
    for row in session.execute(
        _ids_statement(
            _FAMILY
            + """
        SELECT DISTINCT f.canonical_id, e.identifier_system, e.identifier_value
        FROM family f JOIN external_identifier e ON e.node_id = f.member_id
        ORDER BY f.canonical_id, e.identifier_system, e.identifier_value
    """
        ),
        parameters,
    ).mappings():
        identifiers[int(row["canonical_id"])].append(
            ExternalIdentifier(
                identifier_system=row["identifier_system"],
                identifier_value=row["identifier_value"],
            )
        )
    records = {}
    for row in rows:
        node_id = int(row["node_id"])
        records[node_id] = NodeRecord(
            candidate=NodeCandidate(
                node_id=node_id,
                node_type=row["node_type_code"],
                preferred_alias=row["preferred_alias"],
                aliases=tuple(aliases[node_id]),
                external_identifiers=tuple(identifiers[node_id]),
            ),
            node_type_id=int(row["node_type_id"]),
            usable=bool(row["usable"]),
        )
    if set(records) != set(ids):
        raise ValueError("identity changed during candidate retrieval")
    return tuple(records[node_id] for node_id in ids)


def _resolved_ids(session: Session, sql: str, values: dict[str, object]) -> list[int]:
    rows = session.execute(sa.text(sql), values).mappings().all()
    if any(row["invalid"] for row in rows):
        raise ValueError("invalid stored node redirect; lookup did not complete")
    return [int(row["node_id"]) for row in rows]


def identifier_matches(
    session: Session, mention: EntityMention
) -> tuple[NodeRecord, ...]:
    ids: set[int] = set()
    for identifier in mention.external_identifiers:
        sql = (
            """
            WITH RECURSIVE matched AS (
                SELECT node_id, 0 AS priority, 1.0 AS score
                FROM external_identifier
                WHERE identifier_system = :system AND identifier_value = :value
            )
        """
            + _RESOLVE_MATCHES
        )
        ids.update(
            _resolved_ids(
                session,
                sql,
                {
                    "system": identifier.identifier_system,
                    "value": identifier.identifier_value,
                },
            )
        )
    return _load_records(session, sorted(ids))


def name_candidates(session: Session, mention: EntityMention) -> CandidateSet:
    sql = (
        """
        WITH RECURSIVE matched AS (
            SELECT a.node_id, 0 AS priority, 1.0 AS score
            FROM node_alias a WHERE a.alias_text = :name
            UNION ALL
            SELECT a.node_id, 1 AS priority,
                   ts_rank_cd(to_tsvector('simple', a.alias_text),
                              plainto_tsquery('simple', :name)) AS score
            FROM node_alias a JOIN node n ON n.node_id = a.node_id
            JOIN node_type t ON t.node_type_id = n.node_type_id
            WHERE t.node_type_code = :node_type
              AND to_tsvector('simple', a.alias_text)
                  @@ plainto_tsquery('simple', :name)
        )
    """
        + _RESOLVE_MATCHES
        + " LIMIT :limit"
    )
    ids = _resolved_ids(
        session,
        sql,
        {
            "name": mention.approved_topic_name or mention.text,
            "node_type": mention.node_type,
            "limit": CANDIDATE_LIMIT + 1,
        },
    )
    return CandidateSet(
        nodes=_load_records(session, ids[:CANDIDATE_LIMIT]),
        truncated=len(ids) > CANDIDATE_LIMIT,
    )


def active_type_id(session: Session, code: str) -> int | None:
    value = session.execute(
        sa.text("""
        SELECT node_type_id FROM node_type
        WHERE node_type_code = :code AND is_active
    """),
        {"code": code},
    ).scalar_one_or_none()
    return None if value is None else int(value)


def require_pending_batch(session: Session, batch_id: int) -> None:
    row = (
        session.execute(
            sa.text("""
        SELECT promotion_status, publication_status FROM promotion_batch
        WHERE promotion_batch_id = :batch_id FOR UPDATE
    """),
            {"batch_id": batch_id},
        )
        .mappings()
        .one()
    )
    if (row["promotion_status"], row["publication_status"]) != (
        "PENDING",
        "NOT_STARTED",
    ):
        raise ValueError("entity writes require a pending promotion batch")


def _insert_node(session: Session, batch_id: int, type_id: int) -> int:
    node_id = int(
        session.execute(
            sa.text("""
        INSERT INTO knowledge_item (item_kind, current_state, promotion_batch_id)
        VALUES ('NODE', 'EVIDENCE_VERIFIED', :batch_id)
        RETURNING knowledge_item_id
    """),
            {"batch_id": batch_id},
        ).scalar_one()
    )
    session.execute(
        sa.text("""
        INSERT INTO node (node_id, node_type_id) VALUES (:node_id, :type_id)
    """),
        {"node_id": node_id, "type_id": type_id},
    )
    return node_id


def _ensure_observation(session: Session, context: VerifiedContext) -> int:
    values = context.source.model_dump()
    values.update(quote_hash=context.quote_hash)
    session.execute(
        sa.text("""
        INSERT INTO observation
            (source_document_id, start_char, end_char, quote_text,
             quote_hash, observed_at)
        VALUES (:source_document_id, :start_char, :end_char,
                :quote_text, :quote_hash, CURRENT_TIMESTAMP)
        ON CONFLICT (source_document_id, start_char, end_char) DO NOTHING
    """),
        values,
    )
    row = (
        session.execute(
            sa.text("""
        SELECT observation_id, quote_text, quote_hash FROM observation
        WHERE source_document_id = :source_document_id
          AND start_char = :start_char AND end_char = :end_char
    """),
            values,
        )
        .mappings()
        .one()
    )
    if (
        row["quote_text"] != context.source.quote_text
        or bytes(row["quote_hash"]) != context.quote_hash
    ):
        raise ValueError("existing observation does not match the source range")
    return int(row["observation_id"])


def _ensure_alias(
    session: Session,
    batch_id: int,
    node_id: int,
    text: str,
    language: str,
    observation_id: int,
    *,
    preferred: bool,
) -> None:
    """Apply alias canonical changes and #216 provenance in one transaction."""
    values = {
        "node_id": node_id,
        "text": text,
        "language": language,
        "preferred": preferred,
        "observation_id": observation_id,
    }
    existing = session.execute(
        _ids_statement(
            _FAMILY
            + """
        SELECT a.node_alias_id FROM family f
        JOIN node_alias a ON a.node_id = f.member_id
        WHERE a.alias_text = :text
        ORDER BY (a.node_id = :node_id) DESC,
                 (a.language = :language) DESC, a.node_alias_id
        LIMIT 1
    """
        ),
        {**values, "ids": [node_id]},
    ).scalar_one_or_none()
    if existing is None:
        inserted = session.execute(
            sa.text("""
            INSERT INTO node_alias (node_id, alias_text, language, is_preferred)
            VALUES (:node_id, :text, :language, :preferred)
            ON CONFLICT (node_id, alias_text, language) DO NOTHING
            RETURNING node_alias_id
        """),
            values,
        ).scalar_one_or_none()
        if inserted is not None:
            existing = int(inserted)
            provenance.record_node_alias_changed(session, batch_id, existing)
        else:
            existing = session.execute(
                sa.text("""
                SELECT node_alias_id FROM node_alias
                WHERE node_id = :node_id AND alias_text = :text
                  AND language = :language
            """),
                values,
            ).scalar_one()
    provenance.add_node_alias_evidence(
        session,
        batch_id,
        int(existing),
        observation_id,
    )


def has_evidenced_usage(
    session: Session, node_id: int, observation_ids: Sequence[int]
) -> bool:
    statement = _ids_statement("""
        SELECT EXISTS (
            SELECT 1 FROM claim c
            JOIN knowledge_item k ON k.knowledge_item_id = c.claim_id
            JOIN claim_observation co ON co.claim_id = c.claim_id
            WHERE co.observation_id IN :ids AND k.item_kind = 'CLAIM'
              AND k.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
              AND NOT EXISTS (
                SELECT 1 FROM lint_finding f JOIN lint_policy_rule p
                  ON p.lint_policy_rule_id = f.lint_policy_rule_id
                WHERE f.knowledge_item_id = c.claim_id
                  AND f.resolved_at IS NULL AND p.severity = 'BLOCKING'
              ) AND (
                EXISTS (SELECT 1 FROM claim_relation cr JOIN relation r
                        ON r.relation_id = cr.relation_id
                        JOIN knowledge_item rk ON rk.knowledge_item_id = r.relation_id
                        WHERE cr.claim_id = c.claim_id
                          AND rk.item_kind = 'RELATION'
                          AND rk.current_state IN
                              ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
                          AND NOT EXISTS (
                            SELECT 1 FROM lint_finding rf JOIN lint_policy_rule rp
                              ON rp.lint_policy_rule_id = rf.lint_policy_rule_id
                            WHERE rf.knowledge_item_id = r.relation_id
                              AND rf.resolved_at IS NULL AND rp.severity = 'BLOCKING'
                          )
                          AND (r.source_node_id = :node_id
                               OR r.target_node_id = :node_id))
                OR EXISTS (SELECT 1 FROM claim_attribute_value a
                           WHERE a.claim_id = c.claim_id
                             AND a.target_node_id = :node_id)
                OR EXISTS (SELECT 1 FROM event_temporal_basis e
                           WHERE e.claim_id = c.claim_id
                             AND e.event_node_id = :node_id)
              )
        )
    """)
    return bool(
        session.execute(
            statement,
            {
                "node_id": node_id,
                "ids": list(observation_ids),
            },
        ).scalar_one()
    )
