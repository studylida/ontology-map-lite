"""No-provider unit tests. These do not claim PostgreSQL or model quality coverage."""

import json
from dataclasses import replace
from hashlib import sha256
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from ontology_map import entity_resolution as service
from ontology_map.db import entity_resolution as db
from ontology_map.entity_resolution_contracts import (
    CandidateSet,
    EntityMention,
    ExternalIdentifier,
    NodeCandidate,
    NodeRecord,
    ResolutionInput,
    ResolutionProposal,
    SourceContext,
    SourceRange,
    VerifiedContext,
)

BODY = "한빛전자는 신제품을 개발한다."


def mention(**changes):
    values = {
        "mention_id": "m1",
        "text": "한빛전자",
        "node_type": "COMPANY",
        "source_ranges": (
            SourceRange(source_document_id=1, start_char=0, end_char=len(BODY)),
        ),
    }
    values.update(changes)
    return EntityMention(**values)


def record(
    node_id=10,
    node_type="COMPANY",
    *,
    usable=True,
    aliases=None,
    preferred_alias="한빛전자 주식회사",
):
    return NodeRecord(
        NodeCandidate(
            node_id=node_id,
            node_type=node_type,
            preferred_alias=preferred_alias,
            aliases=aliases or ("한빛",),
            external_identifiers=(),
        ),
        node_type_id=1,
        usable=usable,
    )


def contexts(text=BODY):
    digest = sha256(text.encode()).digest()
    return (
        VerifiedContext(
            SourceContext(
                source_document_id=1,
                start_char=0,
                end_char=len(text),
                quote_text=text,
            ),
            digest,
            digest,
            "ko",
        ),
    )


@pytest.fixture
def prepared(monkeypatch):
    context_query = Mock(return_value=contexts())
    id_query = Mock(return_value=())
    candidate_query = Mock(return_value=CandidateSet((record(),), False))
    active_query = Mock(return_value=1)
    monkeypatch.setattr(db, "verified_context", context_query)
    monkeypatch.setattr(db, "identifier_matches", id_query)
    monkeypatch.setattr(db, "name_candidates", candidate_query)
    monkeypatch.setattr(db, "active_type_id", active_query)
    return context_query, id_query, candidate_query, active_query


def test_unique_external_identifier_resolves_without_agent(prepared):
    _, ids, names, _ = prepared
    ids.return_value = (record(),)
    agent = Mock(side_effect=AssertionError("must not call Agent"))
    result = service.resolve_mention(
        Mock(),
        mention(
            external_identifiers=(
                ExternalIdentifier(
                    identifier_system="LEI", identifier_value="trusted-1"
                ),
            )
        ),
        agent,
    )
    assert (result.decision, result.node_id) == ("SAME", 10)
    names.assert_not_called()
    agent.assert_not_called()


@pytest.mark.parametrize(
    "matches",
    [
        (record(node_type="PERSON"),),
        (record(), record(11)),
        (record(usable=False),),
    ],
)
def test_external_conflict_cannot_fall_back_to_agent_or_new(prepared, matches):
    _, ids, names, _ = prepared
    ids.return_value = matches
    agent = Mock()
    result = service.resolve_mention(Mock(), mention(), agent)
    assert (result.decision, result.node_id) == ("UNRESOLVED", None)
    names.assert_not_called()
    agent.assert_not_called()


def test_alias_match_requires_agent_and_only_identity_context_is_sent(prepared):
    agent = Mock(return_value={"decision": "SAME", "node_id": 10})
    result = service.resolve_mention(Mock(), mention(), agent)
    assert result.decision == "SAME"
    messages = agent.call_args.args[0]
    assert messages[0] == ("system", service.SYSTEM_PROMPT)
    payload = json.loads(messages[1][1])
    assert set(payload) == {
        "mention_text",
        "node_type",
        "context",
        "candidates",
        "candidates_truncated",
    }
    assert set(payload["candidates"][0]) == {
        "node_id",
        "node_type",
        "preferred_alias",
        "aliases",
        "external_identifiers",
    }
    assert payload["context"][0]["quote_text"] == BODY
    assert not {"usable", "creation_rule", "knowledge_text", "reasoning"} & (
        payload.keys() | payload["candidates"][0].keys()
    )


def test_unique_exact_alias_still_requires_agent(prepared):
    prepared[2].return_value = CandidateSet(
        (record(preferred_alias="한빛전자"),), False
    )
    agent = Mock(return_value={"decision": "SAME", "node_id": 10})
    result = service.resolve_mention(Mock(), mention(), agent)
    assert (result.decision, result.node_id) == ("SAME", 10)
    agent.assert_called_once()


def test_pending_mentions_are_resolved_in_one_strict_batch(prepared):
    pending = (
        service.prepare_resolution(Mock(), mention()),
        service.prepare_resolution(Mock(), mention(mention_id="m2", text="푸른전자")),
    )
    proposer = Mock(
        return_value={
            "resolutions": [
                {"mention_id": "m1", "decision": "SAME", "node_id": 10},
                {"mention_id": "m2", "decision": "NEW", "node_id": None},
            ]
        }
    )
    result = service.finish_resolutions(pending, proposer)
    assert [(item.decision, item.node_id) for item in result] == [
        ("SAME", 10),
        ("NEW", None),
    ]
    assert [item.mention_id for item in proposer.call_args.args[0].mentions] == [
        "m1",
        "m2",
    ]
    with pytest.raises(ValueError, match="RESOLUTION_BATCH_COVERAGE"):
        service.finish_resolutions(
            pending,
            lambda _: {
                "resolutions": [{"mention_id": "m1", "decision": "SAME", "node_id": 10}]
            },
        )


@pytest.mark.parametrize("selected", [99, 11])
def test_unoffered_or_wrong_type_id_is_unresolved(prepared, selected):
    prepared[2].return_value = CandidateSet((record(), record(11, "PERSON")), False)
    agent = Mock(return_value={"decision": "SAME", "node_id": selected})
    result = service.resolve_mention(Mock(), mention(), agent)
    assert result.decision == "UNRESOLVED"


def test_unusable_saved_node_is_not_hidden_or_replaced_with_new(prepared):
    prepared[2].return_value = CandidateSet((record(usable=False),), False)
    agent = Mock(return_value={"decision": "SAME", "node_id": 10})
    result = service.resolve_mention(Mock(), mention(), agent)
    assert len(result.candidates.nodes) == 1
    assert result.decision == "UNRESOLVED"


@pytest.mark.parametrize(
    "decision, selected, expected",
    [
        ("NEW", None, "UNRESOLVED"),
        ("SAME", 10, "SAME"),
        ("UNRESOLVED", None, "UNRESOLVED"),
    ],
)
def test_truncation_allows_same_but_not_new(prepared, decision, selected, expected):
    prepared[2].return_value = CandidateSet(
        tuple(record(i) for i in range(10, 15)), True
    )
    agent = Mock(return_value={"decision": decision, "node_id": selected})
    result = service.resolve_mention(Mock(), mention(), agent)
    assert result.decision == expected
    assert len(json.loads(agent.call_args.args[0][1][1])["candidates"]) == 5


def test_complete_zero_candidates_still_requires_specificity_judgment(prepared):
    prepared[2].return_value = CandidateSet((), False)
    agent = Mock(return_value={"decision": "NEW", "node_id": None})
    session = Mock()
    result = service.resolve_mention(session, mention(), agent)
    assert result.decision == "NEW" and result.node_id is None
    agent.assert_called_once()
    session.execute.assert_not_called()  # queries above are isolated mocks


def test_inactive_type_cannot_create_new(prepared):
    prepared[3].return_value = None
    result = service.resolve_mention(
        Mock(),
        mention(),
        Mock(
            return_value={
                "decision": "NEW",
                "node_id": None,
            }
        ),
    )
    assert result.decision == "UNRESOLVED"


@pytest.mark.parametrize("name", ["회사", "업계", "관계자", "이 기술", "신기술"])
def test_generic_names_do_not_trigger_creation_or_name_judgment(prepared, name):
    agent = Mock()
    result = service.resolve_mention(Mock(), mention(text=name), agent)
    assert result.decision == "UNRESOLVED"
    agent.assert_not_called()


def test_topic_reuses_only_active_reference_and_does_not_call_agent(
    prepared, monkeypatch
):
    reference = Mock(node_id=10, canonical_display_name="인공지능")
    reference_lookup = Mock(return_value=reference)
    monkeypatch.setattr(service, "find_topic_reference_by_name", reference_lookup)
    target = mention(text="AI", node_type="TOPIC", approved_topic_name="인공지능")
    agent = Mock(side_effect=AssertionError("Topic resolution must be deterministic"))

    same = service.resolve_mention(Mock(), target, agent)

    assert same.decision == "SAME"
    assert same.node_id == 10
    assert same.candidates.nodes[0].candidate.preferred_alias == "인공지능"
    assert same.candidates.nodes[0].candidate.aliases == ()
    assert reference_lookup.call_args.args[1] == "인공지능"
    assert reference_lookup.call_args.kwargs == {"active_only": True}
    agent.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "AMBIGUOUS", "node_id": None},
        {"decision": "NEW", "node_id": 10},
        {"decision": "SAME", "node_id": None},
        {"decision": "SAME", "node_id": "10"},
        {"decision": "SAME", "node_id": True},
        {"decision": "NEW", "node_id": None, "reasoning": "not stored"},
        "not JSON",
    ],
)
def test_invalid_structured_output_is_not_silently_turned_into_new(prepared, payload):
    with pytest.raises(ValidationError):
        service.resolve_mention(Mock(), mention(), Mock(return_value=payload))


def test_proposal_schema_is_strict_and_has_exactly_three_outcomes():
    schema = ResolutionProposal.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"decision", "node_id"}
    assert schema["properties"]["decision"]["enum"] == ["SAME", "NEW", "UNRESOLVED"]
    with pytest.raises(ValidationError):
        ResolutionInput(
            mention_text="x",
            node_type="COMPANY",
            context=(),
            candidates=tuple(record(i).candidate for i in range(1, 7)),
            candidates_truncated=True,
        )


@pytest.mark.parametrize("which", ["source", "lookup", "provider"])
def test_failures_propagate_instead_of_becoming_empty_candidates(prepared, which):
    agent = Mock(return_value={"decision": "NEW", "node_id": None})
    boundary = {"source": prepared[0], "lookup": prepared[2], "provider": agent}[which]
    boundary.side_effect = RuntimeError("boundary failed")
    with pytest.raises(RuntimeError, match="boundary failed"):
        service.resolve_mention(Mock(), mention(), agent)


def test_dependency_filter_preserves_whole_claim_and_independent_results(prepared):
    valid = service.resolve_mention(
        Mock(),
        mention(),
        Mock(
            return_value={
                "decision": "NEW",
                "node_id": None,
            }
        ),
    )
    missing = replace(valid, mention=mention(mention_id="m2"), decision="UNRESOLVED")
    selection = service.select_resolvable_knowledge(
        (valid, missing),
        {
            "joint-claim": ("m1", "m2"),
            "independent-claim": ("m1",),
        },
    )
    assert selection.accepted == ("independent-claim",)
    assert selection.excluded == ("joint-claim",)
    assert selection.mention_ids == {"m1"}
    with pytest.raises(ValueError, match="unknown"):
        service.select_resolvable_knowledge((valid,), {"broken": ("other",)})
    with pytest.raises(ValueError, match="unique"):
        service.select_resolvable_knowledge((valid, valid), {})


def source_session(body=BODY, stored_hash=None):
    session = Mock()
    session.execute.return_value.mappings.return_value.one.return_value = {
        "normalized_body": body,
        "body_hash": stored_hash or sha256(body.encode()).digest(),
        "original_language": "ko",
    }
    return session


def test_real_context_function_reconstructs_unicode_offsets_and_hash():
    body = "🐾 " + BODY
    target = mention(
        source_ranges=(
            SourceRange(source_document_id=1, start_char=2, end_char=len(body)),
        )
    )
    result = db.verified_context(source_session(body), target)
    assert result[0].source.quote_text == BODY
    assert result[0].source.start_char == 2
    assert result[0].quote_hash == sha256(BODY.encode()).digest()


@pytest.mark.parametrize(
    "session, target, pattern",
    [
        (source_session(stored_hash=b"x" * 32), mention(), "hash mismatch"),
        (source_session(), mention(text="없는 회사"), "absent"),
        (
            source_session(),
            mention(
                source_ranges=(
                    SourceRange(
                        source_document_id=1, start_char=0, end_char=len(BODY) + 1
                    ),
                )
            ),
            "exceeds",
        ),
        (source_session(BODY + "\r\n"), mention(), "NFC/LF"),
    ],
)
def test_source_context_rejects_invalid_originals(session, target, pattern):
    with pytest.raises(ValueError, match=pattern):
        db.verified_context(session, target)


def test_name_query_fetches_six_canonical_nodes_without_search_documents(monkeypatch):
    ids = Mock(return_value=[10, 11, 12, 13, 14, 15])
    load = Mock(side_effect=lambda session, values: tuple(record(i) for i in values))
    monkeypatch.setattr(db, "_resolved_ids", ids)
    monkeypatch.setattr(db, "_load_records", load)
    result = db.name_candidates(Mock(), mention())
    assert result.truncated and len(result.nodes) == 5
    sql, params = ids.call_args.args[1:]
    assert params["limit"] == 6
    assert "GROUP BY node_id" in sql and "LIMIT :limit" in sql
    assert "reversed_at IS NULL" in sql and "w.cycle" in sql
    assert "plainto_tsquery('simple'" in sql
    for forbidden in (
        "node_search_document",
        "identity_text",
        "knowledge_text",
        "READY",
    ):
        assert forbidden not in sql


def test_corrupt_redirect_does_not_return_an_empty_lookup():
    session = Mock()
    session.execute.return_value.mappings.return_value.all.return_value = [
        {"node_id": 1, "invalid": True}
    ]
    with pytest.raises(ValueError, match="lookup did not complete"):
        db._resolved_ids(session, "SELECT :value", {"value": 1})


@pytest.fixture
def staged(prepared, monkeypatch):
    result = service.resolve_mention(
        Mock(),
        mention(),
        Mock(
            return_value={
                "decision": "NEW",
                "node_id": None,
            }
        ),
    )
    pending = Mock()
    insert = Mock(return_value=100)
    observation = Mock(return_value=200)
    alias = Mock()
    usage = Mock(return_value=True)
    monkeypatch.setattr(db, "require_pending_batch", pending)
    monkeypatch.setattr(db, "_insert_node", insert)
    monkeypatch.setattr(db, "_ensure_observation", observation)
    monkeypatch.setattr(db, "_ensure_alias", alias)
    monkeypatch.setattr(db, "has_evidenced_usage", usage)
    session = Mock()
    session.in_transaction.return_value = True
    return result, session, pending, insert, observation, alias, usage


def test_new_is_materialized_only_for_surviving_knowledge_and_never_commits(staged):
    result, session, _, insert, _, alias, usage = staged
    with service.resolved_nodes_for_promotion(
        session, 7, (result,), frozenset({"m1"})
    ) as nodes:
        assert nodes["m1"].node_id == 100
        assert nodes["m1"].observation_ids == (200,)
        usage.assert_not_called()  # The owning writer runs in this body.
    insert.assert_called_once_with(session, 7, 1)
    alias.assert_called_once_with(
        session, 7, 100, "한빛전자", "ko", 200, preferred=True
    )
    usage.assert_called_once_with(session, 100, (200,))
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_all_dependents_excluded_leaves_no_node_observation_or_alias(staged):
    result, session, pending, insert, observation, alias, usage = staged
    with service.resolved_nodes_for_promotion(
        session, 7, (result,), frozenset()
    ) as nodes:
        assert not nodes
    for operation in (pending, insert, observation, alias, usage):
        operation.assert_not_called()


def test_orphan_new_raises_to_the_owning_transaction(staged):
    result, session, _, _, _, _, usage = staged
    usage.return_value = False
    with pytest.raises(ValueError, match="no surviving evidenced knowledge"):
        with service.resolved_nodes_for_promotion(
            session, 7, (result,), frozenset({"m1"})
        ):
            pass
    session.commit.assert_not_called()


def test_writer_failure_is_not_swallowed_and_no_partial_commit_is_attempted(staged):
    result, session, _, _, _, _, usage = staged
    with pytest.raises(RuntimeError, match="consumer write failed"):
        with service.resolved_nodes_for_promotion(
            session, 7, (result,), frozenset({"m1"})
        ):
            raise RuntimeError("consumer write failed")
    usage.assert_not_called()
    session.commit.assert_not_called()


def test_node_creation_requires_a_transaction(staged):
    result, session, _, insert, _, _, _ = staged
    session.in_transaction.return_value = False
    with pytest.raises(ValueError, match="caller-owned"):
        with service.resolved_nodes_for_promotion(
            session, 7, (result,), frozenset({"m1"})
        ):
            pass
    insert.assert_not_called()


@pytest.mark.parametrize("changed", ["context", "identifiers", "candidates", "type"])
def test_stale_resolution_fails_before_creating_a_node(prepared, staged, changed):
    result, session, _, insert, _, _, _ = staged
    if changed == "context":
        prepared[0].return_value = contexts("한빛전자는 바뀌었다.")
    elif changed == "identifiers":
        prepared[1].return_value = (record(77),)
    elif changed == "candidates":
        prepared[2].return_value = CandidateSet((record(77),), False)
    else:
        prepared[3].return_value = None
    with pytest.raises(ValueError):
        with service.resolved_nodes_for_promotion(
            session, 7, (result,), frozenset({"m1"})
        ):
            pass
    insert.assert_not_called()


def test_unresolved_mention_cannot_be_forced_into_promotion(staged):
    result, session, _, insert, _, _, _ = staged
    result = replace(result, decision="UNRESOLVED")
    with pytest.raises(ValueError, match="unresolved"):
        with service.resolved_nodes_for_promotion(
            session, 7, (result,), frozenset({"m1"})
        ):
            pass
    insert.assert_not_called()


def test_same_does_not_create_node_or_change_preferred_alias(prepared, staged):
    _, session, _, insert, _, alias, _ = staged
    result = service.resolve_mention(
        session,
        mention(),
        Mock(
            return_value={
                "decision": "SAME",
                "node_id": 10,
            }
        ),
    )
    with service.resolved_nodes_for_promotion(
        session, 7, (result,), frozenset({"m1"})
    ) as nodes:
        assert nodes["m1"].node_id == 10
    insert.assert_not_called()
    assert alias.call_args.kwargs == {"preferred": False}


def test_generic_expression_resolved_by_identifier_is_not_added_as_alias(
    prepared, staged
):
    _, session, _, insert, _, alias, _ = staged
    prepared[1].return_value = (record(),)
    result = service.resolve_mention(session, mention(text="이 회사"), Mock())
    assert result.decision == "SAME"
    with service.resolved_nodes_for_promotion(session, 7, (result,), frozenset({"m1"})):
        pass
    insert.assert_not_called()
    alias.assert_not_called()


def test_alias_reuses_existing_identity_family_without_new_alias_row(monkeypatch):
    session = Mock()
    session.execute.return_value.scalar_one_or_none.return_value = 90
    evidence = Mock(return_value=True)
    monkeypatch.setattr(db.provenance, "add_node_alias_evidence", evidence)

    db._ensure_alias(session, 7, 10, "한빛전자", "ko", 200, preferred=False)

    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert len(statements) == 1
    assert "node_merge" in statements[0]
    assert all("INSERT INTO node_alias (" not in sql for sql in statements)
    evidence.assert_called_once_with(session, 7, 90, 200)
    session.commit.assert_not_called()


def test_existing_observation_is_not_silently_overwritten():
    session = Mock()
    session.execute.return_value.mappings.return_value.one.return_value = {
        "observation_id": 3,
        "quote_text": "altered",
        "quote_hash": b"x" * 32,
    }
    with pytest.raises(ValueError, match="does not match"):
        db._ensure_observation(session, contexts()[0])
    statements = [str(call.args[0]) for call in session.execute.call_args_list]
    assert all("DO UPDATE" not in sql for sql in statements)


def test_duplicate_or_cross_document_context_and_untrusted_identity_fields_rejected():
    location = mention().source_ranges[0]
    with pytest.raises(ValidationError, match="duplicate source"):
        mention(source_ranges=(location, location))
    with pytest.raises(ValidationError, match="one document"):
        mention(
            source_ranges=(
                location,
                location.model_copy(update={"source_document_id": 2}),
            )
        )
    with pytest.raises(ValidationError):
        ExternalIdentifier(identifier_system="invented", identifier_value="1")
    with pytest.raises(ValidationError):
        NodeCandidate(**record().candidate.model_dump(), summary="forbidden")
