from types import SimpleNamespace

from pydantic import SecretStr

from ontology_map import extraction
from ontology_map.entity_resolution_contracts import (
    NodeCandidate,
    ResolutionBatchDecision,
    ResolutionBatchInput,
    ResolutionBatchItem,
    ResolutionBatchProposal,
    ResolutionInput,
    SourceContext,
)
from ontology_map.extraction_contracts import (
    ClaimProposal,
    ClaimReviewBatch,
    Mention,
    Ontology,
    RelationProposal,
    RelationRule,
    SourceSpan,
)
from ontology_map.jev_adapters import (
    JevClaimReviewModels,
    JevExistingCandidateProposer,
    JevJudgmentPolicy,
)
from ontology_map.jev_judgment import (
    BindingAssessment,
    PublicationAssessment,
    PublicationSource,
)
from ontology_map.jev_product import (
    ClaimSupportAssessment,
    ExistingCandidateAssessment,
    JudgmentResult,
    PublicationBindingAssessment,
)
from ontology_map.model_studio import CallLimits


def _judgment_result(*assessments: object) -> JudgmentResult:
    return JudgmentResult(tuple(assessments), 1, 10, 1, 0.1, 100)


def test_unqualified_jev_roles_are_disabled_by_default() -> None:
    policy = JevJudgmentPolicy(PublicationSource("OTHER"))
    assert policy.claim_support is False
    assert policy.existing_candidate_identity is False


def _review_input() -> extraction.ClaimReviewInput:
    span = SourceSpan(
        source_id="s1",
        start=0,
        end=12,
        quote="한빛전자는 새 기술을 개발한다.",
        quote_hash="0" * 64,
        paragraph_id="p1",
    )
    claim = ClaimProposal(
        candidate_id="c1",
        statement=span.quote,
        modality="FACT",
        source_ids=["s1"],
        mentions=[
            Mention(
                mention_id="m1",
                text="한빛전자",
                node_type="COMPANY",
                source_ids=["s1"],
                topic_name=None,
            ),
            Mention(
                mention_id="m2",
                text="새 기술",
                node_type="TECHNOLOGY",
                source_ids=["s1"],
                topic_name=None,
            ),
        ],
        bindings=[
            RelationProposal(
                kind="RELATION",
                binding_id="b1",
                code="DEVELOPS",
                source_mention="m1",
                target_mention="m2",
                stance="SUPPORT",
            )
        ],
    )
    return extraction.ClaimReviewInput(
        candidates=[
            extraction.ReviewCandidate(
                claim=claim,
                evidence=[span],
                retained_bindings=claim.bindings,
            )
        ],
        ontology=Ontology(
            node_types=("COMPANY", "TECHNOLOGY"),
            relations=(
                RelationRule(
                    code="DEVELOPS",
                    version_no=1,
                    revision_id=1,
                    description="개발",
                    direction="DIRECTED",
                    endpoints=(("COMPANY", "TECHNOLOGY"),),
                ),
            ),
            attributes=(),
            topics=(),
        ),
    )


def test_claim_review_assembles_jev_product_verdicts(monkeypatch) -> None:
    payload = _review_input()

    monkeypatch.setattr(
        "ontology_map.jev_adapters.evaluate_publication_bindings",
        lambda *_args, **_kwargs: _judgment_result(
            PublicationBindingAssessment(
                "c1",
                PublicationAssessment("c1", 0.1, 0.1, None, "PUBLISH"),
                (BindingAssessment("b1", 0.9, 0.9, "TRUE"),),
            )
        ),
    )
    monkeypatch.setattr(
        "ontology_map.jev_adapters.evaluate_claim_support",
        lambda *_args, **_kwargs: _judgment_result(
            ClaimSupportAssessment("c1", {"core_change": 0.1}, "TRUE")
        ),
    )
    delegate = SimpleNamespace(
        call=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected OpenAI review")
        )
    )
    models = JevClaimReviewModels(
        delegate,
        SecretStr("private"),
        JevJudgmentPolicy(PublicationSource("OTHER"), claim_support=True),
    )
    result = models.call(
        "claim_review",
        "prompt",
        payload,
        ClaimReviewBatch,
        CallLimits(100, 100, 1000),
    )
    assert result.claims[0].support_verdict == "TRUE"
    assert result.claims[0].publication_verdict == "PUBLISH"
    assert result.claims[0].bindings[0].verdict == "TRUE"


def test_default_claim_review_asks_terra_only_for_support(monkeypatch) -> None:
    monkeypatch.setattr(
        "ontology_map.jev_adapters.evaluate_publication_bindings",
        lambda *_args, **_kwargs: _judgment_result(
            PublicationBindingAssessment(
                "c1",
                PublicationAssessment("c1", 0.1, 0.1, None, "PUBLISH"),
                (BindingAssessment("b1", 0.9, 0.9, "TRUE"),),
            )
        ),
    )
    calls = []

    class Delegate:
        def call(self, role, prompt, payload, schema, limits):
            calls.append((role, prompt, payload.model_dump(), schema, limits))
            return schema.model_validate(
                {"claims": [{"candidate_id": "c1", "support_verdict": "TRUE"}]}
            )

    result = JevClaimReviewModels(
        Delegate(),
        SecretStr("private"),
        JevJudgmentPolicy(PublicationSource("OTHER")),
    ).call(
        "claim_review",
        "combined prompt",
        _review_input(),
        ClaimReviewBatch,
        CallLimits(100, 3_000, 1000),
    )

    assert result.claims[0].support_verdict == "TRUE"
    assert calls[0][2] == {
        "candidates": [
            {
                "candidate_id": "c1",
                "statement": "한빛전자는 새 기술을 개발한다.",
                "modality": "FACT",
                "evidence": ["한빛전자는 새 기술을 개발한다."],
            }
        ]
    }
    assert calls[0][4].max_output_tokens == 2_048


def _resolution_input() -> ResolutionBatchInput:
    source = SourceContext(
        source_document_id=1,
        start_char=0,
        end_char=4,
        quote_text="한빛전자가 발표했다.",
    )
    return ResolutionBatchInput(
        mentions=(
            ResolutionBatchItem(
                mention_id="m1",
                input=ResolutionInput(
                    mention_text="한빛전자",
                    node_type="COMPANY",
                    context=(source,),
                    candidates=(
                        NodeCandidate(
                            node_id=7,
                            node_type="COMPANY",
                            preferred_alias="한빛전자",
                            aliases=("Hanbit",),
                            external_identifiers=(),
                        ),
                    ),
                    candidates_truncated=False,
                ),
            ),
        )
    )


def test_existing_candidate_unique_same_skips_terra(monkeypatch) -> None:
    monkeypatch.setattr(
        "ontology_map.jev_adapters.evaluate_existing_candidate_identity",
        lambda *_args, **_kwargs: _judgment_result(
            ExistingCandidateAssessment("m0c0", 0.95, "SAME")
        ),
    )
    proposer = JevExistingCandidateProposer(
        lambda _payload: (_ for _ in ()).throw(AssertionError("unexpected Terra")),
        SecretStr("private"),
        JevJudgmentPolicy(PublicationSource("OTHER"), existing_candidate_identity=True),
    )
    result = proposer(_resolution_input())
    assert result.resolutions == (
        ResolutionBatchDecision(mention_id="m1", decision="SAME", node_id=7),
    )


def test_existing_candidate_nonmatch_delegates_new_specificity(monkeypatch) -> None:
    monkeypatch.setattr(
        "ontology_map.jev_adapters.evaluate_existing_candidate_identity",
        lambda *_args, **_kwargs: _judgment_result(
            ExistingCandidateAssessment("m0c0", 0.05, "DIFFERENT")
        ),
    )
    proposer = JevExistingCandidateProposer(
        lambda payload: ResolutionBatchProposal(
            resolutions=tuple(
                ResolutionBatchDecision(
                    mention_id=item.mention_id,
                    decision="NEW",
                    node_id=None,
                )
                for item in payload.mentions
            )
        ),
        SecretStr("private"),
        JevJudgmentPolicy(PublicationSource("OTHER"), existing_candidate_identity=True),
    )
    assert proposer(_resolution_input()).resolutions[0].decision == "NEW"
