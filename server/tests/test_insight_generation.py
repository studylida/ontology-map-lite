from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

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


def claim(claim_id: int, *, period_role: str = "IN_WINDOW") -> GroundedClaim:
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


def window(*claims: GroundedClaim, conflict: bool = False) -> InsightWindowInput:
    pairs = (
        (VisibleConflictPair(conflict_set_id=1, claim_ids=(1, 2)),) if conflict else ()
    )
    return InsightWindowInput(
        time_window="RECENT_90_DAYS",
        claims=tuple(claims),
        conflict_pairs=pairs,
    )


def prepared(*claims: GroundedClaim, conflict: bool = False) -> PreparedInsightBundle:
    ninety = window(*claims, conflict=conflict)
    year = InsightWindowInput(
        time_window="RECENT_1_YEAR",
        claims=tuple(claims),
        conflict_pairs=ninety.conflict_pairs,
    )
    return PreparedInsightBundle(
        promotion_batch_id=1,
        node_search_document_id=1,
        prior_insight_model_task_id=None,
        basis_ids=tuple(item.claim_id for item in claims),
        agent_input=InsightAgentInput(
            node_id=10,
            node_type="COMPANY",
            preferred_alias="가온",
            as_of_at=AS_OF,
            recent_90_days=ninety,
            recent_1_year=year,
        ),
    )


def ref(
    claim_id: int,
    *,
    role: str = "KEY_CLAIM",
    order: int = 1,
) -> InsightClaimReference:
    return InsightClaimReference(claim_id=claim_id, role=role, display_order=order)


def section(
    order: int,
    *refs: InsightClaimReference,
    title: str | None = None,
    synthesis: str | None = None,
) -> InsightSectionCandidate:
    return InsightSectionCandidate(
        display_order=order,
        title=title or f"주요 발견 {order}",
        synthesis_text=synthesis or f"여러 근거를 연결한 해석 {order}입니다.",
        claims=tuple(refs),
    )


def report(
    *sections: InsightSectionCandidate,
    question: str = "현재 자료가 보여주는 핵심 흐름은 무엇인가요?",
    title: str = "현재 자료에서 확인되는 핵심 흐름",
) -> InsightReportCandidate:
    return InsightReportCandidate(
        analysis_question_text=question,
        title=title,
        summary_text="자료에서 확인되는 핵심 결론을 요약합니다.",
        synthesis_text="여러 주요 발견을 연결해 분석 질문에 답합니다.",
        caveat_text="현재 공개 자료 범위 밖의 결과는 확인할 수 없습니다.",
        sections=tuple(sections),
    )


def bundle(
    ninety: InsightReportCandidate | None,
    year: InsightReportCandidate | None,
) -> InsightBundleProposal:
    return InsightBundleProposal(
        recent_90_days=InsightWindowProposal(report=ninety),
        recent_1_year=InsightWindowProposal(report=year),
    )


def test_structured_output_strictness_and_semantic_validation_boundary() -> None:
    with pytest.raises(ValidationError):
        InsightBundleProposal.model_validate(
            {
                "recent_90_days": {"report": None},
                "recent_1_year": {"report": None},
                "reasoning": "제품 DB에 저장하지 않는 필드",
            }
        )
    malformed_report = report()
    proposal = bundle(malformed_report, None)
    result = service.validate_bundle(prepared(claim(1), claim(2)), proposal)
    assert not result.valid
    assert "one to three sections" in result.failures[0].reason


def test_valid_bundle_allows_independent_windows_and_union_projection() -> None:
    snapshot = prepared(claim(1), claim(2), claim(3))
    ninety = report(
        section(1, ref(1), ref(2, role="SUPPORTING_CLAIM", order=2)),
        section(
            2,
            ref(2, role="CONTRASTING_CLAIM"),
            ref(3, order=2),
        ),
        question="최근 90일의 핵심 변화는 무엇인가요?",
    )
    year = report(
        section(1, ref(1), ref(3, role="SUPPORTING_CLAIM", order=2)),
        question="지난 1년의 전체 흐름은 무엇인가요?",
        title="지난 1년에서 보이는 전체 흐름",
    )
    result = service.validate_bundle(snapshot, bundle(ninety, year))
    assert result.valid
    assert ninety.analysis_question_text != year.analysis_question_text
    assert service.report_claim_projection(ninety) == (
        (1, "KEY_CLAIM", 1),
        (2, "SUPPORTING_CLAIM", 2),
        (3, "KEY_CLAIM", 3),
    )


def test_report_is_atomic_when_any_section_claim_contract_fails() -> None:
    snapshot = prepared(claim(1), claim(2), claim(3))
    invalid = report(
        section(1, ref(1)),
        section(2, ref(2), ref(3, order=2)),
    )
    result = service.validate_bundle(snapshot, bundle(invalid, None))
    assert not result.valid
    assert len(result.failures) == 1
    assert "at least two Claims" in result.failures[0].reason


def test_report_rejects_same_claim_set_when_only_title_changes() -> None:
    snapshot = prepared(claim(1), claim(2))
    duplicated = report(
        section(
            1,
            ref(1),
            ref(2, order=2),
            synthesis="같은 Claim 묶음에서 같은 종합을 설명합니다.",
        ),
        section(
            2,
            ref(2),
            ref(1, order=2),
            title="제목만 바꾼 반복 발견",
            synthesis="같은 Claim 묶음에서 같은 종합을 설명합니다.",
        ),
    )
    result = service.validate_bundle(snapshot, bundle(duplicated, None))
    assert not result.valid
    assert "same Claim set with the same synthesis" in result.failures[0].reason


def test_report_allows_same_claim_set_for_distinct_findings() -> None:
    snapshot = prepared(claim(1), claim(2))
    distinct = report(
        section(
            1,
            ref(1),
            ref(2, order=2),
            synthesis="첫 번째 발견은 두 근거가 보여주는 변화 시점에 초점을 둡니다.",
        ),
        section(
            2,
            ref(2),
            ref(1, order=2),
            synthesis=(
                "두 번째 발견은 같은 근거가 함께 보여주는 범위 한계에 초점을 둡니다."
            ),
        ),
    )
    assert service.validate_bundle(snapshot, bundle(distinct, None)).valid


def test_visible_conflict_requires_both_members_in_same_section() -> None:
    snapshot = prepared(claim(1), claim(2), claim(3), conflict=True)
    one_sided = report(section(1, ref(1), ref(3, order=2)))
    assert not service.validate_bundle(snapshot, bundle(one_sided, None)).valid

    neutral_pair = report(
        section(
            1,
            ref(1),
            ref(2, role="CONTRASTING_CLAIM", order=2),
        )
    )
    assert service.validate_bundle(snapshot, bundle(neutral_pair, None)).valid


def test_empty_empty_is_a_valid_product_result() -> None:
    result = service.validate_bundle(prepared(), bundle(None, None))
    assert result.valid
    assert result.recent_90_days is None
    assert result.recent_1_year is None


def test_structurally_impossible_bundle_skips_generation() -> None:
    empty = service.empty_bundle_if_structurally_impossible(prepared(claim(1)))
    assert empty == bundle(None, None)
    assert (
        service.empty_bundle_if_structurally_impossible(prepared(claim(1), claim(2)))
        is None
    )


def test_plain_text_contract_blocks_markdown_or_urls() -> None:
    snapshot = prepared(claim(1), claim(2))
    markdown = report(
        section(
            1,
            ref(1),
            ref(2, order=2),
            synthesis="- 근거를 나열합니다.",
        )
    )
    assert not service.validate_bundle(snapshot, bundle(markdown, None)).valid


def test_agent_payload_excludes_generated_grounding_and_urls() -> None:
    snapshot = prepared(claim(1), claim(2))
    human = service.build_messages(snapshot)[1][1]
    assert "context_text" not in human
    assert "FOLLOWUP" not in human
    assert "CONFLICT_SUMMARY" not in human
    assert "canonical_url" not in human
    assert '"claim_id":1' in human
