"""B-3 product finalization for KNOWLEDGE_EXTRACTION (#127).

Runtime extraction and #128 resolution stay in memory. Only the final short
caller-owned promotion transaction writes canonical knowledge, records the
official #216 provenance for canonical associations, marks the promotion
COMMITTED, and finishes the model_task. Provider/model IO never runs here.
"""

from __future__ import annotations

import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Mapping, Sequence, cast

import httpx
import sqlalchemy as sa
from openai import APITimeoutError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ontology_map import entity_resolution as er
from ontology_map.db import extraction_promotion as promotion_db
from ontology_map.db import extraction_tasks as inputs
from ontology_map.db import model_tasks as tasks
from ontology_map.db import promotion_provenance as provenance
from ontology_map.db import schema
from ontology_map.entity_resolution_contracts import (
    ApprovedTopic,
    EntityMention,
    PromotionNodeBinding,
    Resolution,
    ResolutionBatchInput,
    ResolutionBatchProposal,
    SourceRange,
)
from ontology_map.extraction import ExtractionResult, binding_mentions
from ontology_map.extraction_contracts import (
    AttributeProposal,
    AttributeRule,
    Binding,
    ClaimProposal,
    DateValue,
    EventTimeProposal,
    Mention,
    NumberValue,
    PeriodValue,
    RelationProposal,
    RelationRule,
    SourceSpan,
)
from ontology_map.extraction_runner import (
    RunnerResult,
    RuntimeInput,
    classify_provider_error,
)
from ontology_map.llm_pacing import provider_lease
from ontology_map.model_studio import CallLimits, ModelStudio

ResolutionProposer = Callable[[ResolutionBatchInput], object]
ProductDisposition = Literal[
    "SUCCESS",
    "VALIDATION_BLOCKED",
    "RETRY_WAIT",
    "FINAL_FAILED",
    "LEASE_LOST",
]


@dataclass(frozen=True)
class ProductResult:
    task_status: str
    disposition: ProductDisposition
    promotion_batch_id: int | None = None
    accepted_claims: tuple[str, ...] = ()
    excluded_claims: tuple[str, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class _ResolvedRelation:
    relation_id: int
    stance: str
    created: bool


@dataclass(frozen=True)
class _ResolvedEvent:
    event_node_id: int
    start_at: datetime | None
    end_at: datetime | None
    start_precision: str
    end_precision: str
    node_is_new: bool


def _status(engine: Engine, task_id: int) -> str:
    with engine.connect() as connection:
        return str(
            connection.scalar(
                sa.select(schema.model_task.c.status).where(
                    schema.model_task.c.model_task_id == task_id
                )
            )
        )


def _runtime_identity_matches(
    execution: inputs.ExecutionInput, runtime: RuntimeInput
) -> None:
    expected = execution.runtime_settings.get("extraction_runner")
    actual = runtime.identity_settings()

    def canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    if canonical(expected) != canonical(actual):
        raise inputs.InputChanged("RUNNER_SETTINGS_MISMATCH")


def _span_map(runtime: RuntimeInput) -> dict[str, SourceSpan]:
    result = {span.source_id: span for span in runtime.document.sources}
    if len(result) != len(runtime.document.sources):
        raise ValueError("DUPLICATE_RUNTIME_SOURCE_ID")
    return result


def _document_id(runtime: RuntimeInput) -> int:
    try:
        value = int(runtime.document.document_id)
    except ValueError, TypeError:
        raise ValueError("SOURCE_DOCUMENT_ID_NOT_INTERNAL_ID") from None
    if value <= 0 or str(value) != runtime.document.document_id:
        raise ValueError("SOURCE_DOCUMENT_ID_NOT_INTERNAL_ID")
    return value


def _mention_contract(
    mention: Mention,
    spans: Mapping[str, SourceSpan],
    document_id: int,
) -> EntityMention:
    ranges: list[SourceRange] = []
    for source_id in mention.source_ids:
        span = spans.get(source_id)
        if span is None:
            raise ValueError("MENTION_SOURCE_OUTSIDE_RUNTIME_DOCUMENT")
        ranges.append(
            SourceRange(
                source_document_id=document_id,
                start_char=span.start,
                end_char=span.end,
            )
        )
    return EntityMention(
        mention_id=mention.mention_id,
        text=mention.text,
        node_type=mention.node_type,
        approved_topic_name=cast(ApprovedTopic | None, mention.topic_name),
        source_ranges=tuple(ranges),
        # Generated extraction has no trusted external-id field. Persistent
        # node IDs can only come back through #128's validated candidate set.
        external_identifiers=(),
    )


def resolution_inputs(
    runtime: RuntimeInput, claims: Sequence[ClaimProposal]
) -> tuple[tuple[EntityMention, ...], dict[str, tuple[str, ...]]]:
    """Convert local extraction IDs to #128 inputs without accepting DB IDs."""
    document_id = _document_id(runtime)
    spans = _span_map(runtime)
    mentions: dict[str, EntityMention] = {}
    dependencies: dict[str, tuple[str, ...]] = {}
    for claim in claims:
        required: list[str] = []
        claim_sources = set(claim.source_ids)
        for mention in claim.mentions:
            if set(mention.source_ids) - claim_sources:
                raise ValueError("MENTION_EVIDENCE_OUTSIDE_CLAIM")
            converted = _mention_contract(mention, spans, document_id)
            previous = mentions.get(mention.mention_id)
            if previous is not None:
                if (
                    previous.model_copy(
                        update={"source_ranges": converted.source_ranges}
                    )
                    != converted
                ):
                    raise ValueError("CONFLICTING_LOCAL_MENTION_ID")
                converted = previous.model_copy(
                    update={
                        "source_ranges": tuple(
                            dict.fromkeys(
                                previous.source_ranges + converted.source_ranges
                            )
                        )
                    }
                )
            mentions[mention.mention_id] = converted
            required.append(mention.mention_id)
        if len(set(required)) != len(required):
            raise ValueError("DUPLICATE_CLAIM_MENTION_ID")
        dependencies[claim.candidate_id] = tuple(required)
    return tuple(mentions.values()), dependencies


def _rename_binding_mentions(
    binding: Binding, replacements: Mapping[str, str]
) -> Binding:
    if isinstance(binding, RelationProposal):
        return binding.model_copy(
            update={
                "source_mention": replacements.get(
                    binding.source_mention, binding.source_mention
                ),
                "target_mention": replacements.get(
                    binding.target_mention, binding.target_mention
                ),
            }
        )
    if isinstance(binding, AttributeProposal):
        return binding.model_copy(
            update={
                "target_mention": replacements.get(
                    binding.target_mention, binding.target_mention
                )
            }
        )
    if isinstance(binding, EventTimeProposal):
        return binding.model_copy(
            update={
                "event_mention": replacements.get(
                    binding.event_mention, binding.event_mention
                )
            }
        )
    return binding


def _disambiguate_mention_ids(
    claims: Sequence[ClaimProposal],
) -> tuple[tuple[ClaimProposal, ...], dict[str, dict[str, str]]]:
    used_ids = {mention.mention_id for claim in claims for mention in claim.mentions}
    id_signatures: dict[str, str] = {}
    canonical_ids: dict[str, str] = {}
    next_id = 1
    result: list[ClaimProposal] = []
    projection_ids: dict[str, dict[str, str]] = {}
    for claim in claims:
        replacements: dict[str, str] = {}
        mentions: list[Mention] = []
        new_signatures: dict[str, str] = {}
        current_ids: set[str] = set()
        claim_projection_ids: dict[str, str] = {}
        for mention in claim.mentions:
            signature = mention.model_dump_json(exclude={"mention_id", "source_ids"})
            replacement = canonical_ids.get(signature)
            if replacement is None or replacement in current_ids:
                existing = id_signatures.get(mention.mention_id)
                replacement = mention.mention_id
                if replacement in current_ids or (
                    existing is not None and existing != signature
                ):
                    while (replacement := f"projection-mention-{next_id}") in used_ids:
                        next_id += 1
                    next_id += 1
                    used_ids.add(replacement)
                id_signatures[replacement] = signature
                if signature not in canonical_ids:
                    new_signatures[signature] = replacement
            if replacement != mention.mention_id:
                replacements[mention.mention_id] = replacement
            renamed = mention.model_copy(update={"mention_id": replacement})
            mentions.append(renamed)
            current_ids.add(replacement)
            claim_projection_ids[mention.mention_id] = replacement
        bindings = [
            _rename_binding_mentions(binding, replacements)
            for binding in claim.bindings
        ]
        result.append(
            claim.model_copy(update={"mentions": mentions, "bindings": bindings})
        )
        canonical_ids.update(new_signatures)
        projection_ids[claim.candidate_id] = claim_projection_ids
    return tuple(result), projection_ids


def _resolve(
    engine: Engine,
    mentions: Sequence[EntityMention],
    propose: ResolutionProposer,
) -> tuple[Resolution, ...]:
    # Capture one consistent lookup snapshot, then RELEASE the transaction
    # before any helper pacing/model IO. Official promotion revalidation remains.
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session:
                prepared = tuple(
                    er.prepare_resolution(session, mention) for mention in mentions
                )
    return er.finish_resolutions(prepared, propose)


def model_studio_resolution_proposer(
    studio: ModelStudio,
    limits: CallLimits,
) -> ResolutionProposer:
    """Adapt #128's document batch to the existing runtime Plus helper."""

    def propose(payload: ResolutionBatchInput) -> object:
        return studio.call(
            "entity_resolution",
            er.SYSTEM_PROMPT,
            payload,
            ResolutionBatchProposal,
            limits,
        )

    return propose


def _semantic_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _node_semantic_ref(resolution: Resolution) -> dict[str, object]:
    if resolution.decision == "SAME":
        if resolution.node_id is None:
            raise ValueError("SAME_RESOLUTION_WITHOUT_NODE")
        return {"node_id": resolution.node_id}
    if resolution.decision == "NEW":
        return {
            "new_mention_id": resolution.mention.mention_id,
            "node_type": resolution.mention.node_type,
            "mention_text": resolution.mention.text,
        }
    raise ValueError("UNRESOLVED_MENTION_IN_ACCEPTED_CLAIM")


def _ordered_relation_refs(
    rule: RelationRule,
    source: dict[str, object],
    target: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    if rule.direction != "SYMMETRIC":
        return source, target
    source_key = _semantic_json(source)
    target_key = _semantic_json(target)
    return (target, source) if target_key < source_key else (source, target)


def _attribute_semantic_value(binding: AttributeProposal) -> dict[str, object]:
    value = binding.value
    result: dict[str, object] = {
        "value_kind": value.kind,
        "string_value": None,
        "number_value": None,
        "unit_code": None,
        "date_from": None,
        "date_to": None,
        "date_from_precision": "UNKNOWN",
        "date_to_precision": "UNKNOWN",
        "boolean_value": None,
    }
    if value.kind == "STRING":
        result["string_value"] = value.value
    elif isinstance(value, NumberValue):
        result["number_value"] = str(value.value)
        result["unit_code"] = value.unit
    elif isinstance(value, DateValue):
        result["date_from"] = value.value.isoformat()
        result["date_from_precision"] = value.precision
    elif isinstance(value, PeriodValue):
        result["date_from"] = value.start.value.isoformat()
        result["date_from_precision"] = value.start.precision
        if value.end is not None:
            result["date_to"] = value.end.value.isoformat()
            result["date_to_precision"] = value.end.precision
    elif value.kind == "BOOLEAN":
        result["boolean_value"] = value.value
    else:
        raise ValueError("UNSUPPORTED_ATTRIBUTE_VALUE")
    return result


def _proposal_semantic_targets(
    claim: ClaimProposal,
    runtime: RuntimeInput,
    resolutions: Mapping[str, Resolution],
) -> tuple[str, ...]:
    targets: list[str] = []
    for binding in claim.bindings:
        if isinstance(binding, RelationProposal):
            relation_rule = _runtime_relation(runtime, binding.code)
            if relation_rule.revision_id is None:
                raise ValueError("RUNTIME_RELATION_REVISION_MISSING")
            source, target = _ordered_relation_refs(
                relation_rule,
                _node_semantic_ref(resolutions[binding.source_mention]),
                _node_semantic_ref(resolutions[binding.target_mention]),
            )
            targets.append(
                _semantic_json(
                    {
                        "kind": "RELATION",
                        "code": binding.code,
                        "revision_id": relation_rule.revision_id,
                        "source": source,
                        "target": target,
                        "stance": binding.stance,
                    }
                )
            )
            continue
        if isinstance(binding, AttributeProposal):
            attribute_rule = _runtime_attribute(runtime, binding.code)
            if attribute_rule.revision_id is None:
                raise ValueError("RUNTIME_ATTRIBUTE_REVISION_MISSING")
            targets.append(
                _semantic_json(
                    {
                        "kind": "ATTRIBUTE",
                        "code": binding.code,
                        "revision_id": attribute_rule.revision_id,
                        "target": _node_semantic_ref(
                            resolutions[binding.target_mention]
                        ),
                        **_attribute_semantic_value(binding),
                    }
                )
            )
            continue
        if isinstance(binding, EventTimeProposal):
            targets.append(
                _semantic_json(
                    {
                        "kind": "EVENT_TIME",
                        "event": _node_semantic_ref(resolutions[binding.event_mention]),
                        "start_at": (
                            None
                            if binding.start.value is None
                            else binding.start.value.isoformat()
                        ),
                        "end_at": (
                            None
                            if binding.end.value is None
                            else binding.end.value.isoformat()
                        ),
                        "start_precision": binding.start.precision,
                        "end_precision": binding.end.precision,
                    }
                )
            )
            continue
        raise ValueError("UNSUPPORTED_BINDING")
    return tuple(sorted(set(targets)))


def _runtime_relation(runtime: RuntimeInput, code: str) -> RelationRule:
    matches = [rule for rule in runtime.ontology.relations if rule.code == code]
    if len(matches) != 1:
        raise ValueError("RELATION_NOT_IN_RUNTIME_ONTOLOGY")
    return matches[0]


def _runtime_attribute(runtime: RuntimeInput, code: str) -> AttributeRule:
    matches = [rule for rule in runtime.ontology.attributes if rule.code == code]
    if len(matches) != 1:
        raise ValueError("ATTRIBUTE_NOT_IN_RUNTIME_ONTOLOGY")
    return matches[0]


def _validate_relation_snapshot(session: Session, runtime_rule: RelationRule) -> None:
    current = promotion_db.relation_rule(session, runtime_rule.code)
    if runtime_rule.revision_id is None:
        raise ValueError("RUNTIME_RELATION_REVISION_MISSING")
    if current.revision_id != runtime_rule.revision_id:
        raise ValueError("RUNTIME_RELATION_REVISION_CHANGED")
    if current.version_no != runtime_rule.version_no:
        raise ValueError("RUNTIME_RELATION_REVISION_CHANGED")
    if current.direction != runtime_rule.direction:
        raise ValueError("RUNTIME_RELATION_REVISION_CHANGED")
    if set(current.endpoints) != set(runtime_rule.endpoints):
        raise ValueError("RUNTIME_RELATION_ENDPOINTS_CHANGED")


def _validate_attribute_snapshot(session: Session, runtime_rule: AttributeRule) -> None:
    current = promotion_db.attribute_rule(session, runtime_rule.code)
    if runtime_rule.revision_id is None:
        raise ValueError("RUNTIME_ATTRIBUTE_REVISION_MISSING")
    if current.revision_id != runtime_rule.revision_id:
        raise ValueError("RUNTIME_ATTRIBUTE_REVISION_CHANGED")
    if current.version_no != runtime_rule.version_no:
        raise ValueError("RUNTIME_ATTRIBUTE_REVISION_CHANGED")
    if current.target_node_type != runtime_rule.node_type:
        raise ValueError("RUNTIME_ATTRIBUTE_TARGET_CHANGED")
    if current.value_kind != runtime_rule.value_kind:
        raise ValueError("RUNTIME_ATTRIBUTE_KIND_CHANGED")
    if set(current.units) != set(runtime_rule.units):
        raise ValueError("RUNTIME_ATTRIBUTE_UNITS_CHANGED")


def _validate_active_ontology(session: Session, runtime: RuntimeInput) -> None:
    active_types = set(promotion_db.active_node_types(session))
    if set(runtime.ontology.node_types) - active_types:
        raise ValueError("RUNTIME_NODE_TYPE_NO_LONGER_ACTIVE")
    active_topics = {name for _, _, name in promotion_db.active_topic_identity(session)}
    if set(runtime.ontology.topics) - active_topics:
        raise ValueError("RUNTIME_TOPIC_NO_LONGER_ACTIVE")
    for relation_rule in runtime.ontology.relations:
        _validate_relation_snapshot(session, relation_rule)
    for attribute_rule in runtime.ontology.attributes:
        _validate_attribute_snapshot(session, attribute_rule)


def _attribute_values(
    proposal: AttributeProposal,
    target_node_id: int,
    revision_id: int,
) -> dict[str, Any]:
    value = proposal.value
    result: dict[str, Any] = {
        "target_node_id": target_node_id,
        "attribute_revision_id": revision_id,
        "value_kind": value.kind,
        "string_value": None,
        "number_value": None,
        "unit_code": None,
        "date_from": None,
        "date_to": None,
        "date_from_precision": "UNKNOWN",
        "date_to_precision": "UNKNOWN",
        "boolean_value": None,
    }
    if value.kind == "STRING":
        result["string_value"] = value.value
    elif isinstance(value, NumberValue):
        result["number_value"] = value.value
        result["unit_code"] = value.unit
    elif isinstance(value, DateValue):
        result["date_from"] = value.value
        result["date_from_precision"] = value.precision
    elif isinstance(value, PeriodValue):
        result["date_from"] = value.start.value
        result["date_from_precision"] = value.start.precision
        if value.end is not None:
            result["date_to"] = value.end.value
            result["date_to_precision"] = value.end.precision
    elif value.kind == "BOOLEAN":
        result["boolean_value"] = value.value
    else:  # pragma: no cover - pydantic union is closed
        raise ValueError("UNSUPPORTED_ATTRIBUTE_VALUE")
    return result


def _event_value(
    proposal: EventTimeProposal,
    event_node_id: int,
    *,
    node_is_new: bool,
) -> _ResolvedEvent:
    return _ResolvedEvent(
        event_node_id=event_node_id,
        start_at=proposal.start.value,
        end_at=proposal.end.value,
        start_precision=proposal.start.precision,
        end_precision=proposal.end.precision,
        node_is_new=node_is_new,
    )


def _claim_sources(
    claim: ClaimProposal, runtime: RuntimeInput
) -> tuple[tuple[int, int, str], ...]:
    spans = _span_map(runtime)
    values: list[tuple[int, int, str]] = []
    for source_id in claim.source_ids:
        span = spans.get(source_id)
        if span is None:
            raise ValueError("CLAIM_SOURCE_OUTSIDE_RUNTIME_DOCUMENT")
        values.append((span.start, span.end, span.quote))
    return tuple(values)


@dataclass(frozen=True)
class _ClaimBindings:
    relations: tuple[tuple[int, str], ...]
    attributes: tuple[dict[str, Any], ...]
    events: tuple[_ResolvedEvent, ...]
    relation_writes: int
    created_relations: frozenset[int]


def _resolve_relation_binding(
    session: Session,
    batch_id: int,
    runtime: RuntimeInput,
    binding: RelationProposal,
    node_bindings: Mapping[str, PromotionNodeBinding],
) -> _ResolvedRelation:
    source = node_bindings[binding.source_mention].node_id
    target = node_bindings[binding.target_mention].node_id
    runtime_rule = _runtime_relation(runtime, binding.code)
    db_rule = promotion_db.relation_rule(session, binding.code)
    if runtime_rule.revision_id != db_rule.revision_id:
        raise ValueError("RELATION_REVISION_CHANGED_DURING_PROMOTION")
    relation = promotion_db.ensure_relation(session, batch_id, db_rule, source, target)
    return _ResolvedRelation(relation.relation_id, binding.stance, relation.created)


def _resolve_attribute_binding(
    session: Session,
    runtime: RuntimeInput,
    binding: AttributeProposal,
    node_bindings: Mapping[str, PromotionNodeBinding],
) -> dict[str, Any]:
    target = node_bindings[binding.target_mention].node_id
    runtime_rule = _runtime_attribute(runtime, binding.code)
    db_rule = promotion_db.attribute_rule(session, binding.code)
    if runtime_rule.revision_id != db_rule.revision_id:
        raise ValueError("ATTRIBUTE_REVISION_CHANGED_DURING_PROMOTION")
    if promotion_db.node_type(session, target) != db_rule.target_node_type:
        raise ValueError("ATTRIBUTE_TARGET_TYPE_CHANGED")
    if binding.value.kind != db_rule.value_kind:
        raise ValueError("ATTRIBUTE_VALUE_KIND_CHANGED")
    if (
        isinstance(binding.value, NumberValue)
        and binding.value.unit not in db_rule.units
    ):
        raise ValueError("ATTRIBUTE_UNIT_NO_LONGER_ALLOWED")
    return _attribute_values(binding, target, db_rule.revision_id)


def _resolve_event_binding(
    session: Session,
    binding: EventTimeProposal,
    node_bindings: Mapping[str, PromotionNodeBinding],
    resolutions: Mapping[str, Resolution],
) -> _ResolvedEvent:
    event_node = node_bindings[binding.event_mention].node_id
    if promotion_db.node_type(session, event_node) != "EVENT":
        raise ValueError("EVENT_TIME_TARGET_NOT_EVENT")
    resolution = resolutions[binding.event_mention]
    return _event_value(
        binding,
        event_node,
        node_is_new=resolution.decision == "NEW",
    )


def _resolve_claim_bindings(
    session: Session,
    *,
    batch_id: int,
    claim: ClaimProposal,
    runtime: RuntimeInput,
    node_bindings: Mapping[str, PromotionNodeBinding],
    resolutions: Mapping[str, Resolution],
) -> _ClaimBindings:
    relation_by_id: dict[int, str] = {}
    attribute_by_key: dict[tuple[object, ...], dict[str, Any]] = {}
    event_by_node: dict[int, _ResolvedEvent] = {}
    relation_writes = 0
    created_relations: set[int] = set()
    for binding in claim.bindings:
        if isinstance(binding, RelationProposal):
            relation = _resolve_relation_binding(
                session, batch_id, runtime, binding, node_bindings
            )
            previous = relation_by_id.get(relation.relation_id)
            if previous is not None and previous != relation.stance:
                raise ValueError("CLAIM_RELATION_STANCE_CONFLICT")
            relation_by_id[relation.relation_id] = relation.stance
            if relation.created:
                relation_writes += 1
                created_relations.add(relation.relation_id)
            continue
        if isinstance(binding, AttributeProposal):
            values = _resolve_attribute_binding(
                session, runtime, binding, node_bindings
            )
            attribute_by_key[promotion_db.canonical_attribute_tuple(values)] = values
            continue
        if isinstance(binding, EventTimeProposal):
            event = _resolve_event_binding(session, binding, node_bindings, resolutions)
            previous_event = event_by_node.get(event.event_node_id)
            if previous_event is not None and previous_event != event:
                raise ValueError("CONFLICTING_EVENT_TIME_IN_CLAIM")
            event_by_node[event.event_node_id] = event
            continue
        raise ValueError("UNSUPPORTED_BINDING")
    return _ClaimBindings(
        relations=tuple(sorted(relation_by_id.items())),
        attributes=tuple(attribute_by_key.values()),
        events=tuple(event_by_node.values()),
        relation_writes=relation_writes,
        created_relations=frozenset(created_relations),
    )


EventSignature = tuple[int, datetime | None, datetime | None, str, str]


def _event_signature(events: Sequence[_ResolvedEvent]) -> tuple[EventSignature, ...]:
    signatures: list[EventSignature] = [
        (
            event.event_node_id,
            event.start_at,
            event.end_at,
            event.start_precision,
            event.end_precision,
        )
        for event in events
    ]
    return tuple(
        sorted(
            signatures,
            key=lambda item: (
                item[0],
                "" if item[1] is None else item[1].isoformat(),
                "" if item[2] is None else item[2].isoformat(),
                item[3],
                item[4],
            ),
        )
    )


def _claim_batch_id(session: Session, claim_id: int) -> int:
    value = session.scalar(
        sa.select(schema.knowledge_item.c.promotion_batch_id).where(
            schema.knowledge_item.c.knowledge_item_id == claim_id
        )
    )
    if value is None:
        raise ValueError("CLAIM_HAS_NO_PROMOTION_BATCH")
    return int(value)


def _ensure_claim_observations(
    session: Session,
    *,
    claim_id: int,
    batch_id: int,
    claim: ClaimProposal,
    runtime: RuntimeInput,
    document_id: int,
) -> int:
    writes = 0
    for start, end, quote in _claim_sources(claim, runtime):
        observation_id, observation_created = promotion_db.ensure_observation(
            session, document_id, start, end, quote
        )
        writes += int(observation_created)
        writes += int(
            provenance.add_claim_observation(
                session, batch_id, claim_id, observation_id
            )
        )
    return writes


def _apply_event_semantics(
    session: Session,
    batch_id: int,
    event: _ResolvedEvent,
    claim_id: int,
) -> int:
    expected = (
        event.start_at,
        event.end_at,
        event.start_precision,
        event.end_precision,
    )
    existing = promotion_db.event_extent(session, event.event_node_id)
    if existing is None and not event.node_is_new:
        raise ValueError("EXISTING_EVENT_EXTENT_PROVENANCE_UNSUPPORTED")
    if existing is not None and existing != expected:
        raise ValueError("EVENT_TEMPORAL_EXTENT_CONFLICT")
    writes = int(
        promotion_db.ensure_event_extent(
            session,
            event.event_node_id,
            start_at=event.start_at,
            end_at=event.end_at,
            start_precision=event.start_precision,
            end_precision=event.end_precision,
        )
    )
    writes += int(
        provenance.add_event_temporal_basis(
            session, batch_id, event.event_node_id, claim_id
        )
    )
    return writes


def _apply_claim_semantics(
    session: Session,
    batch_id: int,
    claim_id: int,
    bindings: _ClaimBindings,
) -> int:
    writes = 0
    for relation_id, stance in bindings.relations:
        writes += int(
            provenance.add_claim_relation(
                session, batch_id, claim_id, relation_id, stance
            )
        )
    for values in bindings.attributes:
        if promotion_db.claim_attribute_value_exists(session, claim_id, values):
            continue
        provenance.add_claim_attribute_value(
            session,
            batch_id,
            claim_id=claim_id,
            **values,
        )
        writes += 1
    for event in bindings.events:
        writes += _apply_event_semantics(session, batch_id, event, claim_id)
    return writes


def _write_claim(
    session: Session,
    *,
    batch_id: int,
    claim: ClaimProposal,
    runtime: RuntimeInput,
    node_bindings: Mapping[str, PromotionNodeBinding],
    resolutions: Mapping[str, Resolution],
    document_id: int,
    language: str,
    duplicate: _ClaimDuplicateDecision,
) -> tuple[int, int, set[int]]:
    bindings = _resolve_claim_bindings(
        session,
        batch_id=batch_id,
        claim=claim,
        runtime=runtime,
        node_bindings=node_bindings,
        resolutions=resolutions,
    )
    writes = bindings.relation_writes
    if duplicate.existing is not None:
        promotion_db.revalidate_claim_candidate(session, duplicate.existing)
        if duplicate.existing.semantic_targets != duplicate.semantic_targets:
            raise ValueError("CLAIM_DUPLICATE_SEMANTIC_TARGETS_CHANGED")
        claim_id = duplicate.existing.claim_id
    else:
        existing_claim_id = promotion_db.find_exact_claim(
            session,
            statement=claim.statement,
            language=language,
            modality=claim.modality,
            relations=bindings.relations,
            attributes=bindings.attributes,
            events=_event_signature(bindings.events),
            current_batch_id=batch_id,
        )
        if existing_claim_id is None:
            claim_id = promotion_db.insert_claim(
                session,
                batch_id,
                statement=claim.statement,
                language=language,
                modality=claim.modality,
            ).claim_id
            writes += 1
        else:
            claim_id = existing_claim_id
    writes += _ensure_claim_observations(
        session,
        claim_id=claim_id,
        batch_id=batch_id,
        claim=claim,
        runtime=runtime,
        document_id=document_id,
    )
    writes += _apply_claim_semantics(session, batch_id, claim_id, bindings)
    return claim_id, writes, set(bindings.created_relations)


def _finish_without_promotion(
    engine: Engine,
    lease: tasks.Lease,
    execution: inputs.ExecutionInput,
    runtime: RuntimeInput,
    *,
    valid: bool,
) -> ProductResult:
    try:
        _runtime_identity_matches(execution, runtime)
        with Session(engine) as session, session.begin():
            tasks.require_lease(session, lease)
            identity = inputs.require_current_input(session, lease.task_id, execution)
            if identity.source_document_id != _document_id(runtime):
                raise inputs.InputChanged("RUNNER_SOURCE_MISMATCH")
            state = tasks.finish_product(session, lease, valid=valid)
        disposition: ProductDisposition = (
            "SUCCESS" if state == "SUCCESS" else "VALIDATION_BLOCKED"
        )
        return ProductResult(state, disposition)
    except tasks.LeaseLost:
        return ProductResult(
            _status(engine, lease.task_id), "LEASE_LOST", error_code="LEASE_LOST"
        )
    except Exception as error:
        return _fail_after_rollback(engine, lease, error)


def _is_transient_failure(error: Exception) -> bool:
    if bool(getattr(error, "transient", False)):
        return True
    if isinstance(error, sa.exc.DBAPIError):
        sqlstate = getattr(error.orig, "sqlstate", "") or ""
        return bool(
            error.connection_invalidated
            or sqlstate.startswith("08")
            or sqlstate in {"40001", "40P01"}
        )
    if isinstance(error, (httpx.TimeoutException, APITimeoutError)):
        return True
    confirmed = classify_provider_error(error)
    return bool(
        confirmed is not None
        and (confirmed.transient or confirmed.outcome == "TIMEOUT")
    )


def _record_execution_failure(
    engine: Engine, lease: tasks.Lease, *, transient: bool
) -> ProductResult:
    try:
        with Session(engine) as session, session.begin():
            state = tasks.fail_execution(session, lease, transient=transient)
    except tasks.LeaseLost:
        return ProductResult(
            _status(engine, lease.task_id), "LEASE_LOST", error_code="LEASE_LOST"
        )
    except tasks.SlotBusy:
        return ProductResult(
            _status(engine, lease.task_id),
            "FINAL_FAILED",
            error_code="PROMOTION_LEDGER_BUSY",
        )
    disposition: ProductDisposition = (
        "RETRY_WAIT" if state == "RETRY_WAIT" else "FINAL_FAILED"
    )
    return ProductResult(state, disposition, error_code="PROMOTION_FAILED")


def _fail_after_rollback(
    engine: Engine, lease: tasks.Lease, error: Exception
) -> ProductResult:
    if isinstance(error, tasks.LeaseLost):
        return ProductResult(
            _status(engine, lease.task_id), "LEASE_LOST", error_code="LEASE_LOST"
        )
    return _record_execution_failure(
        engine, lease, transient=_is_transient_failure(error)
    )


def _precheck_verified(
    engine: Engine,
    lease: tasks.Lease,
    execution: inputs.ExecutionInput,
    runtime: RuntimeInput,
) -> None:
    _runtime_identity_matches(execution, runtime)
    with Session(engine) as session, session.begin():
        tasks.require_lease(session, lease)
        identity = inputs.require_current_input(session, lease.task_id, execution)
        if identity.source_document_id != _document_id(runtime):
            raise inputs.InputChanged("RUNNER_SOURCE_MISMATCH")


@dataclass(frozen=True)
class _ClaimDuplicateDecision:
    semantic_targets: tuple[str, ...]
    existing: promotion_db.ClaimCandidateSnapshot | None = None


@dataclass(frozen=True)
class _ResolutionSelection:
    resolutions: tuple[Resolution, ...]
    accepted: tuple[ClaimProposal, ...]
    accepted_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    required_mentions: frozenset[str]
    claim_duplicates: Mapping[str, _ClaimDuplicateDecision]
    mention_projection_ids: Mapping[str, Mapping[str, str]]
    binding_outcomes: dict[str, dict[str, str]]
    projection_reasons: dict[str, str]


def _resolve_verified_claims(
    engine: Engine,
    runtime: RuntimeInput,
    claims: Sequence[ClaimProposal],
    propose_resolution: ResolutionProposer,
    extraction: ExtractionResult,
) -> _ResolutionSelection:
    claims, mention_projection_ids = _disambiguate_mention_ids(claims)
    mentions, _ = resolution_inputs(runtime, claims)
    resolutions = _resolve(engine, mentions, propose_resolution)
    by_mention = {item.mention.mention_id: item for item in resolutions}
    accepted: list[ClaimProposal] = []
    excluded: list[str] = []
    required_mentions: set[str] = set()
    for claim in claims:
        retained = []
        outcomes = extraction.binding_outcomes.setdefault(claim.candidate_id, {})
        for binding in claim.bindings:
            required = binding_mentions(binding)
            if any(by_mention[item].decision == "UNRESOLVED" for item in required):
                outcomes[binding.binding_id] = "ENTITY_UNRESOLVED"
            else:
                outcomes[binding.binding_id] = "ENTITY_RESOLVED"
                retained.append(binding)
        if not retained:
            excluded.append(claim.candidate_id)
            extraction.projection_reasons[claim.candidate_id] = "ENTITY_UNRESOLVED"
            continue
        used = set().union(*(binding_mentions(binding) for binding in retained))
        required_mentions.update(used)
        accepted.append(
            claim.model_copy(
                update={
                    "bindings": retained,
                    "mentions": [
                        mention
                        for mention in claim.mentions
                        if mention.mention_id in used
                    ],
                }
            )
        )
    return _ResolutionSelection(
        resolutions=resolutions,
        accepted=tuple(accepted),
        accepted_ids=tuple(claim.candidate_id for claim in accepted),
        excluded_ids=tuple(excluded),
        required_mentions=frozenset(required_mentions),
        claim_duplicates={},
        mention_projection_ids=mention_projection_ids,
        binding_outcomes=extraction.binding_outcomes,
        projection_reasons=extraction.projection_reasons,
    )


def _claim_same_node_ids(
    claim: ClaimProposal, resolutions: Mapping[str, Resolution]
) -> tuple[int, ...]:
    result: set[int] = set()
    for mention in claim.mentions:
        resolution = resolutions[mention.mention_id]
        if resolution.decision == "SAME":
            if resolution.node_id is None:
                raise ValueError("SAME_RESOLUTION_WITHOUT_NODE")
            result.add(resolution.node_id)
    return tuple(sorted(result))


def _duplicate_snapshots(
    engine: Engine,
    runtime: RuntimeInput,
    selection: _ResolutionSelection,
) -> list[
    tuple[
        ClaimProposal, tuple[str, ...], tuple[promotion_db.ClaimCandidateSnapshot, ...]
    ]
]:
    resolutions = {item.mention.mention_id: item for item in selection.resolutions}
    rows = []
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session:
                _validate_active_ontology(session, runtime)
                document = promotion_db.source_document(session, _document_id(runtime))
                if runtime.document.body != document["normalized_body"]:
                    raise inputs.InputChanged("RUNNER_SOURCE_MISMATCH")
                for claim in selection.accepted:
                    semantic_targets = _proposal_semantic_targets(
                        claim, runtime, resolutions
                    )
                    candidates = promotion_db.claim_duplicate_candidates(
                        session,
                        statement=claim.statement,
                        language=str(document["original_language"]),
                        modality=claim.modality,
                        node_ids=_claim_same_node_ids(claim, resolutions),
                    )
                    rows.append((claim, semantic_targets, candidates))
    return rows


def _judge_claim_duplicates(
    engine: Engine,
    runtime: RuntimeInput,
    selection: _ResolutionSelection,
) -> _ResolutionSelection:
    accepted: list[ClaimProposal] = []
    excluded = list(selection.excluded_ids)
    decisions: dict[str, _ClaimDuplicateDecision] = {}
    for claim, semantic_targets, candidates in _duplicate_snapshots(
        engine, runtime, selection
    ):
        exact = tuple(
            item
            for item in candidates
            if item.statement == claim.statement
            and item.semantic_targets == semantic_targets
        )
        if len(exact) > 1:
            raise ValueError("AMBIGUOUS_EXACT_CLAIM_DUPLICATE")
        existing: promotion_db.ClaimCandidateSnapshot | None = None
        if exact:
            existing = exact[0]
        decisions[claim.candidate_id] = _ClaimDuplicateDecision(
            semantic_targets=semantic_targets, existing=existing
        )
        accepted.append(claim)
    accepted_ids = tuple(claim.candidate_id for claim in accepted)
    required_mentions = frozenset(
        mention.mention_id for claim in accepted for mention in claim.mentions
    )
    return _ResolutionSelection(
        resolutions=selection.resolutions,
        accepted=tuple(accepted),
        accepted_ids=accepted_ids,
        excluded_ids=tuple(excluded),
        required_mentions=required_mentions,
        claim_duplicates=decisions,
        mention_projection_ids=selection.mention_projection_ids,
        binding_outcomes=selection.binding_outcomes,
        projection_reasons=selection.projection_reasons,
    )


def _required_resolutions(selection: _ResolutionSelection) -> tuple[Resolution, ...]:
    return tuple(
        item
        for item in selection.resolutions
        if item.mention.mention_id in selection.required_mentions
    )


def _relation_support_key(
    binding: RelationProposal,
    runtime: RuntimeInput,
    resolutions: Mapping[str, Resolution],
) -> tuple[int, str, str]:
    rule = _runtime_relation(runtime, binding.code)
    if rule.revision_id is None:
        raise ValueError("RUNTIME_RELATION_REVISION_MISSING")
    source, target = _ordered_relation_refs(
        rule,
        _node_semantic_ref(resolutions[binding.source_mention]),
        _node_semantic_ref(resolutions[binding.target_mention]),
    )
    return rule.revision_id, _semantic_json(source), _semantic_json(target)


def _existing_relation_id(session: Session, key: tuple[int, str, str]) -> int | None:
    revision_id, source_json, target_json = key
    source = json.loads(source_json).get("node_id")
    target = json.loads(target_json).get("node_id")
    if source is None or target is None:
        return None
    relation_id = session.scalar(
        sa.select(schema.relation.c.relation_id)
        .join(schema.relation_type_revision)
        .where(
            schema.relation.c.relation_type_revision_id == revision_id,
            sa.or_(
                sa.and_(
                    schema.relation.c.source_node_id == source,
                    schema.relation.c.target_node_id == target,
                ),
                sa.and_(
                    schema.relation_type_revision.c.directionality == "SYMMETRIC",
                    schema.relation.c.source_node_id == target,
                    schema.relation.c.target_node_id == source,
                ),
            ),
        )
    )
    return None if relation_id is None else int(relation_id)


def _exclude_unsupported_relations(
    engine: Engine, runtime: RuntimeInput, selection: _ResolutionSelection
) -> _ResolutionSelection:
    resolutions = {item.mention.mention_id: item for item in selection.resolutions}
    keys = {
        claim.candidate_id: {
            binding.binding_id: (
                _relation_support_key(binding, runtime, resolutions),
                binding.stance,
            )
            for binding in claim.bindings
            if isinstance(binding, RelationProposal)
        }
        for claim in selection.accepted
    }
    all_keys = {key for entries in keys.values() for key, _ in entries.values()}
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session:
                relation_ids = {
                    key: _existing_relation_id(session, key) for key in all_keys
                }
                unusable = {
                    key
                    for key, relation_id in relation_ids.items()
                    if relation_id is not None
                    and not promotion_db._usable_knowledge(
                        session, relation_id, "RELATION"
                    )
                }
                existing = {
                    key
                    for key, relation_id in relation_ids.items()
                    if relation_id is not None
                    and key not in unusable
                    and promotion_db.relation_has_supported_claim(session, relation_id)
                }
    supported = existing | {
        key
        for entries in keys.values()
        for key, stance in entries.values()
        if stance == "SUPPORT"
    }
    accepted: list[ClaimProposal] = []
    excluded = list(selection.excluded_ids)
    for claim in selection.accepted:
        retained: list[Binding] = []
        for binding in claim.bindings:
            relation = keys[claim.candidate_id].get(binding.binding_id)
            if relation is not None:
                key, _ = relation
                if key in unusable or key not in supported:
                    outcomes = selection.binding_outcomes[claim.candidate_id]
                    outcomes[binding.binding_id] = "RELATION_SUPPORT_BLOCKED"
                    continue
            retained.append(binding)
        if not retained:
            excluded.append(claim.candidate_id)
            selection.projection_reasons[claim.candidate_id] = (
                "RELATION_SUPPORT_BLOCKED"
            )
            continue
        used = set().union(*(binding_mentions(binding) for binding in retained))
        accepted.append(
            claim.model_copy(
                update={
                    "bindings": retained,
                    "mentions": [
                        mention
                        for mention in claim.mentions
                        if mention.mention_id in used
                    ],
                }
            )
        )
    return _ResolutionSelection(
        resolutions=selection.resolutions,
        accepted=tuple(accepted),
        accepted_ids=tuple(claim.candidate_id for claim in accepted),
        excluded_ids=tuple(excluded),
        required_mentions=frozenset(
            mention.mention_id for claim in accepted for mention in claim.mentions
        ),
        claim_duplicates=selection.claim_duplicates,
        mention_projection_ids=selection.mention_projection_ids,
        binding_outcomes=selection.binding_outcomes,
        projection_reasons=selection.projection_reasons,
    )


def _apply_claim_set(
    session: Session,
    *,
    batch_id: int,
    selection: _ResolutionSelection,
    runtime: RuntimeInput,
    node_bindings: Mapping[str, PromotionNodeBinding],
    document_id: int,
    language: str,
) -> tuple[int, set[int]]:
    resolutions = {item.mention.mention_id: item for item in selection.resolutions}
    writes = 0
    created_relations: set[int] = set()
    for claim in selection.accepted:
        _, added, relations = _write_claim(
            session,
            batch_id=batch_id,
            claim=claim,
            runtime=runtime,
            node_bindings=node_bindings,
            resolutions=resolutions,
            document_id=document_id,
            language=language,
            duplicate=selection.claim_duplicates[claim.candidate_id],
        )
        writes += added
        created_relations.update(relations)
    return writes, created_relations


def _validate_created_relations(session: Session, relation_ids: set[int]) -> None:
    for relation_id in relation_ids:
        if not promotion_db.relation_has_supported_claim(session, relation_id):
            raise ValueError("UNSUPPORTED_NEW_RELATION")


def _finalize_verified_transaction(
    engine: Engine,
    lease: tasks.Lease,
    execution: inputs.ExecutionInput,
    runtime: RuntimeInput,
    selection: _ResolutionSelection,
) -> ProductResult:
    document_id = _document_id(runtime)
    required = _required_resolutions(selection)
    try:
        with Session(engine) as session, session.begin():
            tasks.require_lease(session, lease)
            identity = inputs.require_current_input(session, lease.task_id, execution)
            if identity.source_document_id != document_id:
                raise inputs.InputChanged("RUNNER_SOURCE_MISMATCH")
            document = promotion_db.source_document(session, document_id)
            if runtime.document.body != document["normalized_body"]:
                raise inputs.InputChanged("RUNNER_SOURCE_MISMATCH")
            _validate_active_ontology(session, runtime)

            batch_id = promotion_db.create_batch(session, execution.validator_version)
            writes = sum(item.decision == "NEW" for item in required)
            with er.resolved_nodes_for_promotion(
                session,
                batch_id,
                required,
                selection.required_mentions,
            ) as node_bindings:
                added, created_relations = _apply_claim_set(
                    session,
                    batch_id=batch_id,
                    selection=selection,
                    runtime=runtime,
                    node_bindings=node_bindings,
                    document_id=document_id,
                    language=str(document["original_language"]),
                )
                writes += added
                _validate_created_relations(session, created_relations)

            has_provenance = bool(provenance.changes_for_batch(session, batch_id))
            if writes == 0 and not has_provenance:
                promotion_db.discard_pending_batch(session, batch_id)
                committed_batch = None
            else:
                provenance.mark_promotion_committed(session, batch_id)
                committed_batch = batch_id
            state = tasks.finish_product(session, lease, valid=True)
        return ProductResult(
            state,
            "SUCCESS",
            promotion_batch_id=committed_batch,
            accepted_claims=selection.accepted_ids,
            excluded_claims=selection.excluded_ids,
        )
    except Exception as error:
        result = _fail_after_rollback(engine, lease, error)
        return ProductResult(
            result.task_status,
            result.disposition,
            accepted_claims=selection.accepted_ids,
            excluded_claims=selection.excluded_ids,
            error_code=result.error_code,
        )


def finalize_extraction(
    engine: Engine,
    runner: RunnerResult,
    execution: inputs.ExecutionInput,
    runtime: RuntimeInput,
    propose_resolution: ResolutionProposer,
    *,
    write_lock: AbstractContextManager[object] | None = None,
) -> ProductResult:
    """Resolve and atomically apply one already-generated runtime result."""
    lease = runner.lease
    if lease is None or runner.extraction is None:
        raise ValueError("RUNNER_RESULT_HAS_NO_PRODUCT_PAYLOAD")
    guard = write_lock if write_lock is not None else nullcontext()
    if runner.disposition == "ZERO_RESULT":
        with guard:
            return _finish_without_promotion(
                engine, lease, execution, runtime, valid=True
            )
    if runner.disposition == "ALL_BLOCKED":
        with guard:
            return _finish_without_promotion(
                engine, lease, execution, runtime, valid=False
            )
    if runner.disposition != "VERIFIED_RUNTIME":
        raise ValueError("RUNNER_RESULT_NOT_FINALIZABLE")
    try:
        _precheck_verified(engine, lease, execution, runtime)

        def check_lease() -> None:
            with Session(engine) as session, session.begin():
                tasks.require_lease(session, lease)

        with provider_lease(check_lease):
            selection = _resolve_verified_claims(
                engine,
                runtime,
                runner.extraction.verified,
                propose_resolution,
                runner.extraction,
            )
            selection = _exclude_unsupported_relations(engine, runtime, selection)
            selection = _judge_claim_duplicates(engine, runtime, selection)
    except Exception as error:
        return _fail_after_rollback(engine, lease, error)
    if not selection.accepted:
        with guard:
            result = _finish_without_promotion(
                engine, lease, execution, runtime, valid=False
            )
        return ProductResult(
            result.task_status,
            result.disposition,
            excluded_claims=selection.excluded_ids,
            error_code=result.error_code,
        )
    with guard:
        return _finalize_verified_transaction(
            engine, lease, execution, runtime, selection
        )
