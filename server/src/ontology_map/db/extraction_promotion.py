"""Transaction-local canonical reconciliation for KNOWLEDGE_EXTRACTION (#127).

No provider IO, publication work, hidden commit, or promotion provenance schema
lives here. Callers own the transaction. B-3 routes provenance-covered mutations
through the official ontology_map.db.promotion_provenance API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Mapping, Sequence, cast

import sqlalchemy as sa
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ontology_map.db import schema


@dataclass(frozen=True)
class RelationRuleSnapshot:
    revision_id: int
    code: str
    version_no: int
    direction: str
    endpoints: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class AttributeRuleSnapshot:
    revision_id: int
    code: str
    version_no: int
    target_node_type: str
    value_kind: str
    units: tuple[str, ...]


@dataclass(frozen=True)
class RelationResult:
    relation_id: int
    created: bool


@dataclass(frozen=True)
class ClaimResult:
    claim_id: int
    created: bool


@dataclass(frozen=True)
class ClaimCandidateSnapshot:
    claim_id: int
    statement: str
    language: str
    modality: str
    semantic_targets: tuple[str, ...]


def _now(session: Session) -> datetime:
    value = session.execute(sa.text("SELECT clock_timestamp()"), {}).scalar_one()
    if not isinstance(value, datetime):
        raise TypeError("DATABASE_CLOCK_NOT_DATETIME")
    return value


def _advisory_key(payload: object) -> int:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    value = int.from_bytes(sha256(encoded).digest()[:8], "big", signed=False)
    return value - (1 << 64) if value >= (1 << 63) else value


def _lock_identity(session: Session, namespace: str, payload: object) -> None:
    session.execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _advisory_key([namespace, payload])},
    )


def source_document(session: Session, document_id: int) -> RowMapping:
    return (
        session.execute(
            sa.select(
                schema.source_document.c.source_document_id,
                schema.source_document.c.normalized_body,
                schema.source_document.c.body_hash,
                schema.source_document.c.original_language,
            ).where(schema.source_document.c.source_document_id == document_id)
        )
        .mappings()
        .one()
    )


def active_policy_id(session: Session, validator_version: str) -> int:
    from ontology_map.db import product_lint

    if validator_version == product_lint.VALIDATOR_VERSION:
        return product_lint.require_product_policy(session)[0]
    rows = list(
        session.execute(
            sa.select(
                schema.lint_policy_version.c.lint_policy_version_id,
                schema.lint_policy_version.c.validator_version,
            ).where(schema.lint_policy_version.c.is_active)
        ).mappings()
    )
    if len(rows) != 1 or rows[0]["validator_version"] != validator_version:
        raise ValueError("ACTIVE_VALIDATOR_CONTRACT_MISMATCH")
    return int(rows[0]["lint_policy_version_id"])


def create_batch(session: Session, validator_version: str) -> int:
    policy_id = active_policy_id(session, validator_version)
    return int(
        session.execute(
            sa.insert(schema.promotion_batch)
            .values(lint_policy_version_id=policy_id)
            .returning(schema.promotion_batch.c.promotion_batch_id)
        ).scalar_one()
    )


def discard_pending_batch(session: Session, batch_id: int) -> None:
    deleted = session.execute(
        sa.delete(schema.promotion_batch)
        .where(
            schema.promotion_batch.c.promotion_batch_id == batch_id,
            schema.promotion_batch.c.promotion_status == "PENDING",
            schema.promotion_batch.c.publication_status == "NOT_STARTED",
        )
        .returning(schema.promotion_batch.c.promotion_batch_id)
    ).scalar_one_or_none()
    if deleted is None:
        raise ValueError("PROMOTION_BATCH_NOT_DISCARDABLE")


def active_topic_identity(session: Session) -> tuple[tuple[int, str, str], ...]:
    rows = session.execute(
        sa.text("""
        SELECT tr.node_id, tr.topic_code, tr.canonical_display_name
        FROM topic_reference tr
        JOIN node n ON n.node_id = tr.node_id
        JOIN node_type nt ON nt.node_type_id = n.node_type_id
        JOIN knowledge_item k ON k.knowledge_item_id = tr.node_id
        WHERE tr.is_active
          AND nt.node_type_code = 'TOPIC'
          AND k.item_kind = 'NODE'
          AND k.lifecycle_kind = 'PRODUCT_REFERENCE'
        ORDER BY tr.topic_code, tr.node_id
        """)
    ).mappings()
    return tuple(
        (
            int(row["node_id"]),
            str(row["topic_code"]),
            str(row["canonical_display_name"]),
        )
        for row in rows
    )


def active_node_types(session: Session) -> tuple[str, ...]:
    return tuple(
        str(value)
        for value in session.scalars(
            sa.select(schema.node_type.c.node_type_code)
            .where(schema.node_type.c.is_active)
            .order_by(schema.node_type.c.node_type_code)
        )
    )


def relation_rule(session: Session, code: str) -> RelationRuleSnapshot:
    row = (
        session.execute(
            sa.text("""
            SELECT r.relation_type_revision_id, t.relation_code,
                   r.version_no, r.directionality
            FROM relation_type_revision r
            JOIN relation_type t USING (relation_type_id)
            WHERE t.relation_code = :code AND r.is_active
            """),
            {"code": code},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("ACTIVE_RELATION_REVISION_MISSING")
    endpoints = tuple(
        (str(item["source_code"]), str(item["target_code"]))
        for item in session.execute(
            sa.text("""
            SELECT s.node_type_code AS source_code,
                   t.node_type_code AS target_code
            FROM relation_endpoint_rule e
            JOIN node_type s ON s.node_type_id = e.source_node_type_id
            JOIN node_type t ON t.node_type_id = e.target_node_type_id
            WHERE e.relation_type_revision_id = :revision
            ORDER BY s.node_type_code, t.node_type_code
            """),
            {"revision": row["relation_type_revision_id"]},
        ).mappings()
    )
    if not endpoints:
        raise ValueError("ACTIVE_RELATION_ENDPOINTS_MISSING")
    return RelationRuleSnapshot(
        revision_id=int(row["relation_type_revision_id"]),
        code=str(row["relation_code"]),
        version_no=int(row["version_no"]),
        direction=str(row["directionality"]),
        endpoints=endpoints,
    )


def attribute_rule(session: Session, code: str) -> AttributeRuleSnapshot:
    row = (
        session.execute(
            sa.text("""
            SELECT r.attribute_revision_id, a.attribute_code, r.version_no,
                   nt.node_type_code AS target_node_type,
                   r.allowed_value_kind
            FROM attribute_revision r
            JOIN attribute a USING (attribute_id)
            JOIN node_type nt ON nt.node_type_id = r.target_node_type_id
            WHERE a.attribute_code = :code AND r.is_active
            """),
            {"code": code},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("ACTIVE_ATTRIBUTE_REVISION_MISSING")
    revision_id = int(row["attribute_revision_id"])
    units = tuple(
        str(value)
        for value in session.scalars(
            sa.select(schema.attribute_revision_allowed_unit.c.unit_code)
            .where(
                schema.attribute_revision_allowed_unit.c.attribute_revision_id
                == revision_id
            )
            .order_by(schema.attribute_revision_allowed_unit.c.unit_code)
        )
    )
    if row["allowed_value_kind"] == "NUMBER" and not units:
        raise ValueError("ACTIVE_ATTRIBUTE_UNITS_MISSING")
    if row["allowed_value_kind"] != "NUMBER" and units:
        raise ValueError("NON_NUMBER_ATTRIBUTE_HAS_UNITS")
    return AttributeRuleSnapshot(
        revision_id=revision_id,
        code=str(row["attribute_code"]),
        version_no=int(row["version_no"]),
        target_node_type=str(row["target_node_type"]),
        value_kind=str(row["allowed_value_kind"]),
        units=units,
    )


def node_type(session: Session, node_id: int) -> str:
    value = session.execute(
        sa.text("""
        SELECT t.node_type_code
        FROM node n JOIN node_type t USING (node_type_id)
        WHERE n.node_id = :node_id
        """),
        {"node_id": node_id},
    ).scalar_one_or_none()
    if value is None:
        raise ValueError("NODE_MISSING")
    return str(value)


def ensure_observation(
    session: Session,
    document_id: int,
    start: int,
    end: int,
    quote: str,
) -> tuple[int, bool]:
    document = source_document(session, document_id)
    body = str(document["normalized_body"])
    body_hash = sha256(body.encode()).digest()
    if body_hash != bytes(document["body_hash"]):
        raise ValueError("SOURCE_HASH_MISMATCH")
    if start < 0 or end <= start or end > len(body) or body[start:end] != quote:
        raise ValueError("SOURCE_RANGE_MISMATCH")
    quote_hash = sha256(quote.encode()).digest()
    inserted = session.execute(
        sa.text("""
        INSERT INTO observation
          (source_document_id, start_char, end_char, quote_text,
           quote_hash, observed_at)
        VALUES (:document_id, :start, :end, :quote, :quote_hash, clock_timestamp())
        ON CONFLICT (source_document_id, start_char, end_char) DO NOTHING
        RETURNING observation_id
        """),
        {
            "document_id": document_id,
            "start": start,
            "end": end,
            "quote": quote,
            "quote_hash": quote_hash,
        },
    ).scalar_one_or_none()
    created = inserted is not None
    if inserted is None:
        row = (
            session.execute(
                sa.select(
                    schema.observation.c.observation_id,
                    schema.observation.c.quote_text,
                    schema.observation.c.quote_hash,
                ).where(
                    schema.observation.c.source_document_id == document_id,
                    schema.observation.c.start_char == start,
                    schema.observation.c.end_char == end,
                )
            )
            .mappings()
            .one()
        )
        if row["quote_text"] != quote or bytes(row["quote_hash"]) != quote_hash:
            raise ValueError("OBSERVATION_RANGE_COLLISION")
        inserted = int(row["observation_id"])
    return int(inserted), created


def _usable_knowledge(
    session: Session, item_id: int, kind: str, current_batch_id: int | None = None
) -> bool:
    return bool(
        session.execute(
            sa.text("""
            SELECT EXISTS (
              SELECT 1 FROM knowledge_item k
              JOIN promotion_batch b USING (promotion_batch_id)
              WHERE k.knowledge_item_id = :id
                AND k.item_kind = :kind
                AND k.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
                AND (b.promotion_status = 'COMMITTED'
                     OR k.promotion_batch_id = :current_batch_id)
                AND NOT EXISTS (
                  SELECT 1 FROM lint_finding f
                  JOIN lint_policy_rule p USING (lint_policy_rule_id)
                  WHERE f.knowledge_item_id = k.knowledge_item_id
                    AND f.resolved_at IS NULL AND p.severity = 'BLOCKING'
                )
            )
            """),
            {"id": item_id, "kind": kind, "current_batch_id": current_batch_id},
        ).scalar_one()
    )


def _relation_identity(
    revision_id: int, direction: str, source_node_id: int, target_node_id: int
) -> tuple[bytes, int, int]:
    source = source_node_id
    target = target_node_id
    if direction == "SYMMETRIC" and target < source:
        source, target = target, source
    raw = json.dumps(
        [revision_id, source, target], separators=(",", ":"), allow_nan=False
    ).encode()
    return sha256(raw).digest(), source, target


def ensure_relation(
    session: Session,
    batch_id: int,
    rule: RelationRuleSnapshot,
    source_node_id: int,
    target_node_id: int,
) -> RelationResult:
    source_type = node_type(session, source_node_id)
    target_type = node_type(session, target_node_id)
    endpoints = (source_type, target_type)
    allowed = endpoints in rule.endpoints or (
        rule.direction == "SYMMETRIC" and endpoints[::-1] in rule.endpoints
    )
    if not allowed:
        raise ValueError("RELATION_ENDPOINT_NOT_ALLOWED")
    key, source, target = _relation_identity(
        rule.revision_id, rule.direction, source_node_id, target_node_id
    )
    _lock_identity(session, "relation", key.hex())
    row = (
        session.execute(
            sa.select(schema.relation).where(
                schema.relation.c.relation_identity_key == key
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is not None:
        relation_id = int(row["relation_id"])
        if (
            int(row["source_node_id"]) != source
            or int(row["target_node_id"]) != target
            or int(row["relation_type_revision_id"]) != rule.revision_id
            or not _usable_knowledge(session, relation_id, "RELATION", batch_id)
        ):
            raise ValueError("RELATION_IDENTITY_COLLISION_OR_UNUSABLE")
        return RelationResult(relation_id, False)
    relation_id = int(
        session.execute(
            sa.insert(schema.knowledge_item)
            .values(
                item_kind="RELATION",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(schema.knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    session.execute(
        sa.insert(schema.relation).values(
            relation_id=relation_id,
            source_node_id=source,
            target_node_id=target,
            relation_type_revision_id=rule.revision_id,
            relation_identity_key=key,
        )
    )
    return RelationResult(relation_id, True)


def _attribute_tuple(row: Mapping[str, object]) -> tuple[object, ...]:
    date_from = cast(date | None, row["date_from"])
    date_to = cast(date | None, row["date_to"])
    return (
        cast(int, row["target_node_id"]),
        cast(int, row["attribute_revision_id"]),
        str(row["value_kind"]),
        row["string_value"],
        None if row["number_value"] is None else str(row["number_value"]),
        row["unit_code"],
        None if date_from is None else date_from.isoformat(),
        None if date_to is None else date_to.isoformat(),
        str(row["date_from_precision"]),
        str(row["date_to_precision"]),
        row["boolean_value"],
    )


def canonical_attribute_tuple(values: Mapping[str, object]) -> tuple[object, ...]:
    converted = dict(values)
    if isinstance(converted.get("number_value"), Decimal):
        converted["number_value"] = str(converted["number_value"])
    date_from_value = converted.get("date_from")
    if isinstance(date_from_value, date):
        converted["date_from"] = date_from_value.isoformat()
    date_to_value = converted.get("date_to")
    if isinstance(date_to_value, date):
        converted["date_to"] = date_to_value.isoformat()
    return (
        cast(int, converted["target_node_id"]),
        cast(int, converted["attribute_revision_id"]),
        str(converted["value_kind"]),
        converted.get("string_value"),
        converted.get("number_value"),
        converted.get("unit_code"),
        converted.get("date_from"),
        converted.get("date_to"),
        str(converted.get("date_from_precision", "UNKNOWN")),
        str(converted.get("date_to_precision", "UNKNOWN")),
        converted.get("boolean_value"),
    )


def _claim_signature(
    session: Session, claim_id: int
) -> tuple[
    tuple[tuple[int, str], ...],
    tuple[tuple[object, ...], ...],
    tuple[tuple[object, ...], ...],
]:
    relations = tuple(
        (int(row["relation_id"]), str(row["stance"]))
        for row in session.execute(
            sa.select(
                schema.claim_relation.c.relation_id,
                schema.claim_relation.c.stance,
            )
            .where(schema.claim_relation.c.claim_id == claim_id)
            .order_by(schema.claim_relation.c.relation_id)
        ).mappings()
    )
    attrs = tuple(
        sorted(
            _attribute_tuple(dict(row))
            for row in session.execute(
                sa.select(schema.claim_attribute_value)
                .where(schema.claim_attribute_value.c.claim_id == claim_id)
                .order_by(schema.claim_attribute_value.c.claim_attribute_value_id)
            ).mappings()
        )
    )
    events = tuple(
        sorted(
            (
                int(row["event_node_id"]),
                row["start_at"],
                row["end_at"],
                str(row["start_precision"]),
                str(row["end_precision"]),
            )
            for row in session.execute(
                sa.text("""
                SELECT b.event_node_id, e.start_at, e.end_at,
                       e.start_precision, e.end_precision
                FROM event_temporal_basis b
                JOIN event_temporal_extent e USING (event_node_id)
                WHERE b.claim_id = :claim_id
                ORDER BY b.event_node_id
                """),
                {"claim_id": claim_id},
            ).mappings()
        )
    )
    return relations, attrs, events


def _semantic_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def claim_semantic_targets(session: Session, claim_id: int) -> tuple[str, ...]:
    """Canonical semantic targets only; observations are intentionally excluded."""
    targets: list[str] = []
    relations = session.execute(
        sa.text("""
        SELECT t.relation_code, r.relation_type_revision_id,
               r.source_node_id, r.target_node_id, cr.stance
        FROM claim_relation cr
        JOIN relation r ON r.relation_id = cr.relation_id
        JOIN relation_type_revision rr
          ON rr.relation_type_revision_id = r.relation_type_revision_id
        JOIN relation_type t ON t.relation_type_id = rr.relation_type_id
        WHERE cr.claim_id = :claim_id
        ORDER BY t.relation_code, r.relation_type_revision_id,
                 r.source_node_id, r.target_node_id, cr.stance
        """),
        {"claim_id": claim_id},
    ).mappings()
    for row in relations:
        targets.append(
            _semantic_json(
                {
                    "kind": "RELATION",
                    "code": str(row["relation_code"]),
                    "revision_id": int(row["relation_type_revision_id"]),
                    "source": {"node_id": int(row["source_node_id"])},
                    "target": {"node_id": int(row["target_node_id"])},
                    "stance": str(row["stance"]),
                }
            )
        )
    attributes = session.execute(
        sa.text("""
        SELECT a.attribute_code, v.attribute_revision_id, v.target_node_id,
               v.value_kind, v.string_value, v.number_value, v.unit_code,
               v.date_from, v.date_to, v.date_from_precision,
               v.date_to_precision, v.boolean_value
        FROM claim_attribute_value v
        JOIN attribute_revision ar
          ON ar.attribute_revision_id = v.attribute_revision_id
        JOIN attribute a ON a.attribute_id = ar.attribute_id
        WHERE v.claim_id = :claim_id
        ORDER BY a.attribute_code, v.attribute_revision_id,
                 v.claim_attribute_value_id
        """),
        {"claim_id": claim_id},
    ).mappings()
    for row in attributes:
        targets.append(
            _semantic_json(
                {
                    "kind": "ATTRIBUTE",
                    "code": str(row["attribute_code"]),
                    "revision_id": int(row["attribute_revision_id"]),
                    "target": {"node_id": int(row["target_node_id"])},
                    "value_kind": str(row["value_kind"]),
                    "string_value": row["string_value"],
                    "number_value": (
                        None
                        if row["number_value"] is None
                        else str(row["number_value"])
                    ),
                    "unit_code": row["unit_code"],
                    "date_from": (
                        None
                        if row["date_from"] is None
                        else row["date_from"].isoformat()
                    ),
                    "date_to": (
                        None if row["date_to"] is None else row["date_to"].isoformat()
                    ),
                    "date_from_precision": str(row["date_from_precision"]),
                    "date_to_precision": str(row["date_to_precision"]),
                    "boolean_value": row["boolean_value"],
                }
            )
        )
    events = session.execute(
        sa.text("""
        SELECT b.event_node_id, e.start_at, e.end_at,
               e.start_precision, e.end_precision
        FROM event_temporal_basis b
        JOIN event_temporal_extent e USING (event_node_id)
        WHERE b.claim_id = :claim_id
        ORDER BY b.event_node_id
        """),
        {"claim_id": claim_id},
    ).mappings()
    for row in events:
        targets.append(
            _semantic_json(
                {
                    "kind": "EVENT_TIME",
                    "event": {"node_id": int(row["event_node_id"])},
                    "start_at": (
                        None if row["start_at"] is None else row["start_at"].isoformat()
                    ),
                    "end_at": (
                        None if row["end_at"] is None else row["end_at"].isoformat()
                    ),
                    "start_precision": str(row["start_precision"]),
                    "end_precision": str(row["end_precision"]),
                }
            )
        )
    return tuple(sorted(targets))


def claim_duplicate_candidates(
    session: Session,
    *,
    statement: str,
    language: str,
    modality: str,
    node_ids: Sequence[int],
    limit: int = 8,
) -> tuple[ClaimCandidateSnapshot, ...]:
    """Small deterministic candidate set for the runtime Claim duplicate judge."""
    if limit <= 0:
        raise ValueError("INVALID_CLAIM_DUPLICATE_LIMIT")
    claim = schema.claim
    conditions: list[sa.ColumnElement[bool]] = [claim.c.statement_text == statement]
    node_values = tuple(sorted(set(node_ids)))
    if node_values:
        relation_touch = sa.exists(
            sa.select(1)
            .select_from(
                schema.claim_relation.join(
                    schema.relation,
                    schema.relation.c.relation_id
                    == schema.claim_relation.c.relation_id,
                )
            )
            .where(
                schema.claim_relation.c.claim_id == claim.c.claim_id,
                sa.or_(
                    schema.relation.c.source_node_id.in_(node_values),
                    schema.relation.c.target_node_id.in_(node_values),
                ),
            )
        )
        attribute_touch = sa.exists(
            sa.select(1)
            .select_from(schema.claim_attribute_value)
            .where(
                schema.claim_attribute_value.c.claim_id == claim.c.claim_id,
                schema.claim_attribute_value.c.target_node_id.in_(node_values),
            )
        )
        event_touch = sa.exists(
            sa.select(1)
            .select_from(schema.event_temporal_basis)
            .where(
                schema.event_temporal_basis.c.claim_id == claim.c.claim_id,
                schema.event_temporal_basis.c.event_node_id.in_(node_values),
            )
        )
        conditions.extend((relation_touch, attribute_touch, event_touch))
    rows = session.execute(
        sa.select(
            claim.c.claim_id,
            claim.c.statement_text,
            claim.c.language,
            claim.c.modality,
        )
        .where(
            claim.c.language == language,
            claim.c.modality == modality,
            sa.or_(*conditions),
        )
        .order_by((claim.c.statement_text == statement).desc(), claim.c.claim_id)
        .limit(limit * 4)
    ).mappings()
    result: list[ClaimCandidateSnapshot] = []
    for row in rows:
        claim_id = int(row["claim_id"])
        if not _usable_knowledge(session, claim_id, "CLAIM"):
            continue
        result.append(
            ClaimCandidateSnapshot(
                claim_id=claim_id,
                statement=str(row["statement_text"]),
                language=str(row["language"]),
                modality=str(row["modality"]),
                semantic_targets=claim_semantic_targets(session, claim_id),
            )
        )
        if len(result) >= limit:
            break
    return tuple(result)


def revalidate_claim_candidate(
    session: Session, candidate: ClaimCandidateSnapshot
) -> None:
    row = (
        session.execute(
            sa.select(
                schema.claim.c.statement_text,
                schema.claim.c.language,
                schema.claim.c.modality,
            ).where(schema.claim.c.claim_id == candidate.claim_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not _usable_knowledge(session, candidate.claim_id, "CLAIM"):
        raise ValueError("CLAIM_DUPLICATE_CANDIDATE_CHANGED")
    current = (
        str(row["statement_text"]),
        str(row["language"]),
        str(row["modality"]),
        claim_semantic_targets(session, candidate.claim_id),
    )
    expected = (
        candidate.statement,
        candidate.language,
        candidate.modality,
        candidate.semantic_targets,
    )
    if current != expected:
        raise ValueError("CLAIM_DUPLICATE_CANDIDATE_CHANGED")


def find_exact_claim(
    session: Session,
    *,
    statement: str,
    language: str,
    modality: str,
    relations: Sequence[tuple[int, str]],
    attributes: Sequence[Mapping[str, object]],
    events: Sequence[tuple[int, datetime | None, datetime | None, str, str]],
    current_batch_id: int | None = None,
) -> int | None:
    relation_signature = tuple(sorted(set(relations)))
    attribute_signature = tuple(
        sorted({canonical_attribute_tuple(v) for v in attributes})
    )
    event_signature = tuple(sorted(set(events)))
    identity = [
        statement,
        language,
        modality,
        relation_signature,
        attribute_signature,
        event_signature,
    ]
    _lock_identity(session, "claim", identity)
    candidates = list(
        session.scalars(
            sa.text("""
            SELECT c.claim_id
            FROM claim c
            JOIN knowledge_item k ON k.knowledge_item_id = c.claim_id
            JOIN promotion_batch b ON b.promotion_batch_id = k.promotion_batch_id
            WHERE c.statement_text = :statement
              AND c.language = :language
              AND c.modality = :modality
              AND c.asserted_from IS NULL
              AND c.asserted_to IS NULL
              AND c.asserted_from_precision = 'UNKNOWN'
              AND c.asserted_to_precision = 'UNKNOWN'
              AND k.item_kind = 'CLAIM'
              AND k.current_state IN ('EVIDENCE_VERIFIED', 'HUMAN_VERIFIED')
              AND (b.promotion_status = 'COMMITTED'
                   OR k.promotion_batch_id = :current_batch_id)
              AND NOT EXISTS (
                SELECT 1 FROM lint_finding f
                JOIN lint_policy_rule p USING (lint_policy_rule_id)
                WHERE f.knowledge_item_id = c.claim_id
                  AND f.resolved_at IS NULL AND p.severity = 'BLOCKING'
              )
            ORDER BY c.claim_id
            """),
            {
                "statement": statement,
                "language": language,
                "modality": modality,
                "current_batch_id": current_batch_id,
            },
        )
    )
    matches = [
        int(claim_id)
        for claim_id in candidates
        if _claim_signature(session, int(claim_id))
        == (relation_signature, attribute_signature, event_signature)
    ]
    if len(matches) > 1:
        raise ValueError("AMBIGUOUS_EXISTING_CLAIM")
    return matches[0] if matches else None


def insert_claim(
    session: Session,
    batch_id: int,
    *,
    statement: str,
    language: str,
    modality: str,
) -> ClaimResult:
    claim_id = int(
        session.execute(
            sa.insert(schema.knowledge_item)
            .values(
                item_kind="CLAIM",
                current_state="EVIDENCE_VERIFIED",
                promotion_batch_id=batch_id,
            )
            .returning(schema.knowledge_item.c.knowledge_item_id)
        ).scalar_one()
    )
    session.execute(
        sa.insert(schema.claim).values(
            claim_id=claim_id,
            statement_text=statement,
            language=language,
            modality=modality,
            asserted_from=None,
            asserted_to=None,
            asserted_from_precision="UNKNOWN",
            asserted_to_precision="UNKNOWN",
        )
    )
    return ClaimResult(claim_id, True)


def claim_observation_exists(
    session: Session, claim_id: int, observation_id: int
) -> bool:
    return bool(
        session.execute(
            sa.select(
                sa.exists().where(
                    schema.claim_observation.c.claim_id == claim_id,
                    schema.claim_observation.c.observation_id == observation_id,
                )
            )
        ).scalar_one()
    )


def claim_relation_exists(
    session: Session, claim_id: int, relation_id: int
) -> str | None:
    value = session.execute(
        sa.select(schema.claim_relation.c.stance).where(
            schema.claim_relation.c.claim_id == claim_id,
            schema.claim_relation.c.relation_id == relation_id,
        )
    ).scalar_one_or_none()
    return None if value is None else str(value)


def claim_attribute_value_exists(
    session: Session, claim_id: int, values: Mapping[str, object]
) -> bool:
    expected = canonical_attribute_tuple(values)
    rows = session.execute(
        sa.select(schema.claim_attribute_value).where(
            schema.claim_attribute_value.c.claim_id == claim_id
        )
    ).mappings()
    return any(_attribute_tuple(dict(row)) == expected for row in rows)


def event_extent(
    session: Session, event_node_id: int
) -> tuple[datetime | None, datetime | None, str, str] | None:
    row = (
        session.execute(
            sa.select(schema.event_temporal_extent).where(
                schema.event_temporal_extent.c.event_node_id == event_node_id
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return (
        row["start_at"],
        row["end_at"],
        str(row["start_precision"]),
        str(row["end_precision"]),
    )


def ensure_event_extent(
    session: Session,
    event_node_id: int,
    *,
    start_at: datetime | None,
    end_at: datetime | None,
    start_precision: str,
    end_precision: str,
) -> bool:
    expected = (start_at, end_at, start_precision, end_precision)
    existing = event_extent(session, event_node_id)
    if existing is not None:
        if existing != expected:
            raise ValueError("EVENT_TEMPORAL_EXTENT_CONFLICT")
        return False
    session.execute(
        sa.insert(schema.event_temporal_extent).values(
            event_node_id=event_node_id,
            start_at=start_at,
            end_at=end_at,
            start_precision=start_precision,
            end_precision=end_precision,
        )
    )
    return True


def event_basis_exists(session: Session, event_node_id: int, claim_id: int) -> bool:
    return bool(
        session.execute(
            sa.select(
                sa.exists().where(
                    schema.event_temporal_basis.c.event_node_id == event_node_id,
                    schema.event_temporal_basis.c.claim_id == claim_id,
                )
            )
        ).scalar_one()
    )


def relation_has_supported_claim(session: Session, relation_id: int) -> bool:
    return bool(
        session.execute(
            sa.text("""
            SELECT EXISTS (
                SELECT 1 FROM claim_relation cr
                JOIN claim_observation co ON co.claim_id = cr.claim_id
                WHERE cr.relation_id = :relation_id AND cr.stance = 'SUPPORT'
            )
            """),
            {"relation_id": relation_id},
        ).scalar_one()
    )
