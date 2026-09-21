from dataclasses import replace

import pytest
from test_extraction import candidate, limits, ontology, source_document

from ontology_map.extraction_contracts import ClaimProposal
from ontology_map.extraction_promotion import (
    _disambiguate_mention_ids,
    resolution_inputs,
)
from ontology_map.extraction_runner import RuntimeInput


def test_local_numeric_looking_ids_never_become_persistent_node_ids() -> None:
    payload = candidate(candidate_id="999999")
    payload["mentions"][0]["mention_id"] = "123456789"
    claim = ClaimProposal.model_validate(payload)
    document = source_document().model_copy(update={"document_id": "17"})
    mentions, dependencies = resolution_inputs(
        RuntimeInput(document, ontology(), limits()), (claim,)
    )
    by_id = {item.mention_id: item for item in mentions}
    assert "123456789" in by_id
    assert by_id["123456789"].external_identifiers == ()
    assert {item.source_document_id for item in by_id["123456789"].source_ranges} == {
        17
    }
    assert dependencies == {"999999": ("123456789", "m2")}


def test_reused_mention_id_must_have_one_identical_local_definition() -> None:
    first = ClaimProposal.model_validate(candidate(candidate_id="c1"))
    second_payload = candidate(candidate_id="c2")
    second_payload["mentions"][0]["text"] = "다른 회사"
    second = ClaimProposal.model_validate(second_payload)
    document = source_document().model_copy(update={"document_id": "17"})
    with pytest.raises(ValueError, match="CONFLICTING_LOCAL_MENTION_ID"):
        resolution_inputs(RuntimeInput(document, ontology(), limits()), (first, second))


def test_runtime_document_id_must_be_real_internal_integer_not_model_local_text() -> (
    None
):
    claim = ClaimProposal.model_validate(candidate())
    runtime = RuntimeInput(source_document(), ontology(), limits())
    with pytest.raises(ValueError, match="SOURCE_DOCUMENT_ID_NOT_INTERNAL_ID"):
        resolution_inputs(runtime, (claim,))
    changed = replace(
        runtime, document=runtime.document.model_copy(update={"document_id": "001"})
    )
    with pytest.raises(ValueError, match="SOURCE_DOCUMENT_ID_NOT_INTERNAL_ID"):
        resolution_inputs(changed, (claim,))


def test_exact_document_local_mentions_share_resolution_with_all_source_ranges() -> (
    None
):
    first = ClaimProposal.model_validate(candidate(candidate_id="c1", source="s0"))
    second = ClaimProposal.model_validate(candidate(candidate_id="c2", source="s1"))
    claims, projection_ids = _disambiguate_mention_ids((first, second))
    document = source_document().model_copy(update={"document_id": "17"})
    mentions, dependencies = resolution_inputs(
        RuntimeInput(document, ontology(), limits()), claims
    )
    by_id = {mention.mention_id: mention for mention in mentions}
    shared = projection_ids["c1"]["m1"]
    assert projection_ids["c2"]["m1"] == shared
    assert len(by_id[shared].source_ranges) == 2
    assert dependencies["c1"][0] == dependencies["c2"][0] == shared
