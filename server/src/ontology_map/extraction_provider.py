"""OpenAI generation adapter for the existing durable KNOWLEDGE_EXTRACTION runner.

The transport prepares exactly one strict-schema send. The original product schema
and Mention/temporal validators still run before the durable call can succeed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr, ValidationError

from ontology_map.extraction_contracts import KnowledgeProposals
from ontology_map.kimi_transport import KimiStructuredTransport
from ontology_map.llm_config import (
    BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    GENERATION_READ_TIMEOUT_SECONDS,
    MODEL_VERSION,
)
from ontology_map.model_studio import CallFailed

if TYPE_CHECKING:
    from ontology_map import extraction as harness
    from ontology_map.db import extraction_tasks as inputs
    from ontology_map.extraction_runner import (
        GenerationRequest,
        RunnerResult,
        RuntimeInput,
    )

CORRECTIVE_INPUT_SEPARATOR = "\n\n명시적 corrective input:\n"


class KimiGenerationAdapter:
    """Prepare one OpenAI request; never silently repair or accept malformed claims."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = GENERATION_READ_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._transport = KimiStructuredTransport(
            api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            transport=transport,
        )

    @property
    def endpoint(self) -> str:
        return self._transport.endpoint

    def close(self) -> None:
        self._transport.close()

    def prepare(self, request: GenerationRequest) -> Callable[[], KnowledgeProposals]:
        if (
            request.model != MODEL_VERSION
            or request.output_schema is not KnowledgeProposals
        ):
            raise CallFailed("INVALID_REQUEST", fatal=True)
        if not request.prompt.strip():
            raise CallFailed("INVALID_REQUEST", fatal=True)
        prompt = request.prompt
        if request.execution.corrective_input is not None:
            prompt += CORRECTIVE_INPUT_SEPARATOR + request.execution.corrective_input
        raw_send = self._transport.prepare(
            model=request.model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": request.payload.model_dump_json()},
            ],
            schema_name=KnowledgeProposals.__name__,
            schema=KnowledgeProposals.model_json_schema(),
            limits=request.limits,
        )

        def send() -> KnowledgeProposals:
            try:
                return raw_send.parse(
                    lambda content: KnowledgeProposals.model_validate_json(
                        content, strict=True
                    )
                )
            except ValidationError:
                raise CallFailed("OUTPUT_CONTRACT_ERROR", fatal=False) from None

        return send


def run_kimi_extraction(
    engine: Any,
    task_id: int,
    worker_name: str,
    execution: inputs.ExecutionInput,
    runtime: RuntimeInput,
    helpers: harness.ExtractionModels,
    adapter: KimiGenerationAdapter,
) -> RunnerResult:
    from ontology_map.extraction_runner import run_extraction

    return run_extraction(
        engine, task_id, worker_name, execution, runtime, helpers, adapter.prepare
    )


# Import compatibility only; legacy names execute OpenAI, never Qwen/Kimi.
ModelStudioGenerationAdapter = KimiGenerationAdapter
run_model_studio_extraction = run_kimi_extraction

OpenAIGenerationAdapter = KimiGenerationAdapter
run_openai_extraction = run_kimi_extraction
