"""Durable FOLLOWUP_QUESTIONS task identity and enqueue boundary (#129).

The durable row stores hashes and contract references only. The effective input
is rebuilt from the exact PREPARING publication/context and compared before a
provider call; generated prose and provider payloads are never persisted here.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ontology_map import followup_execution
from ontology_map import followup_generation as product
from ontology_map.db import followup_generation as followup_db
from ontology_map.db import schema
from ontology_map.followup_generation_contracts import PreparedFollowup

TASK_KIND = "FOLLOWUP_QUESTIONS"


class ReferenceNotReady(ValueError):
    """The approved active output contract is missing or does not match #129."""


class InputChanged(ValueError):
    """The exact logical input no longer matches this durable task."""


@dataclass(frozen=True)
class TaskIdentity:
    promotion_batch_id: int
    node_context_id: int
    node_search_document_id: int
    input_hash: bytes
    cache_key: bytes
    output_schema_definition_id: int
    output_schema_version: int
    model_version: str = product.MODEL_VERSION
    prompt_version: str = product.PROMPT_VERSION

    def values(self) -> dict[str, object]:
        return {
            "task_kind": TASK_KIND,
            "source_document_id": None,
            "input_hash": self.input_hash,
            "cache_key": self.cache_key,
            "output_schema_definition_id": self.output_schema_definition_id,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
        }


@dataclass(frozen=True)
class EnqueuedFollowup:
    task_id: int
    status: str
    created: bool
    identity: TaskIdentity
    prepared: PreparedFollowup


def _hash(value: object) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(text.encode("utf-8")).digest()


def _output_contract(session: Session) -> tuple[int, int]:
    row = (
        session.execute(
            sa.select(
                schema.output_schema_definition.c.output_schema_definition_id,
                schema.output_schema_definition.c.version_no,
                schema.output_schema_definition.c.schema_json,
            ).where(
                schema.output_schema_definition.c.task_kind == TASK_KIND,
                schema.output_schema_definition.c.is_active,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ReferenceNotReady("ACTIVE_FOLLOWUP_CONTRACT_MISSING")
    if row["schema_json"] != product.output_schema():
        raise ReferenceNotReady("ACTIVE_FOLLOWUP_CONTRACT_MISMATCH")
    return int(row["output_schema_definition_id"]), int(row["version_no"])


def _effective_input(prepared: PreparedFollowup) -> dict[str, object]:
    return {
        "promotion_batch_id": prepared.promotion_batch_id,
        "node_context_id": prepared.node_context_id,
        "node_search_document_id": prepared.node_search_document_id,
        "basis_ids": list(prepared.basis_ids),
        "agent_input": prepared.agent_input.model_dump(mode="json"),
        "execution_settings": followup_execution.identity_settings(),
    }


def capture_identity(
    session: Session,
    node_context_id: int,
    as_of_at: datetime,
) -> tuple[TaskIdentity, PreparedFollowup]:
    """Capture the exact approved #129 product input in the caller transaction."""
    if as_of_at.utcoffset() is None:
        raise ValueError("AS_OF_REQUIRES_TIMEZONE")
    prepared = followup_db.prepare_followup(session, node_context_id, as_of_at)
    contract_id, contract_version = _output_contract(session)
    input_hash = _hash(_effective_input(prepared))
    cache_key = _hash(
        [
            TASK_KIND,
            input_hash.hex(),
            product.MODEL_VERSION,
            product.PROMPT_VERSION,
            contract_id,
            contract_version,
        ]
    )
    identity = TaskIdentity(
        promotion_batch_id=prepared.promotion_batch_id,
        node_context_id=prepared.node_context_id,
        node_search_document_id=prepared.node_search_document_id,
        input_hash=input_hash,
        cache_key=cache_key,
        output_schema_definition_id=contract_id,
        output_schema_version=contract_version,
    )
    return identity, prepared


def _matches(row: RowMapping, identity: TaskIdentity) -> bool:
    return row["source_document_id"] is None and all(
        row[key] == value for key, value in identity.values().items()
    )


def enqueue_followup(
    session: Session,
    node_context_id: int,
    as_of_at: datetime,
) -> EnqueuedFollowup:
    """Idempotently ensure one task for both exact Node/generation windows."""
    identity, prepared = capture_identity(session, node_context_id, as_of_at)
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
        raise InputChanged("FOLLOWUP_TASK_IDENTITY_MISMATCH")
    return EnqueuedFollowup(
        task_id=int(row["model_task_id"]),
        status=str(row["status"]),
        created=new_id is not None,
        identity=identity,
        prepared=prepared,
    )


def require_current_input(
    session: Session,
    task_id: int,
    node_context_id: int,
    as_of_at: datetime,
) -> PreparedFollowup:
    """Rebuild and compare the effective input immediately before provider preflight."""
    row = (
        session.execute(
            sa.select(schema.model_task).where(
                schema.model_task.c.model_task_id == task_id
            )
        )
        .mappings()
        .one()
    )
    if row["task_kind"] != TASK_KIND:
        raise InputChanged("NOT_A_FOLLOWUP_TASK")
    identity, prepared = capture_identity(session, node_context_id, as_of_at)
    if not _matches(row, identity):
        raise InputChanged("FOLLOWUP_INPUT_CHANGED")
    return prepared
