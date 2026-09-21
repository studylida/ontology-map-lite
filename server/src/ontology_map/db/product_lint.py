"""The deterministic #110 product lint policy and full-graph evaluator."""

from __future__ import annotations

from hashlib import sha256

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map.db import schema

VALIDATOR_VERSION = "source-intake-110-v1"
RULES = (
    ("EVIDENCE_TRACE_COMPLETE", "근거 경로", "지식이 원문 근거에 연결된다.", "BOTH"),
    (
        "OBSERVATION_SOURCE_INTEGRITY",
        "원문 일치",
        "범위·인용·해시가 불변 원문과 일치한다.",
        "BOTH",
    ),
    (
        "CLAIM_SEMANTIC_TARGET",
        "주장 대상",
        "Claim에 관계·속성·사건 시간 대상이 있다.",
        "BOTH",
    ),
    (
        "RELATION_SUPPORTED",
        "관계 지지",
        "Relation에 원문 근거가 있는 지지 Claim이 있다.",
        "BOTH",
    ),
    (
        "ACTIVE_ONTOLOGY",
        "활성 온톨로지",
        "새 지식이 활성 유형·revision의 규칙을 따른다.",
        "PRE_PROMOTION",
    ),
    (
        "STORED_REVISION_VALID",
        "저장 revision",
        "저장 지식이 생성 당시 revision의 규칙을 따른다.",
        "PERSISTED_GRAPH",
    ),
)


def _definitions(session: Session) -> tuple[int | None, dict[str, int]]:
    rows = session.execute(sa.select(schema.lint_rule)).mappings().all()
    by_code = {str(row["rule_code"]): row for row in rows}
    ids: dict[str, int] = {}
    for code, name, description, scope in RULES:
        row = by_code.get(code)
        if row is not None:
            if (row["display_name"], row["description"], row["evaluation_scope"]) != (
                name,
                description,
                scope,
            ):
                raise ValueError(f"PRODUCT_LINT_RULE_MISMATCH:{code}")
            ids[code] = int(row["lint_rule_id"])
    policy = (
        session.execute(
            sa.select(schema.lint_policy_version).where(
                schema.lint_policy_version.c.version_no == 1
            )
        )
        .mappings()
        .one_or_none()
    )
    if policy is not None and policy["validator_version"] != VALIDATOR_VERSION:
        raise ValueError("PRODUCT_LINT_POLICY_VERSION_CONFLICT")
    return (None if policy is None else int(policy["lint_policy_version_id"]), ids)


def ensure_product_policy(session: Session) -> int:
    """Insert missing immutable definitions in an explicit caller transaction."""
    if not session.in_transaction():
        raise ValueError("PRODUCT_LINT_TRANSACTION_REQUIRED")
    active = session.scalars(
        sa.select(schema.lint_policy_version.c.validator_version).where(
            schema.lint_policy_version.c.is_active
        )
    ).all()
    if active and active != [VALIDATOR_VERSION]:
        raise ValueError("ACTIVE_VALIDATOR_CONTRACT_MISMATCH")
    policy_id, ids = _definitions(session)
    if policy_id is not None:
        require_product_policy(session)
        return policy_id
    for code, name, description, scope in RULES:
        if code not in ids:
            ids[code] = int(
                session.execute(
                    schema.lint_rule.insert()
                    .values(
                        rule_code=code,
                        display_name=name,
                        description=description,
                        evaluation_scope=scope,
                    )
                    .returning(schema.lint_rule.c.lint_rule_id)
                ).scalar_one()
            )
    policy_id = int(
        session.execute(
            schema.lint_policy_version.insert()
            .values(
                version_no=1,
                validator_version=VALIDATOR_VERSION,
                is_active=True,
                activated_at=sa.func.clock_timestamp(),
            )
            .returning(schema.lint_policy_version.c.lint_policy_version_id)
        ).scalar_one()
    )
    for rule_id in ids.values():
        session.execute(
            schema.lint_policy_rule.insert().values(
                lint_policy_version_id=policy_id,
                lint_rule_id=rule_id,
                severity="BLOCKING",
            )
        )
    require_product_policy(session)
    return policy_id


def require_product_policy(session: Session) -> tuple[int, dict[str, int]]:
    """Fail closed unless the active selection exactly matches this validator."""
    rows = (
        session.execute(
            sa.select(schema.lint_policy_version).where(
                schema.lint_policy_version.c.is_active
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != 1 or (rows[0]["version_no"], rows[0]["validator_version"]) != (
        1,
        VALIDATOR_VERSION,
    ):
        raise ValueError("ACTIVE_VALIDATOR_CONTRACT_MISMATCH")
    policy_id, ids = _definitions(session)
    if policy_id is None or len(ids) != len(RULES):
        raise ValueError("PRODUCT_LINT_DEFINITION_MISSING")
    selections = (
        session.execute(
            sa.select(schema.lint_policy_rule).where(
                schema.lint_policy_rule.c.lint_policy_version_id == policy_id
            )
        )
        .mappings()
        .all()
    )
    if {(int(row["lint_rule_id"]), str(row["severity"])) for row in selections} != {
        (rule_id, "BLOCKING") for rule_id in ids.values()
    }:
        raise ValueError("PRODUCT_LINT_POLICY_RULE_MISMATCH")
    return policy_id, {
        code: int(
            next(
                row["lint_policy_rule_id"]
                for row in selections
                if row["lint_rule_id"] == rule_id
            )
        )
        for code, rule_id in ids.items()
    }


def _valid_observations(session: Session) -> set[int]:
    rows = session.execute(
        sa.text("""
        SELECT o.observation_id, o.start_char, o.end_char, o.quote_text, o.quote_hash,
               d.normalized_body, d.body_hash
        FROM observation o JOIN source_document d USING (source_document_id)
    """)
    ).mappings()
    valid: set[int] = set()
    for row in rows:
        body = str(row["normalized_body"])
        quote = str(row["quote_text"])
        start, end = int(row["start_char"]), int(row["end_char"])
        if (
            sha256(body.encode()).digest() == bytes(row["body_hash"])
            and 0 <= start < end <= len(body)
            and body[start:end] == quote
            and sha256(quote.encode()).digest() == bytes(row["quote_hash"])
        ):
            valid.add(int(row["observation_id"]))
    return valid


def _direct_evidence(
    session: Session, kinds: dict[int, str], valid: set[int]
) -> tuple[set[int], set[int], set[int], set[tuple[str, int]]]:
    findings: set[tuple[str, int]] = set()

    direct: set[int] = set()
    claim_evidence: set[int] = set()
    claim_bad: set[int] = set()
    for row in session.execute(
        sa.text("""
        SELECT claim_id AS item_id, observation_id FROM claim_observation
        UNION ALL
        SELECT a.node_id AS item_id, e.observation_id
        FROM node_alias_evidence e JOIN node_alias a USING (node_alias_id)
    """)
    ).mappings():
        item_id, observation_id = int(row["item_id"]), int(row["observation_id"])
        if item_id not in kinds:
            continue
        if observation_id in valid:
            direct.add(item_id)
            if kinds[item_id] == "CLAIM":
                claim_evidence.add(item_id)
        else:
            findings.add(("OBSERVATION_SOURCE_INTEGRITY", item_id))
            if kinds[item_id] == "CLAIM":
                claim_bad.add(item_id)
    return direct, claim_evidence, claim_bad, findings


def _relation_evidence(
    session: Session,
    direct: set[int],
    claim_evidence: set[int],
    claim_bad: set[int],
    findings: set[tuple[str, int]],
) -> tuple[set[int], set[int]]:
    targets: set[int] = set()
    supported: set[int] = set()
    for row in session.execute(
        sa.text("""
        SELECT cr.claim_id, cr.relation_id, cr.stance,
               r.source_node_id, r.target_node_id
        FROM claim_relation cr JOIN relation r USING (relation_id)
    """)
    ).mappings():
        claim_id, relation_id = int(row["claim_id"]), int(row["relation_id"])
        targets.add(claim_id)
        if claim_id in claim_bad:
            for item_id in (
                relation_id,
                int(row["source_node_id"]),
                int(row["target_node_id"]),
            ):
                findings.add(("OBSERVATION_SOURCE_INTEGRITY", item_id))
        if claim_id in claim_evidence:
            for item_id in (
                relation_id,
                int(row["source_node_id"]),
                int(row["target_node_id"]),
            ):
                direct.add(item_id)
            if row["stance"] == "SUPPORT":
                supported.add(relation_id)
    return targets, supported


def _node_target_evidence(
    session: Session,
    direct: set[int],
    claim_evidence: set[int],
    claim_bad: set[int],
    findings: set[tuple[str, int]],
) -> set[int]:
    targets: set[int] = set()
    for sql in (
        "SELECT claim_id, target_node_id AS node_id FROM claim_attribute_value",
        "SELECT claim_id, event_node_id AS node_id FROM event_temporal_basis",
    ):
        for row in session.execute(sa.text(sql)).mappings():
            claim_id, node_id = int(row["claim_id"]), int(row["node_id"])
            targets.add(claim_id)
            if claim_id in claim_bad:
                findings.add(("OBSERVATION_SOURCE_INTEGRITY", node_id))
            if claim_id in claim_evidence:
                direct.add(node_id)
    return targets


def _revision_findings(session: Session) -> set[tuple[str, int]]:
    findings: set[tuple[str, int]] = set()

    for row in session.execute(
        sa.text("""
        SELECT r.relation_id, src.node_type_id AS source_type,
               dst.node_type_id AS target_type, rr.directionality,
               rr.relation_type_revision_id AS revision_id
        FROM relation r JOIN node src ON src.node_id = r.source_node_id
        JOIN node dst ON dst.node_id = r.target_node_id
        JOIN relation_type_revision rr USING (relation_type_revision_id)
    """)
    ).mappings():
        endpoints = session.execute(
            sa.text("""
            SELECT source_node_type_id, target_node_type_id FROM relation_endpoint_rule
            WHERE relation_type_revision_id = :revision_id
        """),
            {"revision_id": row["revision_id"]},
        ).all()
        pair = (row["source_type"], row["target_type"])
        if pair not in endpoints and not (
            row["directionality"] == "SYMMETRIC" and pair[::-1] in endpoints
        ):
            findings.add(("STORED_REVISION_VALID", int(row["relation_id"])))
    findings.update(_attribute_revision_findings(session))
    return findings


def _attribute_revision_findings(session: Session) -> set[tuple[str, int]]:
    findings: set[tuple[str, int]] = set()
    for row in session.execute(
        sa.text("""
        SELECT v.claim_id, v.value_kind, v.unit_code, v.target_node_id,
               ar.allowed_value_kind, ar.target_node_type_id, n.node_type_id,
               v.attribute_revision_id
        FROM claim_attribute_value v
        JOIN attribute_revision ar USING (attribute_revision_id)
        JOIN node n ON n.node_id = v.target_node_id
    """)
    ).mappings():
        unit_valid = row["value_kind"] != "NUMBER" or bool(
            session.scalar(
                sa.text("""
            SELECT EXISTS (SELECT 1 FROM attribute_revision_allowed_unit
            WHERE attribute_revision_id = :revision_id AND unit_code = :unit_code)
        """),
                {
                    "revision_id": row["attribute_revision_id"],
                    "unit_code": row["unit_code"],
                },
            )
        )
        if (
            row["value_kind"] != row["allowed_value_kind"]
            or row["node_type_id"] != row["target_node_type_id"]
            or not unit_valid
        ):
            findings.add(("STORED_REVISION_VALID", int(row["claim_id"])))
    return findings


def evaluate_persisted_graph(session: Session) -> set[tuple[str, int]]:
    """Return (rule code, evidence-backed item ID) for committed graph defects."""
    require_product_policy(session)
    rows = session.execute(
        sa.text("""
        SELECT k.knowledge_item_id AS id, k.item_kind AS kind
        FROM knowledge_item k JOIN promotion_batch b USING (promotion_batch_id)
        WHERE k.lifecycle_kind = 'EVIDENCE_BACKED' AND b.promotion_status = 'COMMITTED'
    """)
    ).mappings()
    kinds = {int(row["id"]): str(row["kind"]) for row in rows}
    direct, claim_evidence, claim_bad, findings = _direct_evidence(
        session, kinds, _valid_observations(session)
    )
    targets, supported = _relation_evidence(
        session, direct, claim_evidence, claim_bad, findings
    )
    targets.update(
        _node_target_evidence(session, direct, claim_evidence, claim_bad, findings)
    )

    for item_id, kind in kinds.items():
        if item_id not in direct:
            findings.add(("EVIDENCE_TRACE_COMPLETE", item_id))
        if kind == "CLAIM" and item_id not in targets:
            findings.add(("CLAIM_SEMANTIC_TARGET", item_id))
        if kind == "RELATION" and item_id not in supported:
            findings.add(("RELATION_SUPPORTED", item_id))

    findings.update(_revision_findings(session))
    return {(code, item_id) for code, item_id in findings if item_id in kinds}


def run_full_graph(session: Session) -> int:
    """Persist one successful full scan and resolve only findings it clears."""
    if not session.in_transaction():
        raise ValueError("PRODUCT_LINT_TRANSACTION_REQUIRED")
    if session.connection().get_isolation_level() not in {
        "REPEATABLE READ",
        "SERIALIZABLE",
    }:
        raise ValueError("PRODUCT_LINT_SNAPSHOT_REQUIRED")
    policy_id, rule_ids = require_product_policy(session)
    findings = evaluate_persisted_graph(session)
    now = session.scalar(sa.func.clock_timestamp())
    run_id = int(
        session.execute(
            schema.lint_run.insert()
            .values(lint_policy_version_id=policy_id, status="RUNNING", started_at=now)
            .returning(schema.lint_run.c.lint_run_id)
        ).scalar_one()
    )
    keys: set[bytes] = set()
    for code, item_id in sorted(findings):
        key = sha256(f"{policy_id}:{code}:{item_id}".encode()).digest()
        keys.add(key)
        existing = (
            session.execute(
                sa.select(schema.lint_finding).where(
                    schema.lint_finding.c.finding_key == key,
                    schema.lint_finding.c.resolved_at.is_(None),
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            session.execute(
                schema.lint_finding.insert().values(
                    finding_key=key,
                    knowledge_item_id=item_id,
                    lint_policy_rule_id=rule_ids[code],
                    first_detected_run_id=run_id,
                    latest_detected_run_id=run_id,
                    first_detected_at=now,
                    last_detected_at=now,
                    message=code,
                )
            )
        else:
            session.execute(
                schema.lint_finding.update()
                .where(
                    schema.lint_finding.c.lint_finding_id == existing["lint_finding_id"]
                )
                .values(
                    latest_detected_run_id=run_id,
                    last_detected_at=now,
                    detection_count=existing["detection_count"] + 1,
                )
            )
    session.execute(
        schema.lint_finding.update()
        .where(
            schema.lint_finding.c.lint_policy_rule_id.in_(rule_ids.values()),
            schema.lint_finding.c.resolved_at.is_(None),
            ~schema.lint_finding.c.finding_key.in_(keys),
        )
        .values(
            resolved_by_run_id=run_id,
            resolved_at=now,
            resolution_reason="FULL_GRAPH_CLEAR",
        )
    )
    session.execute(
        schema.lint_run.update()
        .where(schema.lint_run.c.lint_run_id == run_id)
        .values(status="SUCCESS", completed_at=now)
    )
    return run_id
