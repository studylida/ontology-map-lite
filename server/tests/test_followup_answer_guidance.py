from datetime import UTC, datetime

from ontology_map import followup_generation as service
from ontology_map import followup_runner
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
)


def test_one_sentence_answer_is_not_dropped_by_deterministic_validation() -> None:
    as_of = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    grounded = GroundedClaim(
        claim_id=1,
        statement_text="검증된 주장",
        modality="FACT",
        period_role="IN_WINDOW",
        connections=(ClaimConnection(kind="ATTRIBUTE", label="시험 속성"),),
        evidence=(
            EvidenceExcerpt(
                observation_id=1,
                source_document_id=1,
                evidence_group_id=1,
                publisher_name="시험 발행처",
                title="시험 문서",
                quote_text="검증된 원문",
                published_at=as_of,
                period_role="IN_WINDOW",
            ),
        ),
    )
    prepared = PreparedFollowup(
        promotion_batch_id=1,
        node_context_id=1,
        node_search_document_id=1,
        basis_ids=(1,),
        agent_input=FollowupAgentInput(
            node_id=1,
            node_type="COMPANY",
            preferred_alias="가온",
            as_of_at=as_of,
            recent_90_days=FollowupWindowInput(
                time_window="RECENT_90_DAYS", claims=(grounded,)
            ),
            recent_1_year=FollowupWindowInput(
                time_window="RECENT_1_YEAR", claims=(grounded,)
            ),
        ),
    )
    proposal = FollowupQuestionsProposal(
        recent_90_days=FollowupWindowProposal(
            questions=(
                FollowupQuestionCandidate(
                    display_order=1,
                    question_text="무엇을 확인할 수 있나요?",
                    answer_text="현재 공개 근거에서 이 사실을 확인할 수 있습니다.",
                    claims=(
                        FollowupClaimReference(
                            claim_id=1,
                            role="KEY_CLAIM",
                            display_order=1,
                        ),
                    ),
                ),
            )
        ),
        recent_1_year=FollowupWindowProposal(questions=()),
    )

    result = service.validate_proposal(prepared, proposal)
    assert len(result.recent_90_days.candidates) == 1
    assert result.recent_90_days.failures == ()


def test_no_in_window_claims_are_detected_before_provider_call() -> None:
    as_of = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    claim = GroundedClaim(
        claim_id=1,
        statement_text="배경 주장",
        modality="FACT",
        period_role="BACKGROUND",
        connections=(ClaimConnection(kind="ATTRIBUTE", label="시험 속성"),),
        evidence=(
            EvidenceExcerpt(
                observation_id=1,
                source_document_id=1,
                evidence_group_id=1,
                publisher_name="시험 발행처",
                title="시험 문서",
                quote_text="배경 원문",
                published_at=as_of,
                period_role="BACKGROUND",
            ),
        ),
    )
    prepared = PreparedFollowup(
        promotion_batch_id=1,
        node_context_id=1,
        node_search_document_id=1,
        basis_ids=(1,),
        agent_input=FollowupAgentInput(
            node_id=1,
            node_type="COMPANY",
            preferred_alias="가온",
            as_of_at=as_of,
            recent_90_days=FollowupWindowInput(
                time_window="RECENT_90_DAYS", claims=(claim,)
            ),
            recent_1_year=FollowupWindowInput(
                time_window="RECENT_1_YEAR", claims=(claim,)
            ),
        ),
    )

    assert service.has_in_window_claims(prepared) is False


def test_runner_finishes_without_provider_for_background_only_input(
    monkeypatch,
) -> None:
    as_of = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    claim = GroundedClaim(
        claim_id=1,
        statement_text="배경 주장",
        modality="FACT",
        period_role="BACKGROUND",
        connections=(ClaimConnection(kind="ATTRIBUTE", label="시험 속성"),),
        evidence=(
            EvidenceExcerpt(
                observation_id=1,
                source_document_id=1,
                evidence_group_id=1,
                publisher_name="시험 발행처",
                title="시험 문서",
                quote_text="배경 원문",
                published_at=as_of,
                period_role="BACKGROUND",
            ),
        ),
    )
    prepared = PreparedFollowup(
        promotion_batch_id=1,
        node_context_id=1,
        node_search_document_id=1,
        basis_ids=(1,),
        agent_input=FollowupAgentInput(
            node_id=1,
            node_type="COMPANY",
            preferred_alias="가온",
            as_of_at=as_of,
            recent_90_days=FollowupWindowInput(
                time_window="RECENT_90_DAYS", claims=(claim,)
            ),
            recent_1_year=FollowupWindowInput(
                time_window="RECENT_1_YEAR", claims=(claim,)
            ),
        ),
    )
    monkeypatch.setattr(
        followup_runner,
        "_current_prepared",
        lambda *args, **kwargs: prepared,
    )
    monkeypatch.setattr(
        followup_runner,
        "_finalize",
        lambda *args, **kwargs: followup_runner.RunnerResult("SUCCESS", "APPLIED"),
    )

    def provider(_prepared):
        raise AssertionError("provider must not be called")

    result = followup_runner._run_claimed(
        object(),
        object(),
        node_context_id=1,
        as_of_at=as_of,
        prepare_provider=provider,
    )
    assert result.task_status == "SUCCESS"
