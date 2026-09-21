"""Finite, independent review aggregation. Never send these labels to a model."""

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from ontology_map.extraction import ExtractionResult

CriticalError = Literal["ACTOR", "ATTRIBUTION", "PLAN", "NEGATION", "TIME", "QUANTITY"]


@dataclass(frozen=True)
class CandidateReview:
    candidate_id: str
    evidence_supported: bool
    correct: bool
    required_fact_ids: frozenset[str]
    duplicate_group: str
    critical_errors: frozenset[CriticalError]


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _review_index(
    reviews: list[CandidateReview], ids: set[str], required_fact_ids: frozenset[str]
) -> dict[str, CandidateReview]:
    by_id = {r.candidate_id: r for r in reviews}
    if len(by_id) != len(reviews) or by_id.keys() != ids:
        raise ValueError("REVIEW_COVERAGE")
    for review in reviews:
        if (
            not review.required_fact_ids <= required_fact_ids
            or not review.duplicate_group
        ):
            raise ValueError("REVIEW_REFERENCE")
        if review.correct and (review.critical_errors or not review.evidence_supported):
            raise ValueError("INCONSISTENT_REVIEW")
    return by_id


def summarize(
    result: ExtractionResult,
    required_fact_ids: frozenset[str],
    reviews: list[CandidateReview],
    *,
    final_reviews: list[CandidateReview],
) -> dict[str, object]:
    """Final here means returned proposals, not DB promotion or a quality-gate pass."""
    generated_ids = {c.candidate_id for c in result.generated}
    by_id = _review_index(reviews, generated_ids, required_fact_ids)
    final_ids = {c.candidate_id for c in result.verified}
    # The final bindings can differ from generation; never reuse its labels silently.
    final = list(_review_index(final_reviews, final_ids, required_fact_ids).values())
    generated_facts = set().union(*(r.required_fact_ids for r in reviews if r.correct))
    final_facts = set().union(*(r.required_fact_ids for r in final if r.correct))
    final_groups = {r.duplicate_group for r in final}
    critical_groups = {r.duplicate_group for r in final if r.critical_errors}
    judged = [by_id[i] for i in result.support_verdicts]
    wrong = [r for r in judged if not r.evidence_supported]
    correct = [r for r in judged if r.evidence_supported]
    false_accepts = sum(
        result.support_verdicts[r.candidate_id] == "TRUE" for r in wrong
    )
    false_rejects = sum(
        result.support_verdicts[r.candidate_id] != "TRUE" for r in correct
    )
    return {
        "status": result.status,
        "generated_candidates": len(result.generated),
        "verified_candidates": len(final),
        "exact_duplicates": len(result.duplicates),
        "required_facts": len(required_fact_ids),
        "generated_required_facts": len(generated_facts),
        "final_required_facts": len(final_facts),
        "generated_retention": _rate(len(generated_facts), len(required_fact_ids)),
        "final_retention": _rate(len(final_facts), len(required_fact_ids)),
        "final_nonduplicate_claims": len(final_groups),
        "valid_nonduplicate_yield": len(
            {r.duplicate_group for r in final if r.correct}
        ),
        "final_critical_claims": len(critical_groups),
        "final_critical_error_rate": _rate(len(critical_groups), len(final_groups)),
        "generated_critical_claims": sum(bool(r.critical_errors) for r in reviews),
        "generated_critical_error_rate": _rate(
            sum(bool(r.critical_errors) for r in reviews), len(reviews)
        ),
        "support_false_accepts": false_accepts,
        "support_wrong_judged": len(wrong),
        "support_false_accept_rate": _rate(false_accepts, len(wrong)),
        "support_false_rejects": false_rejects,
        "support_correct_judged": len(correct),
        "support_false_reject_rate": _rate(false_rejects, len(correct)),
        "final_incorrect_nonduplicate_claims": len(
            {r.duplicate_group for r in final if not r.correct}
        ),
        "unjudged": len(
            generated_ids - result.support_verdicts.keys() - result.duplicates.keys()
        ),
        "excluded_candidates": len(
            generated_ids - final_ids - result.duplicates.keys()
        ),
        "exclusion_reasons": dict(Counter(e.code for e in result.exclusions)),
        "excluded_bindings": sum(len(e.binding_ids) for e in result.exclusions),
        "unresolved": sum(v == "UNRESOLVED" for v in result.support_verdicts.values())
        + sum(
            outcome == "MEANING_UNRESOLVED"
            for outcomes in result.binding_outcomes.values()
            for outcome in outcomes.values()
        ),
        "empty_final": not final_groups,
    }
