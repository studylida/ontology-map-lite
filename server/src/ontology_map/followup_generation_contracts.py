"""Strict runtime contracts for #129 FOLLOWUP_QUESTIONS generation.

The models in this module describe approved Agent input/output only. They are
not persisted model responses, reasoning traces or an alternate result store.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveId = Annotated[int, Field(strict=True, gt=0)]
PositiveOrder = Annotated[int, Field(strict=True, gt=0)]
Nonblank = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]

TimeWindow = Literal["RECENT_90_DAYS", "RECENT_1_YEAR"]
PeriodRole = Literal["IN_WINDOW", "BACKGROUND", "UNKNOWN"]
ClaimRole = Literal["KEY_CLAIM", "SUPPORTING_CLAIM", "CONTRASTING_CLAIM"]
Modality = Literal[
    "FACT",
    "PLAN_OR_TARGET",
    "PREDICTION_OR_ESTIMATE",
    "OPINION_OR_EVALUATION",
]
ConnectionKind = Literal["RELATION", "ATTRIBUTE", "EVENT_TIME"]
RelationStance = Literal["SUPPORT", "DISPUTE"]


class RelatedNode(BaseModel):
    """Minimum identity for the opposite endpoint of a direct relation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    node_id: PositiveId
    node_type: Nonblank
    preferred_alias: Nonblank | None = None


class ClaimConnection(BaseModel):
    """Why a Claim is directly in the selected node's semantic scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: ConnectionKind
    label: Nonblank
    stance: RelationStance | None = None
    related_node: RelatedNode | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.kind == "RELATION":
            if self.stance is None or self.related_node is None:
                raise ValueError("relation connections require stance and related_node")
        elif self.stance is not None or self.related_node is not None:
            raise ValueError("only relation connections may carry stance/related_node")
        return self


class EvidenceExcerpt(BaseModel):
    """Direct Claim evidence supplied to the Agent; URLs are intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_id: PositiveId
    source_document_id: PositiveId
    evidence_group_id: PositiveId
    publisher_name: Nonblank
    title: Nonblank
    quote_text: Nonblank
    published_at: datetime | None
    period_role: PeriodRole


class GroundedClaim(BaseModel):
    """A public, directly in-scope Claim that the Agent may cite by id."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: PositiveId
    statement_text: Nonblank
    modality: Modality
    period_role: PeriodRole
    connections: Annotated[tuple[ClaimConnection, ...], Field(min_length=1)]
    evidence: Annotated[tuple[EvidenceExcerpt, ...], Field(min_length=1)]


class VisibleConflictPair(BaseModel):
    """A pre-existing #130 conflict pair; no generated summary or winner is included."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    conflict_set_id: PositiveId
    claim_ids: tuple[PositiveId, PositiveId]

    @model_validator(mode="after")
    def distinct_claims(self) -> Self:
        if self.claim_ids[0] == self.claim_ids[1]:
            raise ValueError("conflict pair claims must be distinct")
        return self


class FollowupWindowInput(BaseModel):
    """The bounded Claim input for one follow-up time window."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    time_window: TimeWindow
    claims: tuple[GroundedClaim, ...]
    conflict_pairs: tuple[VisibleConflictPair, ...] = ()

    @model_validator(mode="after")
    def unique_references(self) -> Self:
        claim_ids = [item.claim_id for item in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Agent input claim_id values must be unique")
        conflict_ids = [item.conflict_set_id for item in self.conflict_pairs]
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("Agent input conflict_set_id values must be unique")
        allowed = set(claim_ids)
        if any(set(pair.claim_ids) - allowed for pair in self.conflict_pairs):
            raise ValueError("conflict pair members must exist in Agent input claims")
        return self


class FollowupAgentInput(BaseModel):
    """One provider input containing both approved follow-up windows."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    node_id: PositiveId
    node_type: Nonblank
    preferred_alias: Nonblank | None = None
    as_of_at: datetime
    recent_90_days: FollowupWindowInput
    recent_1_year: FollowupWindowInput

    @model_validator(mode="after")
    def windows_match_fields(self) -> Self:
        if self.recent_90_days.time_window != "RECENT_90_DAYS":
            raise ValueError("recent_90_days must carry RECENT_90_DAYS")
        if self.recent_1_year.time_window != "RECENT_1_YEAR":
            raise ValueError("recent_1_year must carry RECENT_1_YEAR")
        return self


class FollowupClaimReference(BaseModel):
    """Agent-proposed evidence role for one candidate question."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: PositiveId
    role: ClaimRole
    display_order: PositiveOrder


class FollowupQuestionCandidate(BaseModel):
    """One atomic candidate; invalid grounding excludes the complete question."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    display_order: PositiveOrder
    question_text: Nonblank
    answer_text: Nonblank
    caveat_text: Nonblank | None = None
    claims: tuple[FollowupClaimReference, ...]


class FollowupWindowProposal(BaseModel):
    """Questions for one time window; an empty tuple is a normal result."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    questions: Annotated[tuple[FollowupQuestionCandidate, ...], Field(max_length=8)]


class FollowupQuestionsProposal(BaseModel):
    """One Structured Output response for the 90-day + 1-year bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    recent_90_days: FollowupWindowProposal
    recent_1_year: FollowupWindowProposal


@dataclass(frozen=True)
class PreparedFollowup:
    """Validated runtime snapshot used before and after the provider call."""

    promotion_batch_id: int
    node_context_id: int
    node_search_document_id: int
    basis_ids: tuple[int, ...]
    agent_input: FollowupAgentInput


@dataclass(frozen=True)
class CandidateFailure:
    display_order: int
    reason: str


@dataclass(frozen=True)
class ValidatedFollowupWindow:
    candidates: tuple[FollowupQuestionCandidate, ...]
    failures: tuple[CandidateFailure, ...]
    had_candidates: bool


@dataclass(frozen=True)
class ValidatedFollowup:
    recent_90_days: ValidatedFollowupWindow
    recent_1_year: ValidatedFollowupWindow


@dataclass(frozen=True)
class FollowupApplyResult:
    status: Literal["SUCCESS", "VALIDATION_BLOCKED"]
    question_set_ids: tuple[int, int] | None
    stored_count: int
    reason: str | None = None
