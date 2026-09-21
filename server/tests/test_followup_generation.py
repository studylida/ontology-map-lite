from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ontology_map import followup_generation as service
from ontology_map.followup_generation_contracts import (
    ClaimConnection,
    EvidenceExcerpt,
    FollowupAgentInput,
    FollowupClaimReference,
    FollowupQuestionCandidate,
    FollowupQuestionsProposal,
    FollowupWindowInput,
    FollowupWindowProposal,
    GroundedClaim,
    PreparedFollowup,
    VisibleConflictPair,
)

AS_OF = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)


def claim(claim_id: int, *, role: str = "IN_WINDOW") -> GroundedClaim:
    return GroundedClaim(
        claim_id=claim_id,
        statement_text=f"검증된 주장 {claim_id}",
        modality="FACT",
        period_role=role,
        connections=(ClaimConnection(kind="ATTRIBUTE", label="시험 속성"),),
        evidence=(
            EvidenceExcerpt(
                observation_id=claim_id,
                source_document_id=claim_id,
                evidence_group_id=claim_id,
                publisher_name="시험 발행처",
                title="시험 문서",
                quote_text=f"검증된 원문 {claim_id}",
                published_at=AS_OF,
                period_role=role,
            ),
        ),
    )


def prepared(*claims: GroundedClaim, conflict: bool = False) -> PreparedFollowup:
    pairs = (
        (VisibleConflictPair(conflict_set_id=1, claim_ids=(1, 2)),) if conflict else ()
    )
    return PreparedFollowup(
        promotion_batch_id=1,
        node_context_id=1,
        node_search_document_id=1,
        basis_ids=tuple(item.claim_id for item in claims),
        agent_input=FollowupAgentInput(
            node_id=10,
            node_type="COMPANY",
            preferred_alias="가온",
            as_of_at=AS_OF,
            recent_90_days=FollowupWindowInput(
                time_window="RECENT_90_DAYS",
                claims=tuple(claims),
                conflict_pairs=pairs,
            ),
            recent_1_year=FollowupWindowInput(
                time_window="RECENT_1_YEAR",
                claims=tuple(claims),
                conflict_pairs=pairs,
            ),
        ),
    )


def reference(
    claim_id: int,
    *,
    role: str = "KEY_CLAIM",
    display_order: int = 1,
) -> FollowupClaimReference:
    return FollowupClaimReference(
        claim_id=claim_id,
        role=role,
        display_order=display_order,
    )


def question(
    order: int,
    *refs: FollowupClaimReference,
    text: str | None = None,
    answer: str = "첫 번째 근거가 확인됩니다. 추가 해석은 제공된 범위로 제한됩니다.",
) -> FollowupQuestionCandidate:
    return FollowupQuestionCandidate(
        display_order=order,
        question_text=text or f"무엇을 확인할 수 있나요 {order}?",
        answer_text=answer,
        claims=tuple(refs),
    )


def test_structured_output_is_strict_and_limited_to_eight_questions() -> None:
    payload = {
        "questions": [question(i, reference(1)).model_dump() for i in range(1, 10)]
    }
    with pytest.raises(ValidationError):
        FollowupQuestionsProposal.model_validate(payload)
    with pytest.raises(ValidationError):
        FollowupQuestionsProposal.model_validate(
            {"questions": [], "reasoning": "제품 DB에 저장하지 않는 필드"}
        )


def test_partial_validation_keeps_only_independent_valid_candidates() -> None:
    snapshot = prepared(claim(1), claim(2))
    proposal = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                question(1, reference(1)),
                question(2, reference(999)),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    result = service.validate_proposal(snapshot, proposal)
    assert [item.display_order for item in result.recent_90_days.candidates] == [1]
    assert len(result.recent_90_days.failures) == 1
    assert result.recent_90_days.had_candidates


def test_empty_proposal_is_normal_zero_result() -> None:
    result = service.validate_proposal(
        prepared(claim(1)),
        FollowupQuestionsProposal(
            recent_90_days=FollowupWindowProposal(questions=()),
            recent_1_year=FollowupWindowProposal(questions=()),
        ),
    )
    assert result.recent_90_days.candidates == ()
    assert result.recent_90_days.failures == ()
    assert not result.recent_90_days.had_candidates


def test_candidate_requires_in_window_key_claim() -> None:
    snapshot = prepared(claim(1, role="BACKGROUND"), claim(2, role="IN_WINDOW"))
    background_key = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                question(
                    1,
                    reference(1, role="KEY_CLAIM", display_order=1),
                    reference(2, role="SUPPORTING_CLAIM", display_order=2),
                ),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    result = service.validate_proposal(snapshot, background_key)
    assert result.recent_90_days.candidates == ()
    assert len(result.recent_90_days.failures) == 1

    unknown_snapshot = prepared(claim(1, role="UNKNOWN"), claim(2, role="IN_WINDOW"))
    unknown_key = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                question(
                    1,
                    reference(1, role="KEY_CLAIM", display_order=1),
                    reference(2, role="SUPPORTING_CLAIM", display_order=2),
                ),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    result = service.validate_proposal(unknown_snapshot, unknown_key)
    assert result.recent_90_days.candidates == ()
    assert len(result.recent_90_days.failures) == 1


def test_background_and_unknown_may_support_in_window_key_claim() -> None:
    background_snapshot = prepared(
        claim(1, role="IN_WINDOW"), claim(2, role="BACKGROUND")
    )
    background_support = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                question(
                    1,
                    reference(1, role="KEY_CLAIM", display_order=1),
                    reference(2, role="SUPPORTING_CLAIM", display_order=2),
                ),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    assert (
        len(
            service.validate_proposal(
                background_snapshot, background_support
            ).recent_90_days.candidates
        )
        == 1
    )

    unknown_snapshot = prepared(claim(1, role="IN_WINDOW"), claim(2, role="UNKNOWN"))
    unknown_contrast = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                question(
                    1,
                    reference(1, role="KEY_CLAIM", display_order=1),
                    reference(2, role="CONTRASTING_CLAIM", display_order=2),
                ),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    assert (
        len(
            service.validate_proposal(
                unknown_snapshot, unknown_contrast
            ).recent_90_days.candidates
        )
        == 1
    )


def test_period_role_failure_keeps_other_question_partial_success() -> None:
    snapshot = prepared(claim(1, role="IN_WINDOW"), claim(2, role="BACKGROUND"))
    proposal = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                question(1, reference(1)),
                question(
                    2,
                    reference(2, role="KEY_CLAIM", display_order=1),
                    reference(1, role="SUPPORTING_CLAIM", display_order=2),
                ),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    result = service.validate_proposal(snapshot, proposal)
    assert [item.display_order for item in result.recent_90_days.candidates] == [1]
    assert len(result.recent_90_days.failures) == 1


def test_visible_conflict_requires_both_in_window_members_as_key_claims() -> None:
    snapshot = prepared(claim(1), claim(2), conflict=True)
    incomplete = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(questions=(question(1, reference(1)),)),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    assert (
        service.validate_proposal(snapshot, incomplete).recent_90_days.candidates == ()
    )

    complete = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                question(
                    1,
                    reference(1, display_order=1),
                    reference(2, display_order=2),
                ),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    assert (
        len(service.validate_proposal(snapshot, complete).recent_90_days.candidates)
        == 1
    )


def test_conflict_with_background_or_unknown_member_is_rejected_atomically() -> None:
    for period_role in ("BACKGROUND", "UNKNOWN"):
        snapshot = prepared(claim(1), claim(2, role=period_role), conflict=True)
        proposal = FollowupQuestionsProposal(
            recent_90_days=FollowupWindowProposal(
                questions=(
                    question(
                        1,
                        reference(1, display_order=1),
                        reference(2, display_order=2),
                    ),
                )
            ),
            recent_1_year=FollowupWindowProposal(questions=()),
        )
        result = service.validate_proposal(snapshot, proposal)
        assert result.recent_90_days.candidates == ()
        assert len(result.recent_90_days.failures) == 1


def test_obvious_duplicate_and_non_plain_text_are_blocked_per_candidate() -> None:
    snapshot = prepared(claim(1))
    proposal = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                question(1, reference(1), text="이 사실은 무엇인가요?"),
                question(2, reference(1), text="이  사실은 무엇인가요?!"),
                question(
                    3,
                    reference(1),
                    text="다른 질문인가요?",
                    answer="- 첫 근거입니다. 추가 설명입니다.",
                ),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )
    result = service.validate_proposal(snapshot, proposal)
    assert [item.display_order for item in result.recent_90_days.candidates] == [1]
    assert len(result.recent_90_days.failures) == 2


def test_agent_payload_contains_only_bounded_baseline_input() -> None:
    snapshot = prepared(claim(1))
    messages = service.build_messages(snapshot)
    human = messages[1][1]
    assert "context_text" not in human
    assert "CONFLICT_SUMMARY" not in human
    assert "canonical_url" not in human
    assert '"claim_id":1' in human
