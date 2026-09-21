"""Explicit output contracts and read-only product prerequisites for #227."""

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ontology_map import (
    followup_generation,
    insight_generation,
    node_context_generation,
)
from ontology_map.db import extraction_promotion, product_lint, schema
from ontology_map.db.ontology_reference_data import (
    ATTRIBUTE_DEFINITIONS,
    NODE_TYPE_DEFINITIONS,
    RELATION_DEFINITIONS,
)
from ontology_map.db.topic_reference_schema import APPROVED_TOPIC_DEFINITIONS
from ontology_map.extraction_contracts import KnowledgeProposals, Ontology


class RuntimeNotReady(ValueError):
    """A product prerequisite is absent or differs from the current code."""


def output_schemas() -> dict[str, dict[str, object]]:
    return {
        "KNOWLEDGE_EXTRACTION": KnowledgeProposals.model_json_schema(),
        "NODE_CONTEXT": node_context_generation.output_schema(),
        "FOLLOWUP_QUESTIONS": followup_generation.output_schema(),
        "NODE_INSIGHT": insight_generation.output_schema(),
    }


def ensure_output_schemas(session: Session) -> None:
    """Prepare only missing definitions in an explicit caller-owned transaction.

    Existing active definitions are immutable here. In particular, the HBF
    fixture's ``{"type": "object"}`` placeholder is a conflict, not a
    product schema to silently replace.
    """
    if not session.in_transaction():
        raise ValueError("OUTPUT_SCHEMA_TRANSACTION_REQUIRED")
    for task_kind, expected in output_schemas().items():
        rows = (
            session.execute(
                sa.select(schema.output_schema_definition).where(
                    schema.output_schema_definition.c.task_kind == task_kind
                )
            )
            .mappings()
            .all()
        )
        active = [row for row in rows if row["is_active"]]
        if active:
            if len(active) != 1 or active[0]["schema_json"] != expected:
                raise RuntimeNotReady(f"ACTIVE_OUTPUT_SCHEMA_MISMATCH:{task_kind}")
            continue
        session.execute(
            schema.output_schema_definition.insert().values(
                task_kind=task_kind,
                version_no=max((int(row["version_no"]) for row in rows), default=0) + 1,
                schema_json=expected,
                is_active=True,
            )
        )


def _require_output_schemas(session: Session) -> None:
    for task_kind, expected in output_schemas().items():
        rows = (
            session.execute(
                sa.select(schema.output_schema_definition.c.schema_json).where(
                    schema.output_schema_definition.c.task_kind == task_kind,
                    schema.output_schema_definition.c.is_active,
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != 1 or rows[0] != expected:
            raise RuntimeNotReady(f"ACTIVE_OUTPUT_SCHEMA_MISMATCH:{task_kind}")


def _require_ontology(session: Session) -> None:
    node_types = set(extraction_promotion.active_node_types(session))
    if node_types != {code for code, _ in NODE_TYPE_DEFINITIONS}:
        raise RuntimeNotReady("ACTIVE_NODE_TYPES_MISMATCH")
    relation_codes = set(
        session.scalars(
            sa.text("""
        SELECT t.relation_code FROM relation_type t
        JOIN relation_type_revision r USING (relation_type_id)
        WHERE r.is_active
    """)
        )
    )
    if relation_codes != {definition.code for definition in RELATION_DEFINITIONS}:
        raise RuntimeNotReady("ACTIVE_RELATIONS_MISMATCH")
    for definition in RELATION_DEFINITIONS:
        current = extraction_promotion.relation_rule(session, definition.code)
        actual = {
            tuple(sorted(pair)) if current.direction == "SYMMETRIC" else pair
            for pair in current.endpoints
        }
        expected = {
            tuple(sorted(pair)) if definition.directionality == "SYMMETRIC" else pair
            for pair in definition.endpoint_pairs
        }
        if current.direction != definition.directionality or actual != expected:
            raise RuntimeNotReady(f"ACTIVE_RELATION_MISMATCH:{definition.code}")
    attribute_codes = set(
        session.scalars(
            sa.text("""
        SELECT a.attribute_code FROM attribute a
        JOIN attribute_revision r USING (attribute_id)
        WHERE r.is_active
    """)
        )
    )
    if attribute_codes != {definition.code for definition in ATTRIBUTE_DEFINITIONS}:
        raise RuntimeNotReady("ACTIVE_ATTRIBUTES_MISMATCH")
    for attribute_definition in ATTRIBUTE_DEFINITIONS:
        attribute_current = extraction_promotion.attribute_rule(
            session, attribute_definition.code
        )
        if (
            attribute_current.target_node_type != attribute_definition.target_node_type
            or attribute_current.value_kind != attribute_definition.value_kind
            or set(attribute_current.units) != set(attribute_definition.allowed_units)
        ):
            raise RuntimeNotReady(
                f"ACTIVE_ATTRIBUTE_MISMATCH:{attribute_definition.code}"
            )
    topics = extraction_promotion.active_topic_identity(session)
    if len(topics) != len(APPROVED_TOPIC_DEFINITIONS) or {
        (code, name) for _, code, name in topics
    } != set(APPROVED_TOPIC_DEFINITIONS):
        raise RuntimeNotReady("ACTIVE_TOPICS_MISMATCH")


def _require_runtime_ontology(session: Session, ontology: Ontology) -> None:
    if (
        set(ontology.node_types) != {code for code, _ in NODE_TYPE_DEFINITIONS}
        or set(ontology.topics) != {name for _, name in APPROVED_TOPIC_DEFINITIONS}
        or {rule.code for rule in ontology.relations}
        != {definition.code for definition in RELATION_DEFINITIONS}
        or {rule.code for rule in ontology.attributes}
        != {definition.code for definition in ATTRIBUTE_DEFINITIONS}
    ):
        raise RuntimeNotReady("RUNTIME_ONTOLOGY_SCOPE_MISMATCH")
    for rule in ontology.relations:
        current = extraction_promotion.relation_rule(session, rule.code)
        actual = {
            tuple(sorted(pair)) if rule.direction == "SYMMETRIC" else pair
            for pair in rule.endpoints
        }
        expected = {
            tuple(sorted(pair)) if current.direction == "SYMMETRIC" else pair
            for pair in current.endpoints
        }
        if (
            rule.revision_id != current.revision_id
            or rule.version_no != current.version_no
            or rule.direction != current.direction
            or actual != expected
        ):
            raise RuntimeNotReady(f"RUNTIME_RELATION_MISMATCH:{rule.code}")
    for attribute in ontology.attributes:
        active_attribute = extraction_promotion.attribute_rule(session, attribute.code)
        if (
            attribute.revision_id != active_attribute.revision_id
            or attribute.version_no != active_attribute.version_no
            or attribute.node_type != active_attribute.target_node_type
            or attribute.value_kind != active_attribute.value_kind
            or set(attribute.units) != set(active_attribute.units)
        ):
            raise RuntimeNotReady(f"RUNTIME_ATTRIBUTE_MISMATCH:{attribute.code}")


def require_runtime_ready(session: Session, ontology: Ontology | None = None) -> None:
    """Read-only preflight for the exact product contracts."""
    _require_ontology(session)
    _require_output_schemas(session)
    if ontology is not None:
        _require_runtime_ontology(session, ontology)
    try:
        product_lint.require_product_policy(session)
    except ValueError as error:
        raise RuntimeNotReady(str(error)) from error
