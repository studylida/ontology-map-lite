"""Small product DTO fixtures; no real corpus or quality claims."""

from ontology_map.entity_resolution_contracts import ResolutionBatchProposal
from ontology_map.extraction_contracts import (
    ClaimReviewBatch,
    KnowledgeProposals,
)
from ontology_map.followup_generation_contracts import FollowupQuestionsProposal
from ontology_map.insight_generation_contracts import InsightBundleProposal
from ontology_map.node_context_generation_contracts import NodeContextProposal

CASES = {
    "generation": (KnowledgeProposals, {"claims": []}),
    "claim_review": (ClaimReviewBatch, {"claims": []}),
    "entity_resolution": (
        ResolutionBatchProposal,
        {"resolutions": []},
    ),
    "node_context": (NodeContextProposal, {"context_text": "합성 맥락"}),
    "followup": (
        FollowupQuestionsProposal,
        {
            "recent_90_days": {"questions": []},
            "recent_1_year": {"questions": []},
        },
    ),
    "insight": (
        InsightBundleProposal,
        {
            "recent_90_days": {"report": None},
            "recent_1_year": {"report": None},
        },
    ),
}
