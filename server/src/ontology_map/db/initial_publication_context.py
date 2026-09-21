"""Generation-scoped NODE_CONTEXT persistence boundary for issue #215."""

import json
from datetime import datetime
from hashlib import sha256
from struct import pack

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import node_context_execution
from ontology_map import node_context_generation as context_product
from ontology_map.db import schema as s
from ontology_map.db.initial_publication_contracts import (
    InitialPublicationError,
    NodeContextTaskError,
    NodeContextTaskRef,
    PublicationStateError,
)
from ontology_map.db.initial_publication_search import (
    SEARCH_DOCUMENT_GENERATOR_VERSION,
    build_search_document_snapshot,
    publication_visible_preferred_alias,
)
from ontology_map.node_context_generation_contracts import (
    NodeContextAgentInput,
    NodeContextApplyResult,
    NodeContextProposal,
    PreparedNodeContext,
)


def _frame_text(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return pack(">Q", len(encoded)) + encoded


def _task_cache_key(
    task_kind: str,
    input_hash: bytes,
    output_contract_id: int,
    model_version: str,
    prompt_version: str,
) -> bytes:
    payload = (
        b"TASK1"
        + _frame_text(task_kind)
        + input_hash
        + pack(">q", output_contract_id)
        + _frame_text(model_version)
        + _frame_text(prompt_version)
    )
    return sha256(payload).digest()


def _canonical_hash(value: object) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(text.encode("utf-8")).digest()


def _node_context_effective_input(
    *,
    promotion_batch_id: int,
    node_search_document_id: int,
    search_document_input_hash: bytes,
    agent_input: NodeContextAgentInput,
) -> dict[str, object]:
    """Return the exact result-affecting input for one publication generation."""
    return {
        "promotion_batch_id": promotion_batch_id,
        "node_id": agent_input.node_id,
        "search_document": {
            "node_search_document_id": node_search_document_id,
            "input_hash": search_document_input_hash.hex(),
            "generator_version": SEARCH_DOCUMENT_GENERATOR_VERSION,
        },
        "agent_input": agent_input.model_dump(mode="json"),
        "execution_settings": node_context_execution.identity_settings(),
    }


def _node_context_input_hash(
    *,
    promotion_batch_id: int,
    node_search_document_id: int,
    search_document_input_hash: bytes,
    agent_input: NodeContextAgentInput,
) -> bytes:
    return _canonical_hash(
        _node_context_effective_input(
            promotion_batch_id=promotion_batch_id,
            node_search_document_id=node_search_document_id,
            search_document_input_hash=search_document_input_hash,
            agent_input=agent_input,
        )
    )


def prepare_node_context(
    session: Session,
    *,
    promotion_batch_id: int,
    node_id: int,
) -> PreparedNodeContext:
    """Build exact context input from the selected deterministic search artifact."""
    publication = (
        session.execute(
            sa.text(
                """
                SELECT pan.node_search_document_id,
                       pb.promotion_status, pb.publication_status
                FROM publication_affected_node pan
                JOIN promotion_batch pb
                  ON pb.promotion_batch_id = pan.promotion_batch_id
                WHERE pan.promotion_batch_id = :batch_id
                  AND pan.node_id = :node_id
                """
            ),
            {"batch_id": promotion_batch_id, "node_id": node_id},
        )
        .mappings()
        .one_or_none()
    )
    if (
        publication is None
        or publication["promotion_status"] != "COMMITTED"
        or publication["publication_status"] != "PREPARING"
        or publication["node_search_document_id"] is None
    ):
        raise NodeContextTaskError(
            "NODE_CONTEXT requires a selected PREPARING search document"
        )

    document_id = int(publication["node_search_document_id"])
    document = (
        session.execute(
            sa.text(
                """
                SELECT node_id, identity_text, knowledge_text, input_hash,
                       generator_version
                FROM node_search_document
                WHERE node_search_document_id = :document_id
                """
            ),
            {"document_id": document_id},
        )
        .mappings()
        .one()
    )
    if (
        int(document["node_id"]) != node_id
        or str(document["generator_version"]) != SEARCH_DOCUMENT_GENERATOR_VERSION
    ):
        raise NodeContextTaskError("selected search document is not the #215 document")

    current = build_search_document_snapshot(
        session,
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
    )
    selected_basis = tuple(
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
    if (
        bytes(document["input_hash"]) != current.input_hash
        or str(document["identity_text"]) != current.identity_text
        or str(document["knowledge_text"]) != current.knowledge_text
        or selected_basis != current.basis_ids
    ):
        raise NodeContextTaskError("selected search-document basis is stale")

    node_type = session.execute(
        sa.text(
            """
            SELECT nt.node_type_code
            FROM node n
            JOIN node_type nt ON nt.node_type_id = n.node_type_id
            WHERE n.node_id = :node_id
            """
        ),
        {"node_id": node_id},
    ).scalar_one()
    preferred_alias = publication_visible_preferred_alias(
        session,
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
    )
    search_hash = bytes(document["input_hash"])
    agent_input = NodeContextAgentInput(
        node_id=node_id,
        node_type=str(node_type),
        preferred_alias=preferred_alias,
        identity_text=current.identity_text,
        knowledge_text=current.knowledge_text,
        basis_ids=current.basis_ids,
    )
    return PreparedNodeContext(
        promotion_batch_id=promotion_batch_id,
        node_search_document_id=document_id,
        search_document_input_hash=search_hash,
        input_hash=_node_context_input_hash(
            promotion_batch_id=promotion_batch_id,
            node_search_document_id=document_id,
            search_document_input_hash=search_hash,
            agent_input=agent_input,
        ),
        agent_input=agent_input,
    )


def _active_context_contract_id(session: Session) -> int:
    row = (
        session.execute(
            sa.text(
                """
                SELECT output_schema_definition_id, schema_json
                FROM output_schema_definition
                WHERE task_kind = 'NODE_CONTEXT' AND is_active
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["schema_json"] != context_product.output_schema():
        raise NodeContextTaskError(
            "active NODE_CONTEXT output contract is missing or stale"
        )
    return int(row["output_schema_definition_id"])


def ensure_node_context_task(
    session: Session,
    prepared: PreparedNodeContext,
) -> NodeContextTaskRef:
    """Enqueue/dedupe one logical task without executing a provider call."""
    contract_id = _active_context_contract_id(session)
    cache_key = _task_cache_key(
        "NODE_CONTEXT",
        prepared.input_hash,
        contract_id,
        context_product.MODEL_VERSION,
        context_product.PROMPT_VERSION,
    )
    inserted = (
        session.execute(
            sa.text(
                """
                INSERT INTO model_task (
                    task_kind, source_document_id, input_hash,
                    output_schema_definition_id, model_version, prompt_version,
                    cache_key
                ) VALUES (
                    'NODE_CONTEXT', NULL, :input_hash, :contract_id,
                    :model_version, :prompt_version, :cache_key
                )
                ON CONFLICT (cache_key) DO NOTHING
                RETURNING model_task_id, status
                """
            ),
            {
                "input_hash": prepared.input_hash,
                "contract_id": contract_id,
                "model_version": context_product.MODEL_VERSION,
                "prompt_version": context_product.PROMPT_VERSION,
                "cache_key": cache_key,
            },
        )
        .mappings()
        .one_or_none()
    )
    if inserted is not None:
        return NodeContextTaskRef(
            model_task_id=int(inserted["model_task_id"]),
            status=str(inserted["status"]),
        )

    existing = (
        session.execute(
            sa.text(
                """
                SELECT model_task_id, task_kind, source_document_id, input_hash,
                       output_schema_definition_id, model_version, prompt_version,
                       cache_key, status
                FROM model_task
                WHERE cache_key = :cache_key
                """
            ),
            {"cache_key": cache_key},
        )
        .mappings()
        .one()
    )
    if (
        existing["task_kind"] != "NODE_CONTEXT"
        or existing["source_document_id"] is not None
        or bytes(existing["input_hash"]) != prepared.input_hash
        or int(existing["output_schema_definition_id"]) != contract_id
        or existing["model_version"] != context_product.MODEL_VERSION
        or existing["prompt_version"] != context_product.PROMPT_VERSION
        or bytes(existing["cache_key"]) != cache_key
    ):
        raise NodeContextTaskError("NODE_CONTEXT cache identity collision")
    return NodeContextTaskRef(
        model_task_id=int(existing["model_task_id"]),
        status=str(existing["status"]),
    )


def _lock_publication_node(
    session: Session,
    promotion_batch_id: int,
    node_id: int,
) -> dict[str, object]:
    row = (
        session.execute(
            sa.text(
                """
                SELECT pan.node_context_id, pb.promotion_status,
                       pb.publication_status
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
        raise PublicationStateError("publication node is not PREPARING")
    return dict(row)


def _finish_context_task(
    session: Session,
    model_task_id: int,
    *,
    status: str,
    finished_at: datetime,
) -> None:
    updated = session.scalar(
        s.model_task.update()
        .where(
            s.model_task.c.model_task_id == model_task_id,
            s.model_task.c.status == "RUNNING",
        )
        .values(
            status=status,
            next_attempt_at=None,
            lease_owner=None,
            lease_expires_at=None,
            finished_at=finished_at,
        )
        .returning(s.model_task.c.model_task_id)
    )
    if updated is None:
        raise NodeContextTaskError("NODE_CONTEXT task changed during finalization")


def apply_node_context(
    session: Session,
    *,
    model_task_id: int,
    prepared: PreparedNodeContext,
    proposal: NodeContextProposal,
    finished_at: datetime,
) -> NodeContextApplyResult:
    """Revalidate, store immutable context and finish one task atomically."""
    task = (
        session.execute(
            sa.text(
                """
                SELECT t.task_kind, t.input_hash, t.model_version, t.prompt_version,
                       t.status, o.task_kind AS schema_task_kind, o.schema_json
                FROM model_task t
                JOIN output_schema_definition o
                  ON o.output_schema_definition_id = t.output_schema_definition_id
                WHERE t.model_task_id = :task_id
                FOR UPDATE OF t
                """
            ),
            {"task_id": model_task_id},
        )
        .mappings()
        .one_or_none()
    )
    if task is None:
        raise NodeContextTaskError("NODE_CONTEXT model_task does not exist")
    if (
        task["task_kind"] != "NODE_CONTEXT"
        or task["schema_task_kind"] != "NODE_CONTEXT"
        or task["model_version"] != context_product.MODEL_VERSION
        or task["prompt_version"] != context_product.PROMPT_VERSION
        or task["schema_json"] != context_product.output_schema()
        or bytes(task["input_hash"]) != prepared.input_hash
    ):
        raise NodeContextTaskError("model_task does not match the #215 contract")
    if task["status"] != "RUNNING":
        raise NodeContextTaskError(
            "model_task must be RUNNING before NODE_CONTEXT finalization"
        )

    try:
        current = prepare_node_context(
            session,
            promotion_batch_id=prepared.promotion_batch_id,
            node_id=prepared.agent_input.node_id,
        )
    except InitialPublicationError:
        current = None
    if current != prepared:
        _finish_context_task(
            session,
            model_task_id,
            status="VALIDATION_BLOCKED",
            finished_at=finished_at,
        )
        return NodeContextApplyResult(
            status="VALIDATION_BLOCKED",
            node_context_id=None,
            reason="STALE_PUBLICATION",
        )

    publication = _lock_publication_node(
        session,
        prepared.promotion_batch_id,
        prepared.agent_input.node_id,
    )
    if publication["node_context_id"] is not None:
        raise NodeContextTaskError("publication already has a NODE_CONTEXT result")

    context_id = session.scalar(
        s.node_context.insert()
        .values(
            node_id=prepared.agent_input.node_id,
            node_search_document_id=prepared.node_search_document_id,
            model_task_id=model_task_id,
            language="ko",
            context_text=proposal.context_text.strip(),
        )
        .returning(s.node_context.c.node_context_id)
    )
    if context_id is None:
        raise NodeContextTaskError("node_context insert returned no identity")

    updated = session.scalar(
        s.publication_affected_node.update()
        .where(
            s.publication_affected_node.c.promotion_batch_id
            == prepared.promotion_batch_id,
            s.publication_affected_node.c.node_id == prepared.agent_input.node_id,
            s.publication_affected_node.c.node_context_id.is_(None),
        )
        .values(node_context_id=context_id)
        .returning(s.publication_affected_node.c.node_id)
    )
    if updated is None:
        raise NodeContextTaskError("publication context pointer changed concurrently")

    _finish_context_task(
        session,
        model_task_id,
        status="SUCCESS",
        finished_at=finished_at,
    )
    return NodeContextApplyResult(
        status="SUCCESS",
        node_context_id=int(context_id),
    )


__all__ = [
    "apply_node_context",
    "ensure_node_context_task",
    "prepare_node_context",
]
