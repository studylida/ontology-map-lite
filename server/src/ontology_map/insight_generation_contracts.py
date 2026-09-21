"""Strict runtime contracts for #68 NODE_INSIGHT generation.

The central analysis question is an Agent-generation contract, not a new product
DB field. Persisted report meaning remains in the approved node_insight and
section tables. The two time windows are one product-level atomic bundle even
when either window is an explicit normal empty result.
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
    model_config = ConfigDict(extra="forbid", frozen=True)
    node_id: PositiveId
    node_type: Nonblank
    preferred_alias: Nonblank | None = None


class ClaimConnection(BaseModel):
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
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: PositiveId
    statement_text: Nonblank
    modality: Modality
    period_role: PeriodRole
    connections: Annotated[tuple[ClaimConnection, ...], Field(min_length=1)]
    evidence: Annotated[tuple[EvidenceExcerpt, ...], Field(min_length=1)]


class VisibleConflictPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    conflict_set_id: PositiveId
    claim_ids: tuple[PositiveId, PositiveId]

    @model_validator(mode="after")
    def distinct_claims(self) -> Self:
        if self.claim_ids[0] == self.claim_ids[1]:
            raise ValueError("conflict pair claims must be distinct")
        return self


class InsightWindowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    time_window: TimeWindow
    claims: tuple[GroundedClaim, ...]
    conflict_pairs: tuple[VisibleConflictPair, ...] = ()

    @model_validator(mode="after")
    def unique_references(self) -> Self:
        claim_ids = [item.claim_id for item in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("window claim_id values must be unique")
        conflict_ids = [item.conflict_set_id for item in self.conflict_pairs]
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("window conflict_set_id values must be unique")
        allowed = set(claim_ids)
        if any(set(pair.claim_ids) - allowed for pair in self.conflict_pairs):
            raise ValueError("conflict pair members must exist in window claims")
        return self


class InsightAgentInput(BaseModel):
    """One provider input containing both approved report windows."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    node_id: PositiveId
    node_type: Nonblank
    preferred_alias: Nonblank | None = None
    as_of_at: datetime
    recent_90_days: InsightWindowInput
    recent_1_year: InsightWindowInput

    @model_validator(mode="after")
    def windows_match_fields(self) -> Self:
        if self.recent_90_days.time_window != "RECENT_90_DAYS":
            raise ValueError("recent_90_days must carry RECENT_90_DAYS")
        if self.recent_1_year.time_window != "RECENT_1_YEAR":
            raise ValueError("recent_1_year must carry RECENT_1_YEAR")
        return self


class InsightClaimReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    claim_id: PositiveId
    role: ClaimRole
    display_order: PositiveOrder


class InsightSectionCandidate(BaseModel):
    """One major finding proposed by the Agent.

    Cardinality and grounding rules are product validation rather than JSON
    parsing so a structurally readable but invalid report becomes
    VALIDATION_BLOCKED instead of an output-contract retry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    display_order: PositiveOrder
    title: Nonblank
    synthesis_text: Nonblank
    caveat_text: Nonblank | None = None
    claims: tuple[InsightClaimReference, ...]


class InsightReportCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    analysis_question_text: Nonblank
    title: Nonblank
    summary_text: Nonblank
    synthesis_text: Nonblank
    caveat_text: Nonblank
    sections: tuple[InsightSectionCandidate, ...]


class InsightWindowProposal(BaseModel):
    """`report=None` is the explicit normal empty result for one window."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    report: InsightReportCandidate | None


class InsightBundleProposal(BaseModel):
    """One Structured Output response for the 90-day + 1-year bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    recent_90_days: InsightWindowProposal
    recent_1_year: InsightWindowProposal


@dataclass(frozen=True)
class PreparedInsightBundle:
    promotion_batch_id: int
    node_search_document_id: int
    prior_insight_model_task_id: int | None
    basis_ids: tuple[int, ...]
    agent_input: InsightAgentInput


@dataclass(frozen=True)
class ReportFailure:
    time_window: TimeWindow
    reason: str


@dataclass(frozen=True)
class ValidatedInsightBundle:
    recent_90_days: InsightReportCandidate | None
    recent_1_year: InsightReportCandidate | None
    failures: tuple[ReportFailure, ...]

    @property
    def valid(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class InsightApplyResult:
    status: Literal["SUCCESS", "VALIDATION_BLOCKED"]
    report_ids: tuple[int | None, int | None]
    reason: str | None = None
