"""Post-commit application handoff from #127 promotion to #215 publication.

The #127 finalizer owns the canonical promotion transaction and returns only
after that transaction has committed. This module is the narrow production
application boundary that consumes its ProductResult and, only for a real
committed batch, starts/resumes #215 initial publication in separate
transactions. It is intentionally not a queue, scheduler, outbox or event bus.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from sqlalchemy.engine import Engine

from ontology_map import extraction_promotion
from ontology_map.db import extraction_tasks
from ontology_map.extraction_runner import RunnerResult as ExtractionRunnerResult
from ontology_map.extraction_runner import RuntimeInput
from ontology_map.followup_runner import ProviderPreflight as FollowupProviderPreflight
from ontology_map.initial_publication_coordinator import (
    InitialPublicationRunResult,
    run_initial_publication,
)
from ontology_map.insight_runner import ProviderPreflight as InsightProviderPreflight
from ontology_map.node_context_runner import (
    ProviderPreflight as NodeContextProviderPreflight,
)


@dataclass(frozen=True)
class ExtractionPublicationResult:
    product: extraction_promotion.ProductResult
    publication: InitialPublicationRunResult | None


def handoff_committed_promotion(
    engine: Engine,
    product: extraction_promotion.ProductResult,
    worker_name: str,
    *,
    prepare_node_context_provider: NodeContextProviderPreflight,
    prepare_followup_provider: FollowupProviderPreflight,
    prepare_insight_provider: InsightProviderPreflight,
) -> InitialPublicationRunResult | None:
    """Start/resume publication only for an already committed real promotion."""
    if product.disposition != "SUCCESS" or product.promotion_batch_id is None:
        return None
    return run_initial_publication(
        engine,
        product.promotion_batch_id,
        worker_name,
        prepare_node_context_provider=prepare_node_context_provider,
        prepare_followup_provider=prepare_followup_provider,
        prepare_insight_provider=prepare_insight_provider,
    )


def finalize_extraction_with_initial_publication(
    engine: Engine,
    runner: ExtractionRunnerResult,
    execution: extraction_tasks.ExecutionInput,
    runtime: RuntimeInput,
    propose_resolution: extraction_promotion.ResolutionProposer,
    publication_worker_name: str,
    *,
    prepare_node_context_provider: NodeContextProviderPreflight,
    prepare_followup_provider: FollowupProviderPreflight,
    prepare_insight_provider: InsightProviderPreflight,
    promotion_write_lock: AbstractContextManager[object] | None = None,
    after_promotion: Callable[[extraction_promotion.ProductResult], None] | None = None,
) -> ExtractionPublicationResult:
    """Finalize #127, then hand its committed batch to #215 after commit.

    ``finalize_extraction`` returns only after its short promotion transaction has
    exited. Publication therefore cannot roll that transaction back. If this
    process exits between the two calls, the durable state remains
    ``COMMITTED + NOT_STARTED`` and ``run_initial_publication`` is the explicit
    restart entry for the same batch.
    """
    product = extraction_promotion.finalize_extraction(
        engine,
        runner,
        execution,
        runtime,
        propose_resolution,
        write_lock=promotion_write_lock,
    )
    if after_promotion is not None:
        after_promotion(product)
    publication = handoff_committed_promotion(
        engine,
        product,
        publication_worker_name,
        prepare_node_context_provider=prepare_node_context_provider,
        prepare_followup_provider=prepare_followup_provider,
        prepare_insight_provider=prepare_insight_provider,
    )
    return ExtractionPublicationResult(product=product, publication=publication)
