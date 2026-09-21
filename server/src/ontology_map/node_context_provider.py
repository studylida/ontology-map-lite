"""Production Structured Output adapter for #215 NODE_CONTEXT."""

from collections.abc import Callable

import httpx
import sqlalchemy as sa
from pydantic import SecretStr, ValidationError

from ontology_map import node_context_execution
from ontology_map import node_context_generation as product
from ontology_map.model_studio import CallFailed
from ontology_map.node_context_generation_contracts import (
    NodeContextProposal,
    PreparedNodeContext,
)
from ontology_map.node_context_runner import RunnerResult, run_node_context
from ontology_map.structured_provider import (
    DEFAULT_TIMEOUT_SECONDS,
    ModelStudioStructuredTransport,
)


class ModelStudioNodeContextAdapter:
    """Prepare one approved NODE_CONTEXT request before durable slot reservation."""

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
        self, prepared: PreparedNodeContext
    ) -> Callable[[], NodeContextProposal]:
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
            schema_name=NodeContextProposal.__name__,
            schema=product.output_schema(),
            limits=node_context_execution.NODE_CONTEXT_LIMITS,
        )

        def send() -> NodeContextProposal:
            try:
                return raw_send.parse(product.parse_proposal)
            except ValidationError:
                raise CallFailed("OUTPUT_CONTRACT_ERROR", fatal=False) from None

        return send


def run_model_studio_node_context(
    engine: sa.Engine,
    task_id: int,
    worker_name: str,
    *,
    promotion_batch_id: int,
    node_id: int,
    adapter: ModelStudioNodeContextAdapter,
) -> RunnerResult:
    """Compose the shared durable lifecycle with one production context send."""
    return run_node_context(
        engine,
        task_id,
        worker_name,
        promotion_batch_id=promotion_batch_id,
        node_id=node_id,
        prepare_provider=adapter.prepare,
    )
