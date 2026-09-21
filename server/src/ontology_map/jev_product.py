"""Production Jev judgments for extraction and existing-candidate matching."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

import httpx
from pydantic import SecretStr

from ontology_map.extraction_contracts import Modality
from ontology_map.jev_judgment import (
    BindingAssessment,
    BindingCase,
    PublicationAssessment,
    PublicationSource,
    Verdict,
    _publication_questions,
    _question,
    _verdict,
    publication_verdict,
)
from ontology_map.jev_selection import BATCH_SIZE, JevSelectionError, evaluate_nouls

IdentityVerdict = Literal["SAME", "DIFFERENT", "UNRESOLVED"]
T = TypeVar("T")


@dataclass(frozen=True)
class ProductClaimCase:
    case_id: str
    statement: str
    evidence: tuple[str, ...]
    source: PublicationSource
    bindings: tuple[BindingCase, ...] = ()


@dataclass(frozen=True)
class PublicationBindingAssessment:
    case_id: str
    publication: PublicationAssessment
    bindings: tuple[BindingAssessment, ...]


@dataclass(frozen=True)
class ClaimSupportCase:
    case_id: str
    statement: str
    modality: Modality
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ClaimSupportAssessment:
    case_id: str
    violation_scores: dict[str, float]
    verdict: Verdict


@dataclass(frozen=True)
class ExistingCandidateCase:
    case_id: str
    mention_text: str
    node_type: str
    context: tuple[str, ...]
    candidate_name: str
    candidate_aliases: tuple[str, ...] = ()
    candidate_identifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExistingCandidateAssessment:
    case_id: str
    same_score: float
    verdict: IdentityVerdict


@dataclass(frozen=True)
class JudgmentResult(Generic[T]):
    assessments: tuple[T, ...]
    calls: int
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    state_bytes: int


def _validate_cases(cases: tuple[object, ...], thresholds: tuple[float, float]) -> None:
    negative, positive = thresholds
    case_ids = [getattr(case, "case_id", None) for case in cases]
    if (
        not cases
        or len(case_ids) != len(set(case_ids))
        or any(
            not isinstance(case_id, str) or not case_id.strip() for case_id in case_ids
        )
        or not 0 <= negative < positive <= 1
    ):
        raise JevSelectionError("JEV_JUDGMENT_INPUT")


def _groups(cases: tuple[T, ...], sizes: tuple[int, ...]) -> list[tuple[T, ...]]:
    groups: list[tuple[T, ...]] = []
    current: list[T] = []
    count = 0
    for case, needed in zip(cases, sizes, strict=True):
        if needed < 1 or needed > BATCH_SIZE:
            raise JevSelectionError("JEV_INPUT_LIMIT")
        if current and count + needed > BATCH_SIZE:
            groups.append(tuple(current))
            current, count = [], 0
        current.append(case)
        count += needed
    if current:
        groups.append(tuple(current))
    return groups


def _binding_questions(claim_index: int, count: int) -> dict[str, object]:
    root = f"claims[{claim_index}]"
    questions: dict[str, object] = {}
    for index in range(count):
        binding = f"{root}.bindings[{index}]"
        questions[f"c{claim_index}_binding_{index}_participant"] = _question(
            "Are the binding participants, roles, and direction supported by the "
            "claim and evidence?",
            "Every participant and its source or target role agrees with the claim "
            "and evidence.",
            "A participant or role is invented, omitted, or reversed.",
            f"{root}.claim",
            f"{root}.evidence",
            binding,
        )
        questions[f"c{claim_index}_binding_{index}_meaning"] = _question(
            "Does this binding express a supported part of the claim under the "
            "supplied rule?",
            "Its predicate, stance, value, and time express a supported part and "
            "follow the rule.",
            "Its predicate, stance, value, time, or rule meaning is unsupported or "
            "changed.",
            f"{root}.claim",
            f"{root}.evidence",
            binding,
        )
    return questions


def _product_state(case: ProductClaimCase) -> dict[str, object]:
    source: dict[str, str] = {"kind": case.source.kind}
    if case.source.issuer is not None:
        source["verified_issuer"] = case.source.issuer
    return {
        "claim": case.statement,
        "evidence": list(case.evidence),
        "source": source,
        "bindings": [
            {
                "proposal": item.proposal,
                "mentions": list(item.mentions),
                "rule": item.rule,
            }
            for item in case.bindings
        ],
    }


def evaluate_publication_bindings(
    cases: tuple[ProductClaimCase, ...],
    *,
    api_key: SecretStr,
    publication_positive_threshold: float = 0.51,
    publication_negative_threshold: float = 0.46,
    binding_positive_threshold: float = 0.56,
    binding_negative_threshold: float = 0.30,
    transport: httpx.BaseTransport | None = None,
) -> JudgmentResult[PublicationBindingAssessment]:
    """Judge publication and graph bindings in batches of at most 64 questions."""
    _validate_cases(
        cases,
        (publication_negative_threshold, publication_positive_threshold),
    )
    _validate_cases(cases, (binding_negative_threshold, binding_positive_threshold))
    if any(
        not case.statement.strip()
        or not case.evidence
        or any(not evidence.strip() for evidence in case.evidence)
        or len({binding.case_id for binding in case.bindings}) != len(case.bindings)
        for case in cases
    ):
        raise JevSelectionError("JEV_JUDGMENT_INPUT")
    sizes = tuple(
        (3 if case.source.issuer is not None else 2) + 2 * len(case.bindings)
        for case in cases
    )
    groups = _groups(cases, sizes)
    started = time.monotonic()
    calls = input_tokens = output_tokens = state_bytes = 0
    assessments: list[PublicationBindingAssessment] = []
    for group in groups:
        state = {"claims": [_product_state(case) for case in group]}
        questions: dict[str, object] = {}
        for index, case in enumerate(group):
            questions.update(
                _publication_questions(index, official=case.source.issuer is not None)
            )
            questions.update(_binding_questions(index, len(case.bindings)))
        response = evaluate_nouls(
            state,
            questions,
            api_key=api_key,
            role="claim_review",
            transport=transport,
        )
        calls += 1
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        state_bytes += len(json.dumps(state, ensure_ascii=False).encode())
        for index, case in enumerate(group):
            interpretation = response.answers[f"c{index}_interpretation"].noul
            attribution = response.answers[f"c{index}_attribution"].noul
            official_self = (
                response.answers[f"c{index}_official_self"].noul
                if case.source.issuer is not None
                else None
            )
            bindings = tuple(
                BindingAssessment(
                    binding.case_id,
                    response.answers[
                        f"c{index}_binding_{binding_index}_participant"
                    ].noul,
                    response.answers[f"c{index}_binding_{binding_index}_meaning"].noul,
                    _verdict(
                        [
                            response.answers[
                                f"c{index}_binding_{binding_index}_participant"
                            ].noul,
                            response.answers[
                                f"c{index}_binding_{binding_index}_meaning"
                            ].noul,
                        ],
                        binding_positive_threshold,
                        binding_negative_threshold,
                    ),
                )
                for binding_index, binding in enumerate(case.bindings)
            )
            publication = PublicationAssessment(
                case.case_id,
                interpretation,
                attribution,
                official_self,
                publication_verdict(
                    interpretation,
                    attribution,
                    official_self,
                    positive_threshold=publication_positive_threshold,
                    negative_threshold=publication_negative_threshold,
                ),
            )
            assessments.append(
                PublicationBindingAssessment(case.case_id, publication, bindings)
            )
    return JudgmentResult(
        tuple(assessments),
        calls,
        input_tokens,
        output_tokens,
        time.monotonic() - started,
        state_bytes,
    )


_VIOLATIONS = {
    "core_change": (
        "Does the claim add, remove, or change a central subject, action, or object "
        "compared with the evidence?",
        "A central actor, action, or object differs or is unsupported.",
        "The central actor, action, and object are directly stated or plainly "
        "entailed.",
    ),
    "attribution_change": (
        "Does the claim change who said, announced, denied, reported, or owns an "
        "assertion in the evidence?",
        "The speaker, source, owner, or reported nature changes.",
        "Attribution agrees, or neither text makes an attribution.",
    ),
    "status_change": (
        "Does the claim change negation, denial, certainty, or "
        "plan-versus-completion status from the evidence?",
        "A denial becomes an action, a plan becomes completed, or certainty changes.",
        "Negation and status agree with the evidence.",
    ),
    "detail_change": (
        "Does the claim invent or materially change a quantity, date, place, or "
        "condition?",
        "At least one material detail is invented or changed.",
        "Every material detail is supported, or the claim contains none.",
    ),
}

_MODALITY_MEANINGS: dict[Modality, str] = {
    "FACT": "The claim reports an observed or completed fact.",
    "PLAN_OR_TARGET": "The claim reports a plan, intention, goal, or target.",
    "PREDICTION_OR_ESTIMATE": "The claim reports a prediction or estimate.",
    "OPINION_OR_EVALUATION": "The claim reports an attributed opinion or evaluation.",
}


def evaluate_claim_support(
    cases: tuple[ClaimSupportCase, ...],
    *,
    api_key: SecretStr,
    positive_threshold: float = 0.70,
    negative_threshold: float = 0.30,
    transport: httpx.BaseTransport | None = None,
) -> JudgmentResult[ClaimSupportAssessment]:
    """Return TRUE only when every unsupported-change detector is negative."""
    _validate_cases(cases, (negative_threshold, positive_threshold))
    if any(
        not case.statement.strip()
        or not case.evidence
        or any(not evidence.strip() for evidence in case.evidence)
        or case.modality not in _MODALITY_MEANINGS
        for case in cases
    ):
        raise JevSelectionError("JEV_JUDGMENT_INPUT")
    groups = _groups(cases, (len(_VIOLATIONS),) * len(cases))
    started = time.monotonic()
    calls = input_tokens = output_tokens = state_bytes = 0
    assessments: list[ClaimSupportAssessment] = []
    for group in groups:
        state = {
            "claims": [
                {
                    "claim": {
                        "statement": case.statement,
                        "modality": case.modality,
                        "modality_meaning": _MODALITY_MEANINGS[case.modality],
                    },
                    "evidence": list(case.evidence),
                }
                for case in group
            ]
        }
        questions: dict[str, object] = {
            f"c{index}_{name}": _question(
                question,
                true,
                false,
                f"claims[{index}].claim",
                f"claims[{index}].evidence",
            )
            for index in range(len(group))
            for name, (question, true, false) in _VIOLATIONS.items()
        }
        response = evaluate_nouls(
            state,
            questions,
            api_key=api_key,
            role="claim_review",
            transport=transport,
        )
        calls += 1
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        state_bytes += len(json.dumps(state, ensure_ascii=False).encode())
        for index, case in enumerate(group):
            scores = {
                name: response.answers[f"c{index}_{name}"].noul for name in _VIOLATIONS
            }
            verdict: Verdict = (
                "FALSE"
                if any(score >= positive_threshold for score in scores.values())
                else "TRUE"
                if all(score <= negative_threshold for score in scores.values())
                else "UNRESOLVED"
            )
            assessments.append(ClaimSupportAssessment(case.case_id, scores, verdict))
    return JudgmentResult(
        tuple(assessments),
        calls,
        input_tokens,
        output_tokens,
        time.monotonic() - started,
        state_bytes,
    )


def evaluate_existing_candidate_identity(
    cases: tuple[ExistingCandidateCase, ...],
    *,
    api_key: SecretStr,
    positive_threshold: float = 0.70,
    negative_threshold: float = 0.30,
    transport: httpx.BaseTransport | None = None,
) -> JudgmentResult[ExistingCandidateAssessment]:
    """Judge each mention/candidate pair without exposing internal node identifiers."""
    _validate_cases(cases, (negative_threshold, positive_threshold))
    if any(
        not case.mention_text.strip()
        or not case.node_type.strip()
        or not case.candidate_name.strip()
        for case in cases
    ):
        raise JevSelectionError("JEV_JUDGMENT_INPUT")
    groups = _groups(cases, (1,) * len(cases))
    started = time.monotonic()
    calls = input_tokens = output_tokens = state_bytes = 0
    assessments: list[ExistingCandidateAssessment] = []
    for group in groups:
        state = {
            "pairs": [
                {
                    "mention": {
                        "text": case.mention_text,
                        "node_type": case.node_type,
                        "context": list(case.context),
                    },
                    "candidate": {
                        "name": case.candidate_name,
                        "aliases": list(case.candidate_aliases),
                        "identifiers": list(case.candidate_identifiers),
                    },
                }
                for case in group
            ]
        }
        questions: dict[str, object] = {
            f"pair_{index}": _question(
                "Do the mention in context and the candidate identify the same "
                "real-world entity?",
                "Their type and available names, identifiers, and contextual facts "
                "establish the same entity.",
                "The supplied names, identifiers, or contextual facts establish "
                "that they are different entities.",
                f"pairs[{index}].mention",
                f"pairs[{index}].candidate",
            )
            for index in range(len(group))
        }
        response = evaluate_nouls(
            state,
            questions,
            api_key=api_key,
            role="entity_resolution",
            transport=transport,
        )
        calls += 1
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        state_bytes += len(json.dumps(state, ensure_ascii=False).encode())
        for index, case in enumerate(group):
            score = response.answers[f"pair_{index}"].noul
            verdict: IdentityVerdict = (
                "SAME"
                if score >= positive_threshold
                else "DIFFERENT"
                if score <= negative_threshold
                else "UNRESOLVED"
            )
            assessments.append(
                ExistingCandidateAssessment(case.case_id, score, verdict)
            )
    return JudgmentResult(
        tuple(assessments),
        calls,
        input_tokens,
        output_tokens,
        time.monotonic() - started,
        state_bytes,
    )
