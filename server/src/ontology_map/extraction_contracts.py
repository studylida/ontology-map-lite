"""Runtime knowledge proposals; none of these models is a persistence contract."""

from calendar import monthrange
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Preserve the same nonblank contract under search and full-string matching.
Text = Annotated[str, StringConstraints(pattern=r"^[\s\S]*\S[\s\S]*$")]
NodeType = Literal["COMPANY", "PERSON", "TECHNOLOGY", "EVENT", "TOPIC"]
Modality = Literal[
    "FACT", "PLAN_OR_TARGET", "PREDICTION_OR_ESTIMATE", "OPINION_OR_EVALUATION"
]
Verdict = Literal["TRUE", "FALSE", "UNRESOLVED"]
PublicationVerdict = Literal["PUBLISH", "EDITORIAL_INTERPRETATION", "UNRESOLVED"]
Precision = Literal["INSTANT", "DAY", "MONTH", "YEAR", "UNKNOWN"]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )


def digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class SourceSpan(Contract):
    source_id: Text
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: Text = Field(repr=False)
    quote_hash: str
    # This is a normalization group, not an inferred HTML paragraph/section.
    paragraph_id: str | None


def _check_span(span: SourceSpan, body: str, previous_end: int) -> None:
    if not previous_end <= span.start < span.end <= len(body):
        raise ValueError("SOURCE_OFFSET")
    if body[previous_end : span.start].strip():
        raise ValueError("SOURCE_PROJECTION_GAP")
    if body[span.start : span.end] != span.quote:
        raise ValueError("SOURCE_QUOTE")
    if digest(span.quote) != span.quote_hash:
        raise ValueError("SOURCE_QUOTE_HASH")


class SourceDocument(Contract):
    document_id: Text
    body: Text = Field(repr=False)
    body_hash: str
    sources: tuple[SourceSpan, ...] = Field(repr=False)

    @model_validator(mode="after")
    def validate_source(self) -> "SourceDocument":
        if normalize("NFC", self.body) != self.body or "\r" in self.body:
            raise ValueError("SOURCE_NORMALIZATION")
        if digest(self.body) != self.body_hash:
            raise ValueError("SOURCE_HASH")
        ids: set[str] = set()
        end = 0
        for span in self.sources:
            if span.source_id in ids:
                raise ValueError("SOURCE_ID_OR_ORDER")
            _check_span(span, self.body, end)
            ids.add(span.source_id)
            end = span.end
        if self.body[end:].strip():
            raise ValueError("SOURCE_PROJECTION_GAP")
        return self


class BodySelection(Contract):
    source_ids: list[Text]
    provider: Literal["typesafe"]
    model: Text
    policy_version: Literal["jev-source-safety-v2"]
    drop_threshold: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_sources(self) -> "BodySelection":
        if not self.source_ids or len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("SOURCE_SELECTION")
        return self


class Mention(Contract):
    mention_id: Text
    text: Text
    node_type: NodeType
    source_ids: list[Text]
    topic_name: Text | None

    @model_validator(mode="after")
    def validate_topic(self) -> "Mention":
        if (self.node_type == "TOPIC") != (self.topic_name is not None):
            raise ValueError("TOPIC_REFERENCE")
        return self


class TemporalPoint(Contract):
    value: datetime | None
    precision: Precision

    @model_validator(mode="before")
    @classmethod
    def normalize_time_string(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = data.get("value")
        if not isinstance(raw, str):
            return data
        precision = data.get("precision")
        if precision in ("DAY", "MONTH", "YEAR"):
            try:
                day = date.fromisoformat(raw)
            except ValueError:
                pass
            else:
                if day.isoformat() == raw:
                    return {
                        **data,
                        "value": _calendar_period_start(day, precision),
                    }
        try:
            instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return data
        if precision in ("DAY", "MONTH", "YEAR") and instant.utcoffset() is not None:
            instant = _calendar_period_start(instant.astimezone(UTC).date(), precision)
        return {**data, "value": instant}

    @model_validator(mode="after")
    def validate_time(self) -> "TemporalPoint":
        if (self.value is None) != (self.precision == "UNKNOWN"):
            raise ValueError("TIME_PRECISION")
        if self.value is not None and self.value.utcoffset() is None:
            raise ValueError("TIME_ZONE_REQUIRED")
        if self.value is not None and self.precision != "INSTANT":
            utc = self.value.astimezone(UTC)
            if utc.time().isoformat() != "00:00:00":
                raise ValueError("TIME_ANCHOR")
            _check_date_anchor(utc.date(), self.precision)
        return self


def _check_date_anchor(value: date, precision: str) -> None:
    if precision in ("MONTH", "YEAR") and value.day != 1:
        raise ValueError("DATE_ANCHOR")
    if precision == "YEAR" and value.month != 1:
        raise ValueError("DATE_ANCHOR")


def _calendar_period_start(value: date, precision: str) -> datetime:
    if precision == "MONTH":
        if value.day not in (1, monthrange(value.year, value.month)[1]):
            return datetime.combine(value, datetime.min.time(), UTC)
        value = value.replace(day=1)
    elif precision == "YEAR":
        if (value.month, value.day) not in ((1, 1), (12, 31)):
            return datetime.combine(value, datetime.min.time(), UTC)
        value = value.replace(month=1, day=1)
    return datetime.combine(value, datetime.min.time(), UTC)


def _last_date(value: date, precision: str) -> date:
    if precision == "YEAR":
        return date(value.year, 12, 31)
    if precision == "MONTH":
        return value.replace(day=monthrange(value.year, value.month)[1])
    return value


def _last_instant(point: TemporalPoint) -> datetime:
    if point.value is None:
        raise ValueError("UNKNOWN_TIME_BOUND")
    if point.precision == "INSTANT":
        return point.value
    last = _last_date(point.value.astimezone(UTC).date(), point.precision)
    return datetime.combine(last, datetime.max.time(), tzinfo=UTC)


class StringValue(Contract):
    kind: Literal["STRING"]
    value: Text


class NumberValue(Contract):
    kind: Literal["NUMBER"]
    value: Decimal = Field(allow_inf_nan=False)
    unit: Text


class BooleanValue(Contract):
    kind: Literal["BOOLEAN"]
    value: bool


class DateValue(Contract):
    kind: Literal["DATE"]
    value: date
    precision: Literal["DAY", "MONTH", "YEAR"]

    @model_validator(mode="after")
    def validate_anchor(self) -> "DateValue":
        _check_date_anchor(self.value, self.precision)
        return self


class PeriodValue(Contract):
    kind: Literal["PERIOD"]
    start: DateValue
    end: DateValue | None

    @model_validator(mode="after")
    def validate_period(self) -> "PeriodValue":
        if (
            self.end is not None
            and _last_date(self.end.value, self.end.precision) < self.start.value
        ):
            raise ValueError("REVERSED_PERIOD")
        return self


class RelationProposal(Contract):
    kind: Literal["RELATION"]
    binding_id: Text
    code: Text
    source_mention: Text
    target_mention: Text
    stance: Literal["SUPPORT", "DISPUTE"]


class AttributeProposal(Contract):
    kind: Literal["ATTRIBUTE"]
    binding_id: Text
    code: Text
    target_mention: Text
    value: StringValue | NumberValue | BooleanValue | DateValue | PeriodValue


class EventTimeProposal(Contract):
    kind: Literal["EVENT_TIME"]
    binding_id: Text
    event_mention: Text
    start: TemporalPoint
    end: TemporalPoint

    @model_validator(mode="after")
    def validate_extent(self) -> "EventTimeProposal":
        if self.start.value is None and self.end.value is None:
            raise ValueError("EMPTY_EVENT_TIME")
        if (
            self.start.value
            and self.end.value
            and _last_instant(self.end) < self.start.value
        ):
            raise ValueError("REVERSED_EVENT_TIME")
        return self


Binding = RelationProposal | AttributeProposal | EventTimeProposal


class ClaimProposal(Contract):
    candidate_id: Text
    statement: Text = Field(repr=False)
    modality: Modality
    source_ids: list[Text]
    mentions: list[Mention] = Field(repr=False)
    # Empty is allowed for generation evaluation, never for final acceptance.
    bindings: list[Binding] = Field(repr=False)


class KnowledgeProposals(Contract):
    claims: list[ClaimProposal] = Field(repr=False)


class ClaimSupport(Contract):
    verdict: Verdict


class BindingProjectionVerdict(Contract):
    binding_id: Text
    verdict: Verdict


class BindingProjectionSupport(Contract):
    bindings: list[BindingProjectionVerdict]


class ClaimReview(Contract):
    candidate_id: Text
    support_verdict: Verdict
    publication_verdict: PublicationVerdict
    bindings: list[BindingProjectionVerdict]


class ClaimReviewBatch(Contract):
    claims: list[ClaimReview]


class RelationRule(Contract):
    code: Text
    version_no: int = Field(gt=0)
    revision_id: int | None = Field(gt=0)
    description: Text
    direction: Literal["DIRECTED", "SYMMETRIC"]
    endpoints: tuple[tuple[NodeType, NodeType], ...]


class AttributeRule(Contract):
    code: Text
    version_no: int = Field(gt=0)
    revision_id: int | None = Field(gt=0)
    description: Text
    node_type: NodeType
    value_kind: Literal["STRING", "NUMBER", "BOOLEAN", "DATE", "PERIOD"]
    units: tuple[Text, ...]


class Ontology(Contract):
    """An explicit caller-approved snapshot, not a built-in seed policy."""

    node_types: tuple[NodeType, ...]
    relations: tuple[RelationRule, ...]
    attributes: tuple[AttributeRule, ...]
    topics: tuple[Text, ...]

    @model_validator(mode="after")
    def validate_codes(self) -> "Ontology":
        for rules in (self.relations, self.attributes):
            if len({r.code for r in rules}) != len(rules):
                raise ValueError("AMBIGUOUS_ONTOLOGY_CODE")
        return self
