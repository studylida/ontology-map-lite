"""OpenAI-only application wiring (legacy module name).

Construction never sends requests or touches the database.

The existing application_execution.run_document remains the execution boundary.
No provider fallback, automatic retry, implicit budget or credential discovery.
"""

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from ontology_map.extraction_promotion import (
    ResolutionProposer,
    model_studio_resolution_proposer,
)
from ontology_map.extraction_provider import KimiGenerationAdapter
from ontology_map.followup_provider import (
    ModelStudioFollowupAdapter as KimiFollowupAdapter,
)
from ontology_map.insight_provider import (
    ModelStudioInsightAdapter as KimiInsightAdapter,
)
from ontology_map.llm_config import BASE_URL
from ontology_map.llm_pacing import ProcessPacer, pacing_scope
from ontology_map.model_studio import Budget, CallLimits, KimiModels
from ontology_map.node_context_provider import (
    ModelStudioNodeContextAdapter as KimiNodeContextAdapter,
)


@dataclass(frozen=True, repr=False)
class KimiClients:
    helpers: KimiModels
    generation: KimiGenerationAdapter
    node_context: KimiNodeContextAdapter
    followup: KimiFollowupAdapter
    insight: KimiInsightAdapter

    def resolution_proposer(self, limits: CallLimits) -> ResolutionProposer:
        return model_studio_resolution_proposer(self.helpers, limits)


@contextmanager
def kimi_clients(
    helper_budget: Budget,
    *,
    api_key: SecretStr | None = None,
    pacer: ProcessPacer | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[KimiClients]:
    """Use only an explicit key or OPENAI_API_KEY, never an old provider key.

    The caller must activate a separate explicit PilotBudget for paid calls;
    helper_budget alone never authorizes a live send. ExitStack also closes
    already-created clients if later construction fails.
    """
    key = (
        api_key
        if api_key is not None
        else SecretStr(os.environ.get("OPENAI_API_KEY", ""))
    )
    with ExitStack() as stack:
        stack.enter_context(pacing_scope(pacer))
        helpers = KimiModels(key, helper_budget, base_url=BASE_URL, transport=transport)
        stack.callback(helpers.close)
        generation = KimiGenerationAdapter(key, base_url=BASE_URL, transport=transport)
        stack.callback(generation.close)
        node_context = KimiNodeContextAdapter(
            key, base_url=BASE_URL, transport=transport
        )
        stack.callback(node_context.close)
        followup = KimiFollowupAdapter(key, base_url=BASE_URL, transport=transport)
        stack.callback(followup.close)
        insight = KimiInsightAdapter(key, base_url=BASE_URL, transport=transport)
        stack.callback(insight.close)
        yield KimiClients(helpers, generation, node_context, followup, insight)


OpenAIClients = KimiClients
openai_clients = kimi_clients
