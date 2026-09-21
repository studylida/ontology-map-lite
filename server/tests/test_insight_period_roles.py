from datetime import UTC, datetime

from ontology_map import insight_generation as service
from ontology_map.insight_generation_contracts import (
    ClaimConnection,
    EvidenceExcerpt,
    GroundedClaim,
    InsightAgentInput,
    InsightBundleProposal,
    InsightClaimReference,
    InsightReportCandidate,
    InsightSectionCandidate,
    InsightWindowInput,
    InsightWindowProposal,
    PreparedInsightBundle,
    VisibleConflictPair,
)

AS_OF = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)


def _claim(claim_id: int, period_role: str) -> GroundedClaim:
    return GroundedClaim(
        claim_id=claim_id,
        statement_text=f"검증된 주장 {claim_id}",
        modality="FACT",
        period_role=period_role,
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
                period_role=period_role,
            ),
        ),
    )


def _prepared(
    first_role: str,
    second_role: str,
    *,
    conflict: bool = False,
) -> PreparedInsightBundle:
    claims = (_claim(1, first_role), _claim(2, second_role))
    pairs = (
        (VisibleConflictPair(conflict_set_id=1, claim_ids=(1, 2)),) if conflict else ()
    )
    ninety = InsightWindowInput(
        time_window="RECENT_90_DAYS",
        claims=claims,
        conflict_pairs=pairs,
    )
    year = InsightWindowInput(
        time_window="RECENT_1_YEAR",
        claims=claims,
        conflict_pairs=pairs,
    )
    return PreparedInsightBundle(
        promotion_batch_id=1,
        node_search_document_id=1,
        prior_insight_model_task_id=None,
        basis_ids=(1, 2),
        agent_input=InsightAgentInput(
            node_id=10,
            node_type="COMPANY",
            preferred_alias="가온",
            as_of_at=AS_OF,
            recent_90_days=ninety,
            recent_1_year=year,
        ),
    )


def _report(
    first_role: str,
    second_role: str,
    *,
    include_second: bool = True,
) -> InsightReportCandidate:
    refs = [
        InsightClaimReference(
            claim_id=1,
            role=first_role,
            display_order=1,
        )
    ]
    if include_second:
        refs.append(
            InsightClaimReference(
                claim_id=2,
                role=second_role,
                display_order=2,
            )
        )
    return InsightReportCandidate(
        analysis_question_text="현재 자료에서 무엇을 확인할 수 있나요?",
        title="현재 자료의 핵심 발견",
        summary_text="기간 안 핵심 근거를 중심으로 요약합니다.",
        synthesis_text="두 근거가 함께 보여주는 범위와 한계를 설명합니다.",
        caveat_text="제공된 자료 범위 밖의 사실은 확인할 수 없습니다.",
        sections=(
            InsightSectionCandidate(
                display_order=1,
                title="근거를 함께 본 핵심 발견",
                synthesis_text="두 근거를 함께 보면 확인 범위가 드러납니다.",
                claims=tuple(refs),
            ),
        ),
    )


def _bundle(report: InsightReportCandidate) -> InsightBundleProposal:
    return InsightBundleProposal(
        recent_90_days=InsightWindowProposal(report=report),
        recent_1_year=InsightWindowProposal(report=None),
    )


def test_background_or_unknown_key_is_invalid_even_with_in_window_support() -> None:
    for period_role in ("BACKGROUND", "UNKNOWN"):
        snapshot = _prepared(period_role, "IN_WINDOW")
        candidate = _report("KEY_CLAIM", "SUPPORTING_CLAIM")
        result = service.validate_bundle(snapshot, _bundle(candidate))
        assert not result.valid
        assert "KEY_CLAIM must be an in-window Claim" in result.failures[0].reason


def test_background_or_unknown_may_support_or_contrast_in_window_key() -> None:
    background = _prepared("IN_WINDOW", "BACKGROUND")
    supporting = _report("KEY_CLAIM", "SUPPORTING_CLAIM")
    assert service.validate_bundle(background, _bundle(supporting)).valid

    unknown = _prepared("IN_WINDOW", "UNKNOWN")
    contrasting = _report("KEY_CLAIM", "CONTRASTING_CLAIM")
    assert service.validate_bundle(unknown, _bundle(contrasting)).valid


def test_conflict_allows_in_window_key_with_background_contrasting_member() -> None:
    snapshot = _prepared("IN_WINDOW", "BACKGROUND", conflict=True)
    candidate = _report("KEY_CLAIM", "CONTRASTING_CLAIM")
    assert service.validate_bundle(snapshot, _bundle(candidate)).valid


def test_conflict_still_blocks_when_one_member_is_missing() -> None:
    snapshot = _prepared("IN_WINDOW", "BACKGROUND", conflict=True)
    candidate = _report(
        "KEY_CLAIM",
        "CONTRASTING_CLAIM",
        include_second=False,
    )
    result = service.validate_bundle(snapshot, _bundle(candidate))
    assert not result.valid
    assert "at least two Claims" in result.failures[0].reason
