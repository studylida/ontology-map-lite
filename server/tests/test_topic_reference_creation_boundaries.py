from unittest.mock import Mock

import pytest

from ontology_map import entity_resolution as service
from ontology_map.entity_resolution_contracts import (
    CandidateSet,
    EntityMention,
    Resolution,
    SourceContext,
    SourceRange,
    VerifiedContext,
)


def _mention() -> EntityMention:
    return EntityMention(
        mention_id="topic-ai",
        text="AI",
        node_type="TOPIC",
        approved_topic_name="인공지능",
        source_ranges=(SourceRange(source_document_id=1, start_char=0, end_char=2),),
    )


def _context() -> tuple[VerifiedContext, ...]:
    return (
        VerifiedContext(
            source=SourceContext(
                source_document_id=1,
                start_char=0,
                end_char=2,
                quote_text="AI",
            ),
            body_hash=b"b" * 32,
            quote_hash=b"q" * 32,
            language="ko",
        ),
    )


def test_topic_without_active_reference_is_unresolved_without_agent(
    monkeypatch,
) -> None:
    context = _context()
    monkeypatch.setattr(service.queries, "verified_context", Mock(return_value=context))
    lookup = Mock(return_value=None)
    monkeypatch.setattr(service, "find_topic_reference_by_name", lookup)
    agent = Mock(side_effect=AssertionError("TOPIC must not enter the Agent NEW path"))

    result = service.resolve_mention(Mock(), _mention(), agent)

    assert result.decision == "UNRESOLVED"
    assert result.node_id is None
    assert result.candidates == CandidateSet((), False)
    assert lookup.call_args.kwargs == {"active_only": True}
    agent.assert_not_called()


def test_forged_new_topic_cannot_enter_promotion(monkeypatch) -> None:
    mention = _mention()
    context = _context()
    candidates = CandidateSet((), False)
    forged = Resolution(
        mention=mention,
        decision="NEW",
        node_id=None,
        context=context,
        candidates=candidates,
        identifier_nodes=(),
    )
    session = Mock()
    session.in_transaction.return_value = True
    pending = Mock()
    insert_node = Mock(return_value=999)
    monkeypatch.setattr(service.queries, "require_pending_batch", pending)
    monkeypatch.setattr(service.queries, "verified_context", Mock(return_value=context))
    monkeypatch.setattr(service, "_topic_candidates", Mock(return_value=candidates))
    monkeypatch.setattr(service.queries, "_insert_node", insert_node)

    with pytest.raises(
        ValueError, match="Topic promotion must reuse an active reference"
    ):
        with service.resolved_nodes_for_promotion(
            session,
            7,
            (forged,),
            frozenset({mention.mention_id}),
        ):
            pass

    pending.assert_called_once_with(session, 7)
    insert_node.assert_not_called()
