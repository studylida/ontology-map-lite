"""Runtime-only #128 contracts; none of these objects is a persisted result."""

from dataclasses import dataclass
from typing import Annotated, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveId = Annotated[int, Field(strict=True, gt=0)]
Nonblank = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
NodeTypeCode = Literal["COMPANY", "PERSON", "TECHNOLOGY", "EVENT", "TOPIC"]
ApprovedTopic = Literal[
    "반도체",
    "메모리 반도체",
    "첨단 패키징",
    "인공지능",
    "데이터센터",
    "제조 공정",
    "투자",
    "상용화",
    "규제·정책",
]
ResolutionStatus = Literal["SAME", "NEW", "UNRESOLVED"]
CANDIDATE_LIMIT = 5
APPROVED_TOPICS = frozenset(get_args(ApprovedTopic))


class ExternalIdentifier(BaseModel):
    """Input values must come from trusted prepared metadata, not model text."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    identifier_system: Literal["KRX", "WIKIDATA", "ORCID", "LEI"]
    identifier_value: Nonblank


class SourceRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_document_id: PositiveId
    start_char: Annotated[int, Field(strict=True, ge=0)]
    end_char: PositiveId

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("source range must be nonempty and ordered")
        return self


class EntityMention(BaseModel):
    """Document-local identity supplied by the validated extraction boundary.

    Repeated uses of one extracted entity must share mention_id. This module
    never unifies two local identities merely because their strings match.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    mention_id: Nonblank
    text: Nonblank
    node_type: NodeTypeCode
    approved_topic_name: ApprovedTopic | None = None
    source_ranges: Annotated[tuple[SourceRange, ...], Field(min_length=1)]
    external_identifiers: tuple[ExternalIdentifier, ...] = ()

    @model_validator(mode="after")
    def one_document(self) -> Self:
        if self.approved_topic_name is not None and self.node_type != "TOPIC":
            raise ValueError("approved_topic_name is only for a TOPIC mention")
        documents = {item.source_document_id for item in self.source_ranges}
        if len(documents) != 1:
            raise ValueError("one mention context must belong to one document")
        if len(set(self.source_ranges)) != len(self.source_ranges):
            raise ValueError("duplicate source ranges")
        if len(set(self.external_identifiers)) != len(self.external_identifiers):
            raise ValueError("duplicate external identifiers")
        return self


class NodeCandidate(BaseModel):
    """Only directly stored identity fields may cross the Agent boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    node_id: PositiveId
    node_type: NodeTypeCode
    preferred_alias: str | None
    aliases: tuple[str, ...]
    external_identifiers: tuple[ExternalIdentifier, ...]


class SourceContext(SourceRange):
    quote_text: Nonblank


class ResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mention_text: Nonblank
    node_type: NodeTypeCode
    context: Annotated[tuple[SourceContext, ...], Field(min_length=1)]
    candidates: Annotated[tuple[NodeCandidate, ...], Field(max_length=5)]
    candidates_truncated: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def unique_candidates(self) -> Self:
        ids = [item.node_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate node_id values must be unique")
        return self


class ResolutionProposal(BaseModel):
    """Strict Structured Output. No free-text reasoning or extra states."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: ResolutionStatus
    node_id: PositiveId | None

    @model_validator(mode="after")
    def selected_id(self) -> Self:
        if (self.decision == "SAME") != (self.node_id is not None):
            raise ValueError("only SAME must select a node_id")
        return self


class ResolutionBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mention_id: Nonblank
    input: ResolutionInput


class ResolutionBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mentions: Annotated[tuple[ResolutionBatchItem, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_mentions(self) -> Self:
        ids = [item.mention_id for item in self.mentions]
        if len(ids) != len(set(ids)):
            raise ValueError("mention_id values must be unique")
        return self


class ResolutionBatchDecision(ResolutionProposal):
    mention_id: Nonblank


class ResolutionBatchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    resolutions: tuple[ResolutionBatchDecision, ...]


@dataclass(frozen=True)
class VerifiedContext:
    source: SourceContext
    body_hash: bytes
    quote_hash: bytes
    language: str


@dataclass(frozen=True)
class NodeRecord:
    candidate: NodeCandidate
    node_type_id: int
    usable: bool


@dataclass(frozen=True)
class CandidateSet:
    nodes: tuple[NodeRecord, ...]
    truncated: bool


@dataclass(frozen=True)
class Resolution:
    """A proposal validated against a particular runtime input snapshot.

    NEW is permission to participate in promotion, not a saved Node. The
    promotion boundary still requires surviving validated knowledge.
    """

    mention: EntityMention
    decision: ResolutionStatus
    node_id: int | None
    context: tuple[VerifiedContext, ...]
    candidates: CandidateSet
    identifier_nodes: tuple[NodeRecord, ...]


@dataclass(frozen=True)
class ResolvableKnowledge:
    accepted: tuple[str, ...]
    excluded: tuple[str, ...]
    mention_ids: frozenset[str]


@dataclass(frozen=True)
class PromotionNodeBinding:
    node_id: int
    observation_ids: tuple[int, ...]
