"""Extraction task identity and pre-dispatch reference checks (#125/#127).

This is not an ontology approval policy, provider adapter or promotion worker.
Read actual reference rows; never seed missing contracts or persist input/output
payloads. All writes participate in the caller's transaction.
"""

import json
from dataclasses import dataclass
from hashlib import sha256
from unicodedata import normalize

import sqlalchemy as sa
from pydantic import JsonValue
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ontology_map import entity_resolution
from ontology_map.db import schema
from ontology_map.entity_resolution_contracts import ResolutionBatchProposal
from ontology_map.extraction import (
    CLAIM_REVIEW_PROMPT,
    GENERATION_PROMPT,
)
from ontology_map.extraction_contracts import (
    ClaimReviewBatch,
    Contract,
    KnowledgeProposals,
    Text,
)
from ontology_map.llm_config import request_identity_settings
from ontology_map.model_studio import FLASH

TASK_KIND = "KNOWLEDGE_EXTRACTION"
PROMPT_VERSION = "ke127-generation-" + sha256(GENERATION_PROMPT.encode()).hexdigest()


class ReferenceNotReady(ValueError):
    """A missing or mismatched reference is not a normal zero-result."""


class InputChanged(ValueError):
    """Reprocess with a new effective input; do not silently execute old work."""


class ExecutionInput(Contract):
    """Trusted application configuration, never provider output or credentials.

    Include all result-affecting limits/settings here. A manual reprocess after
    FINAL_FAILED uses an explicitly supplied new execution_generation; this
    module neither invents generations nor resets existing task history.
    """

    validator_version: Text
    runtime_settings: dict[str, JsonValue]
    corrective_input: Text | None = None
    execution_generation: Text | None = None


@dataclass(frozen=True)
class TaskIdentity:
    source_document_id: int
    input_hash: bytes
    cache_key: bytes
    output_schema_definition_id: int
    model_version: str = FLASH
    prompt_version: str = PROMPT_VERSION

    def values(self) -> dict[str, object]:
        return {
            "task_kind": TASK_KIND,
            "source_document_id": self.source_document_id,
            "input_hash": self.input_hash,
            "cache_key": self.cache_key,
            "output_schema_definition_id": self.output_schema_definition_id,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
        }


@dataclass(frozen=True)
class EnqueuedTask:
    task_id: int
    status: str
    created: bool
    identity: TaskIdentity


def _hash(value: object) -> bytes:
    # JSON, not repr/hash(): stable key order, no NaN or Infinity.
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(text.encode("utf-8")).digest()


def _document(session: Session, document_id: int) -> dict[str, object]:
    row = (
        session.execute(
            sa.select(
                schema.source_document.c.source_document_id,
                schema.source_document.c.source_key,
                schema.source_document.c.version_no,
                schema.source_document.c.normalized_body,
                schema.source_document.c.body_hash,
                schema.source_document.c.original_language,
            ).where(schema.source_document.c.source_document_id == document_id)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ReferenceNotReady("SOURCE_DOCUMENT_MISSING")
    body = str(row["normalized_body"])
    if normalize("NFC", body) != body or "\r" in body:
        raise ReferenceNotReady("SOURCE_NORMALIZATION_MISMATCH")
    body_hash = sha256(body.encode("utf-8")).digest()
    if body_hash != bytes(row["body_hash"]):
        raise ReferenceNotReady("SOURCE_HASH_MISMATCH")
    return {
        "id": document_id,
        "source_key": row["source_key"],
        "version_no": row["version_no"],
        "body_hash": body_hash.hex(),
        "language": row["original_language"],
    }


def _output_contract(session: Session) -> int:
    row = (
        session.execute(
            sa.select(schema.output_schema_definition).where(
                schema.output_schema_definition.c.task_kind == TASK_KIND,
                schema.output_schema_definition.c.is_active,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ReferenceNotReady("ACTIVE_EXTRACTION_CONTRACT_MISSING")
    if _hash(row["schema_json"]) != _hash(KnowledgeProposals.model_json_schema()):
        raise ReferenceNotReady("ACTIVE_EXTRACTION_CONTRACT_MISMATCH")
    return int(row["output_schema_definition_id"])


def _references(session: Session, validator_version: str) -> dict[str, object]:
    policy = (
        session.execute(
            sa.select(
                schema.lint_policy_version.c.lint_policy_version_id,
                schema.lint_policy_version.c.version_no,
                schema.lint_policy_version.c.validator_version,
            ).where(schema.lint_policy_version.c.is_active)
        )
        .mappings()
        .one_or_none()
    )
    if policy is None or policy["validator_version"] != validator_version:
        raise ReferenceNotReady("ACTIVE_VALIDATOR_CONTRACT_MISMATCH")
    # Capture actual active definitions, not assumed fixture IDs. This does not
    # approve them for generation; the owning ontology validator still must
    # enforce #126, including the unresolved bandwidth/event boundaries.
    queries = {
        "node_types": """
            SELECT node_type_id, node_type_code, display_name, creation_rule
            FROM node_type WHERE is_active ORDER BY node_type_id
        """,
        "relations": """
            SELECT r.*, t.relation_code FROM relation_type_revision r
            JOIN relation_type t USING (relation_type_id)
            WHERE r.is_active ORDER BY r.relation_type_revision_id
        """,
        "endpoints": """
            SELECT e.* FROM relation_endpoint_rule e
            JOIN relation_type_revision r USING (relation_type_revision_id)
            WHERE r.is_active ORDER BY e.relation_type_revision_id,
              e.source_node_type_id, e.target_node_type_id
        """,
        "attributes": """
            SELECT r.*, a.attribute_code FROM attribute_revision r
            JOIN attribute a USING (attribute_id)
            WHERE r.is_active ORDER BY r.attribute_revision_id
        """,
        "allowed_units": """
            SELECT u.attribute_revision_id, u.allowed_value_kind, u.unit_code
            FROM attribute_revision_allowed_unit u
            JOIN attribute_revision r USING (attribute_revision_id)
            WHERE r.is_active
            ORDER BY u.attribute_revision_id, u.unit_code
        """,
        "topics": """
            SELECT tr.node_id, tr.topic_code, tr.canonical_display_name,
              n.node_type_id, nt.node_type_code, k.lifecycle_kind
            FROM topic_reference tr
            JOIN node n ON n.node_id = tr.node_id
            JOIN node_type nt ON nt.node_type_id = n.node_type_id
            JOIN knowledge_item k ON k.knowledge_item_id = tr.node_id
            WHERE tr.is_active
              AND nt.node_type_code = 'TOPIC'
              AND k.item_kind = 'NODE'
              AND k.lifecycle_kind = 'PRODUCT_REFERENCE'
            ORDER BY tr.topic_code, tr.node_id
        """,
        "lint_rules": """
            SELECT p.lint_policy_rule_id, p.lint_policy_version_id,
              p.lint_rule_id, p.severity, r.rule_code, r.evaluation_scope
            FROM lint_policy_rule p JOIN lint_rule r USING (lint_rule_id)
            JOIN lint_policy_version v USING (lint_policy_version_id)
            WHERE v.is_active ORDER BY p.lint_policy_rule_id
        """,
    }
    result: dict[str, object] = {"lint_policy": dict(policy)}
    for name, query in queries.items():
        result[name] = [dict(row) for row in session.execute(sa.text(query)).mappings()]
    if not result["node_types"]:
        raise ReferenceNotReady("ACTIVE_NODE_TYPES_MISSING")
    return result


def capture_identity(
    session: Session, document_id: int, execution: ExecutionInput
) -> TaskIdentity:
    """Use a consistent read transaction; keep raw effective input in memory."""
    execution = ExecutionInput.model_validate_json(execution.model_dump_json())
    contract_id = _output_contract(session)
    effective_input = {
        "provider_execution": request_identity_settings(),
        "source": _document(session, document_id),
        "execution": execution.model_dump(mode="json"),
        "references": _references(session, execution.validator_version),
        "runtime_helpers": {
            "prompts": [
                CLAIM_REVIEW_PROMPT,
                entity_resolution.SYSTEM_PROMPT,
            ],
            "schemas": [
                model.model_json_schema()
                for model in (
                    ClaimReviewBatch,
                    ResolutionBatchProposal,
                )
            ],
        },
    }
    input_hash = _hash(effective_input)
    cache_key = _hash([TASK_KIND, input_hash.hex(), FLASH, PROMPT_VERSION, contract_id])
    return TaskIdentity(document_id, input_hash, cache_key, contract_id)


def _matches(row: RowMapping, identity: TaskIdentity) -> bool:
    return all(row[key] == value for key, value in identity.values().items())


def enqueue_extraction(
    session: Session, document_id: int, execution: ExecutionInput
) -> EnqueuedTask:
    """Idempotent creation only: never reset status, attempts, slots or leases."""
    identity = capture_identity(session, document_id, execution)
    new_id = session.execute(
        insert(schema.model_task)
        .values(**identity.values())
        .on_conflict_do_nothing(index_elements=[schema.model_task.c.cache_key])
        .returning(schema.model_task.c.model_task_id)
    ).scalar_one_or_none()
    row = (
        session.execute(
            sa.select(schema.model_task).where(
                schema.model_task.c.cache_key == identity.cache_key
            )
        )
        .mappings()
        .one()
    )
    if not _matches(row, identity):
        raise InputChanged("TASK_IDENTITY_MISMATCH")
    return EnqueuedTask(
        int(row["model_task_id"]), row["status"], new_id is not None, identity
    )


def require_current_input(
    session: Session, task_id: int, execution: ExecutionInput
) -> TaskIdentity:
    """Recheck before provider preflight/reservation, not inside provider IO.

    The runner separately checks its valid lease with model_tasks.require_lease.
    Changed inputs cannot overwrite the old task or restore cached model data.
    """
    row = (
        session.execute(
            sa.select(schema.model_task).where(
                schema.model_task.c.model_task_id == task_id
            )
        )
        .mappings()
        .one()
    )
    if row["task_kind"] != TASK_KIND or row["source_document_id"] is None:
        raise InputChanged("NOT_A_DOCUMENT_EXTRACTION_TASK")
    identity = capture_identity(session, int(row["source_document_id"]), execution)
    if not _matches(row, identity):
        raise InputChanged("EXTRACTION_INPUT_CHANGED")
    return identity
