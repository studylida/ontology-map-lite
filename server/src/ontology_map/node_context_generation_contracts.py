"""Strict provider-independent contracts for #215 NODE_CONTEXT generation."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

PositiveId = Annotated[int, Field(strict=True, gt=0)]
Nonblank = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]


class NodeContextAgentInput(BaseModel):
    """Bounded deterministic input for one publication generation's context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: PositiveId
    node_type: Nonblank
    preferred_alias: Nonblank | None = None
    identity_text: Nonblank
    knowledge_text: Nonblank
    basis_ids: tuple[PositiveId, ...]


class NodeContextProposal(BaseModel):
    """The only generated product payload owned by NODE_CONTEXT."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_text: Nonblank


@dataclass(frozen=True)
class PreparedNodeContext:
    promotion_batch_id: int
    node_search_document_id: int
    search_document_input_hash: bytes
    input_hash: bytes
    agent_input: NodeContextAgentInput


@dataclass(frozen=True)
class NodeContextApplyResult:
    status: Literal["SUCCESS", "VALIDATION_BLOCKED"]
    node_context_id: int | None
    reason: str | None = None
