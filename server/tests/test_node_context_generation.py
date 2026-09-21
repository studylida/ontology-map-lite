"""Provider-independent NODE_CONTEXT product-contract tests for issue #215."""

import pytest
from pydantic import ValidationError

from ontology_map import node_context_generation as generation
from ontology_map.node_context_generation_contracts import (
    NodeContextAgentInput,
    NodeContextProposal,
    PreparedNodeContext,
)


def _prepared() -> PreparedNodeContext:
    return PreparedNodeContext(
        promotion_batch_id=17,
        node_search_document_id=31,
        search_document_input_hash=b"s" * 32,
        input_hash=b"i" * 32,
        agent_input=NodeContextAgentInput(
            node_id=7,
            node_type="COMPANY",
            preferred_alias="예시 회사",
            identity_text="예시 회사\nExample Corp",
            knowledge_text="유형: 회사\n주장: 검증된 공개 지식",
            basis_ids=(7, 11),
        ),
    )


def test_context_output_has_no_normal_empty() -> None:
    with pytest.raises(ValidationError):
        NodeContextProposal(context_text="   ")


def test_context_input_surface_excludes_window_and_derived_products() -> None:
    fields = set(NodeContextAgentInput.model_fields)
    assert fields == {
        "node_id",
        "node_type",
        "preferred_alias",
        "identity_text",
        "knowledge_text",
        "basis_ids",
    }
    assert {
        "time_window",
        "context_text",
        "followup_questions",
        "node_insight",
    }.isdisjoint(fields)


def test_prompt_uses_only_deterministic_search_input() -> None:
    messages = generation.build_messages(_prepared())
    assert [role for role, _text in messages] == ["system", "human"]
    system = messages[0][1]
    human = messages[1][1]
    assert "FOLLOWUP_QUESTIONS" in system
    assert "NODE_INSIGHT" in system
    assert "사용하지 않는다" in system
    assert '"identity_text":"예시 회사\\nExample Corp"' in human
    assert '"knowledge_text":"유형: 회사\\n주장: 검증된 공개 지식"' in human
    assert "time_window" not in human
    assert "context_text" not in human


def test_output_schema_and_parser_are_strict() -> None:
    schema = generation.output_schema()
    assert schema["additionalProperties"] is False
    parsed = generation.parse_proposal({"context_text": "짧은 한국어 맥락 설명"})
    assert parsed.context_text == "짧은 한국어 맥락 설명"
    with pytest.raises(ValidationError):
        generation.parse_proposal(
            {"context_text": "설명", "reasoning": "저장하면 안 되는 필드"}
        )
