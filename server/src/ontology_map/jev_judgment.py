"""Read-only Jev calibration for claim and graph-binding judgments."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import SecretStr

from ontology_map.jev_selection import BATCH_SIZE, JevSelectionError, evaluate_nouls

Verdict = Literal["TRUE", "FALSE", "UNRESOLVED"]
PublicationVerdict = Literal["PUBLISH", "EDITORIAL_INTERPRETATION", "UNRESOLVED"]
SourceKind = Literal["NEWS", "OFFICIAL_COMPANY", "OFFICIAL_GOVERNMENT", "OTHER"]


@dataclass(frozen=True)
class BindingCase:
    case_id: str
    proposal: dict[str, object]
    mentions: tuple[dict[str, str], ...]
    rule: dict[str, object]


@dataclass(frozen=True)
class ClaimCase:
    case_id: str
    statement: str
    modality: str
    evidence: tuple[str, ...]
    bindings: tuple[BindingCase, ...] = ()


@dataclass(frozen=True)
class BindingAssessment:
    case_id: str
    participant_score: float
    meaning_score: float
    verdict: Verdict


@dataclass(frozen=True)
class ClaimAssessment:
    case_id: str
    support_scores: dict[str, float]
    support_verdict: Verdict
    publication_score: float
    publication_verdict: PublicationVerdict
    bindings: tuple[BindingAssessment, ...]


@dataclass(frozen=True)
class CalibrationResult:
    assessments: tuple[ClaimAssessment, ...]
    calls: int
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    state_bytes: int


@dataclass(frozen=True)
class PublicationSource:
    kind: SourceKind
    issuer: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "NEWS",
            "OFFICIAL_COMPANY",
            "OFFICIAL_GOVERNMENT",
            "OTHER",
        }:
            raise ValueError("JEV_PUBLICATION_SOURCE")
        official = self.kind in {"OFFICIAL_COMPANY", "OFFICIAL_GOVERNMENT"}
        if (
            official != (self.issuer is not None)
            or self.issuer is not None
            and not self.issuer.strip()
        ):
            raise ValueError("JEV_PUBLICATION_SOURCE")


@dataclass(frozen=True)
class PublicationCase:
    case_id: str
    statement: str
    evidence: tuple[str, ...]
    source: PublicationSource


@dataclass(frozen=True)
class PublicationAssessment:
    case_id: str
    interpretation_score: float
    attribution_score: float
    official_self_score: float | None
    verdict: PublicationVerdict


@dataclass(frozen=True)
class PublicationResult:
    assessments: tuple[PublicationAssessment, ...]
    calls: int
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    state_bytes: int


_SUPPORT_QUESTIONS = {
    "core": (
        "Does the claim preserve a central subject-action-object proposition "
        "directly stated or plainly entailed by the evidence?",
        "The main actor, action, and object agree with the evidence.",
        "A central actor, action, or object is added, removed, or changed.",
    ),
    "attribution": (
        "Does the claim preserve who said, announced, denied, reported, or owns "
        "the assertion in the evidence?",
        "Attribution agrees, or neither the claim nor evidence makes an attribution.",
        "The speaker, source, owner, or reported nature of the assertion changes.",
    ),
    "status": (
        "Does the claim preserve negation, denial, plan-versus-completion status, "
        "and modality from the evidence?",
        "All stated status and modality distinctions agree with the evidence.",
        "A denial becomes an action, a plan becomes completed, or certainty changes.",
    ),
    "details": (
        "Is every material quantity, date, place, and condition in the claim "
        "supported by the evidence?",
        "Each such detail is supported, or the claim contains none.",
        "Any quantity, date, place, or condition is invented or materially changed.",
    ),
}


def minimal_state(case: ClaimCase) -> dict[str, object]:
    """Return only text and rules needed for the requested judgments."""
    return {
        "claim": {"statement": case.statement, "modality": case.modality},
        "evidence": list(case.evidence),
        "bindings": [
            {
                "proposal": binding.proposal,
                "mentions": list(binding.mentions),
                "rule": binding.rule,
            }
            for binding in case.bindings
        ],
    }


def _question(question: str, true: str, false: str, *paths: str) -> dict[str, object]:
    return {
        "type": "noul",
        "instructions": {
            "question": question,
            "compare": list(paths),
            "focus": "Judge only this distinction from the supplied state.",
        },
        "criteria": {"true": true, "false": false},
    }


def _questions(claim_index: int, binding_count: int) -> dict[str, object]:
    root = f"claims[{claim_index}]"
    questions: dict[str, object] = {
        f"c{claim_index}_support_{name}": _question(
            question, true, false, f"{root}.claim", f"{root}.evidence"
        )
        for name, (question, true, false) in _SUPPORT_QUESTIONS.items()
    }
    questions[f"c{claim_index}_publication"] = _question(
        "Is the claim suitable for factual publication from this evidence?",
        "It reports an evidenced fact, action, statement, quantity, plan, or an "
        "interpretation explicitly attributed to a source.",
        "It adds an unattributed journalist strategy, intent, motive, or evaluation.",
        f"{root}.claim",
        f"{root}.evidence",
    )
    for binding_index in range(binding_count):
        binding = f"{root}.bindings[{binding_index}]"
        questions[f"c{claim_index}_binding_{binding_index}_participant"] = _question(
            "Are the binding participants, their roles, and direction supported by "
            "the claim and evidence?",
            "The participating mentions, source and target roles, and direction agree.",
            "A participant, role, or direction is invented or reversed.",
            f"{root}.claim",
            f"{root}.evidence",
            binding,
        )
        questions[f"c{claim_index}_binding_{binding_index}_meaning"] = _question(
            "Does the binding predicate, stance, value, or time express a supported "
            "part of the claim under its rule?",
            "The binding expresses a supported part and conforms to the supplied "
            "rule; it need not encode the whole claim.",
            "Its predicate, stance, value, time, or rule meaning is unsupported or "
            "changed.",
            f"{root}.claim",
            f"{root}.evidence",
            binding,
        )
    return questions


def _verdict(scores: list[float], positive: float, negative: float) -> Verdict:
    if all(score >= positive for score in scores):
        return "TRUE"
    if any(score <= negative for score in scores):
        return "FALSE"
    return "UNRESOLVED"


def _publication(score: float, positive: float, negative: float) -> PublicationVerdict:
    if score >= positive:
        return "PUBLISH"
    if score <= negative:
        return "EDITORIAL_INTERPRETATION"
    return "UNRESOLVED"


def _groups(cases: tuple[ClaimCase, ...], packed: bool) -> list[tuple[ClaimCase, ...]]:
    if not packed:
        return [(case,) for case in cases]
    groups: list[tuple[ClaimCase, ...]] = []
    current: list[ClaimCase] = []
    count = 0
    for case in cases:
        needed = 5 + 2 * len(case.bindings)
        if needed > BATCH_SIZE:
            raise JevSelectionError("JEV_INPUT_LIMIT")
        if current and count + needed > BATCH_SIZE:
            groups.append(tuple(current))
            current, count = [], 0
        current.append(case)
        count += needed
    if current:
        groups.append(tuple(current))
    return groups


def evaluate_cases(
    cases: tuple[ClaimCase, ...],
    *,
    api_key: SecretStr,
    mode: Literal["per_claim", "packed"] = "per_claim",
    positive_threshold: float = 0.70,
    negative_threshold: float = 0.30,
    transport: httpx.BaseTransport | None = None,
) -> CalibrationResult:
    if not cases or not 0 <= negative_threshold < positive_threshold <= 1:
        raise ValueError("JEV_CALIBRATION_INPUT")
    started = time.monotonic()
    calls = input_tokens = output_tokens = state_bytes = 0
    assessments: list[ClaimAssessment] = []
    for group in _groups(cases, mode == "packed"):
        state = {"claims": [minimal_state(case) for case in group]}
        questions: dict[str, object] = {}
        for index, case in enumerate(group):
            questions.update(_questions(index, len(case.bindings)))
        response = evaluate_nouls(
            state, questions, api_key=api_key, transport=transport
        )
        calls += 1
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        state_bytes += len(json.dumps(state, ensure_ascii=False).encode())
        for index, case in enumerate(group):
            support = {
                name: response.answers[f"c{index}_support_{name}"].noul
                for name in _SUPPORT_QUESTIONS
            }
            bindings = tuple(
                BindingAssessment(
                    binding.case_id,
                    response.answers[f"c{index}_binding_{i}_participant"].noul,
                    response.answers[f"c{index}_binding_{i}_meaning"].noul,
                    _verdict(
                        [
                            response.answers[f"c{index}_binding_{i}_participant"].noul,
                            response.answers[f"c{index}_binding_{i}_meaning"].noul,
                        ],
                        positive_threshold,
                        negative_threshold,
                    ),
                )
                for i, binding in enumerate(case.bindings)
            )
            publication_score = response.answers[f"c{index}_publication"].noul
            assessments.append(
                ClaimAssessment(
                    case.case_id,
                    support,
                    _verdict(
                        list(support.values()), positive_threshold, negative_threshold
                    ),
                    publication_score,
                    _publication(
                        publication_score, positive_threshold, negative_threshold
                    ),
                    bindings,
                )
            )
    return CalibrationResult(
        tuple(assessments),
        calls,
        input_tokens,
        output_tokens,
        time.monotonic() - started,
        state_bytes,
    )


def publication_state(case: PublicationCase) -> dict[str, object]:
    source: dict[str, str] = {"kind": case.source.kind}
    if case.source.issuer is not None:
        source["verified_issuer"] = case.source.issuer
    return {
        "claim": case.statement,
        "evidence": list(case.evidence),
        "source": source,
    }


def _publication_questions(claim_index: int, *, official: bool) -> dict[str, object]:
    root = f"claims[{claim_index}]"
    questions: dict[str, object] = {
        f"c{claim_index}_interpretation": _question(
            "Does the claim itself assert a strategy, intention, purpose, motive, "
            "evaluation, or prediction beyond reporting an observable action, "
            "number, plan, or statement?",
            "The claim explains why an actor acts or evaluates what its actions mean.",
            "The claim only reports an observable fact, action, number, plan, or "
            "what somebody said without adding that explanation.",
            f"{root}.claim",
        ),
        f"c{claim_index}_attribution": _question(
            "Does the evidence explicitly identify a person or organization as the "
            "speaker or owner of the strategy, intention, purpose, motive, "
            "evaluation, or prediction stated by the claim?",
            "The evidence explicitly says that a named person or organization said, "
            "announced, described, explained, or officially stated that "
            "interpretation.",
            "The article narrator states or implies the interpretation without "
            "assigning it to a named speaker or organization.",
            f"{root}.claim",
            f"{root}.evidence",
        ),
    }
    if official:
        questions[f"c{claim_index}_official_self"] = _question(
            "Is the person or organization whose strategy, intention, purpose, "
            "motive, evaluation, or prediction is asserted the same real-world "
            "entity as the verified official source issuer?",
            "The interpretation concerns the verified issuer itself.",
            "It concerns another entity, or the identity is not established.",
            f"{root}.claim",
            f"{root}.source.verified_issuer",
        )
    return questions


def publication_verdict(
    interpretation: float,
    attribution: float,
    official_self: float | None,
    *,
    positive_threshold: float = 0.70,
    negative_threshold: float = 0.30,
) -> PublicationVerdict:
    if not 0 <= negative_threshold < positive_threshold <= 1:
        raise ValueError("JEV_CALIBRATION_INPUT")
    if interpretation <= negative_threshold:
        return "PUBLISH"
    if interpretation < positive_threshold:
        return "UNRESOLVED"
    if attribution >= positive_threshold or (
        official_self is not None and official_self >= positive_threshold
    ):
        return "PUBLISH"
    if attribution <= negative_threshold and (
        official_self is None or official_self <= negative_threshold
    ):
        return "EDITORIAL_INTERPRETATION"
    return "UNRESOLVED"


def _publication_groups(
    cases: tuple[PublicationCase, ...], packed: bool
) -> list[tuple[PublicationCase, ...]]:
    if not packed:
        return [(case,) for case in cases]
    groups: list[tuple[PublicationCase, ...]] = []
    current: list[PublicationCase] = []
    count = 0
    for case in cases:
        needed = 3 if case.source.issuer is not None else 2
        if current and count + needed > BATCH_SIZE:
            groups.append(tuple(current))
            current, count = [], 0
        current.append(case)
        count += needed
    if current:
        groups.append(tuple(current))
    return groups


def evaluate_publication_cases(
    cases: tuple[PublicationCase, ...],
    *,
    api_key: SecretStr,
    mode: Literal["per_claim", "packed"] = "per_claim",
    positive_threshold: float = 0.70,
    negative_threshold: float = 0.30,
    transport: httpx.BaseTransport | None = None,
) -> PublicationResult:
    if not cases or not 0 <= negative_threshold < positive_threshold <= 1:
        raise ValueError("JEV_CALIBRATION_INPUT")
    started = time.monotonic()
    calls = input_tokens = output_tokens = state_bytes = 0
    assessments: list[PublicationAssessment] = []
    for group in _publication_groups(cases, mode == "packed"):
        state = {"claims": [publication_state(case) for case in group]}
        questions: dict[str, object] = {}
        for index, case in enumerate(group):
            questions.update(
                _publication_questions(index, official=case.source.issuer is not None)
            )
        response = evaluate_nouls(
            state, questions, api_key=api_key, transport=transport
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
            assessments.append(
                PublicationAssessment(
                    case.case_id,
                    interpretation,
                    attribution,
                    official_self,
                    publication_verdict(
                        interpretation,
                        attribution,
                        official_self,
                        positive_threshold=positive_threshold,
                        negative_threshold=negative_threshold,
                    ),
                )
            )
    return PublicationResult(
        tuple(assessments),
        calls,
        input_tokens,
        output_tokens,
        time.monotonic() - started,
        state_bytes,
    )
