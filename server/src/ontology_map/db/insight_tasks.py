"""Durable NODE_INSIGHT task identity and enqueue boundary (#68 C2).

One durable row owns the complete 90-day + 1-year bundle. The row stores only
hashes and contract references; the exact PREPARING publication input is rebuilt
before provider execution and raw/generated provider payloads are never stored.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from ontology_map import insight_execution
from ontology_map import insight_generation as product
from ontology_map.db import insight_generation as insight_db
from ontology_map.db import schema
from ontology_map.insight_generation_contracts import PreparedInsightBundle

TASK_KIND = "NODE_INSIGHT"


class ReferenceNotReady(ValueError):
    """The approved active output contract is missing or does not match #68."""


class InputChanged(ValueError):
    """The exact logical input no longer matches this durable task."""


@dataclass(frozen=True)
class TaskIdentity:
    promotion_batch_id: int
    node_id: int
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
class EnqueuedInsight:
    task_id: int
    status: str
    created: bool
    identity: TaskIdentity
    prepared: PreparedInsightBundle


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
        raise ReferenceNotReady("ACTIVE_INSIGHT_CONTRACT_MISSING")
    if row["schema_json"] != product.output_schema():
        raise ReferenceNotReady("ACTIVE_INSIGHT_CONTRACT_MISMATCH")
    return int(row["output_schema_definition_id"]), int(row["version_no"])


def _effective_input(prepared: PreparedInsightBundle) -> dict[str, object]:
    return {
        "promotion_batch_id": prepared.promotion_batch_id,
        "node_search_document_id": prepared.node_search_document_id,
        "basis_ids": list(prepared.basis_ids),
        "agent_input": prepared.agent_input.model_dump(mode="json"),
        "execution_settings": insight_execution.identity_settings(),
    }


def capture_identity(
    session: Session,
    promotion_batch_id: int,
    node_id: int,
    as_of_at: datetime,
) -> tuple[TaskIdentity, PreparedInsightBundle]:
    """Capture one exact 90-day + 1-year #68 bundle in the caller transaction."""
    if as_of_at.utcoffset() is None:
        raise ValueError("AS_OF_REQUIRES_TIMEZONE")
    prepared = insight_db.prepare_insight_bundle(
        session,
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
        as_of_at=as_of_at,
    )
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
        node_id=prepared.agent_input.node_id,
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


def enqueue_insight(
    session: Session,
    promotion_batch_id: int,
    node_id: int,
    as_of_at: datetime,
) -> EnqueuedInsight:
    """Idempotently ensure one durable task for the entire approved bundle."""
    identity, prepared = capture_identity(
        session,
        promotion_batch_id,
        node_id,
        as_of_at,
    )
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
        raise InputChanged("INSIGHT_TASK_IDENTITY_MISMATCH")
    return EnqueuedInsight(
        task_id=int(row["model_task_id"]),
        status=str(row["status"]),
        created=new_id is not None,
        identity=identity,
        prepared=prepared,
    )


def require_current_input(
    session: Session,
    task_id: int,
    promotion_batch_id: int,
    node_id: int,
    as_of_at: datetime,
) -> PreparedInsightBundle:
    """Rebuild and compare the whole bundle immediately before provider preflight."""
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
        raise InputChanged("NOT_AN_INSIGHT_TASK")
    identity, prepared = capture_identity(
        session,
        promotion_batch_id,
        node_id,
        as_of_at,
    )
    if not _matches(row, identity):
        raise InputChanged("INSIGHT_INPUT_CHANGED")
    return prepared
