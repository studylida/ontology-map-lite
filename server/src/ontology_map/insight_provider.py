"""Production Structured Output adapter for #68 NODE_INSIGHT.

The #68 product module owns model/prompt/schema/parser semantics. Shared code is
limited to the one-send Model Studio transport; no FOLLOWUP DTO/parser is reused.
"""

from collections.abc import Callable
from datetime import datetime

import httpx
import sqlalchemy as sa
from pydantic import SecretStr, ValidationError

from ontology_map import insight_execution
from ontology_map import insight_generation as product
from ontology_map.insight_generation_contracts import (
    InsightBundleProposal,
    PreparedInsightBundle,
)
from ontology_map.insight_runner import RunnerResult, run_insight
from ontology_map.model_studio import CallFailed
from ontology_map.structured_provider import (
    DEFAULT_TIMEOUT_SECONDS,
    ModelStudioStructuredTransport,
)


class ModelStudioInsightAdapter:
    """Prepare one approved #68 bundle request before durable slot reservation."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._transport = ModelStudioStructuredTransport(
            api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    @property
    def endpoint(self) -> str:
        return self._transport.endpoint

    def close(self) -> None:
        self._transport.close()

    def prepare(
        self, prepared: PreparedInsightBundle
    ) -> Callable[[], InsightBundleProposal]:
        """Use #68 model/messages/schema/parser as the only product contract."""
        messages: list[dict[str, str]] = []
        for role, content in product.build_messages(prepared):
            if role == "system":
                mapped = "system"
            elif role == "human":
                mapped = "user"
            else:
                raise CallFailed("INVALID_REQUEST", fatal=True)
            messages.append({"role": mapped, "content": content})
        raw_send = self._transport.prepare(
            model=product.MODEL_VERSION,
            messages=messages,
            schema_name=InsightBundleProposal.__name__,
            schema=product.output_schema(),
            limits=insight_execution.INSIGHT_LIMITS,
        )

        def send() -> InsightBundleProposal:
            try:
                return raw_send.parse(product.parse_proposal)
            except ValidationError:
                raise CallFailed("OUTPUT_CONTRACT_ERROR", fatal=False) from None

        return send


def run_model_studio_insight(
    engine: sa.Engine,
    task_id: int,
    worker_name: str,
    *,
    promotion_batch_id: int,
    node_id: int,
    as_of_at: datetime,
    adapter: ModelStudioInsightAdapter,
) -> RunnerResult:
    """Compose the durable #68 runner with one production Model Studio send."""
    return run_insight(
        engine,
        task_id,
        worker_name,
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
        as_of_at=as_of_at,
        prepare_provider=adapter.prepare,
    )
