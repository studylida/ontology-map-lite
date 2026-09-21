"""Small application boundary for one extraction and its post-commit publication."""

import argparse
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from hashlib import sha256

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from ontology_map import extraction as harness
from ontology_map.db import extraction_tasks
from ontology_map.db.ontology_reference_data import (
    ATTRIBUTE_DEFINITIONS,
    RELATION_DEFINITIONS,
)
from ontology_map.db.runtime_bootstrap import output_schemas, require_runtime_ready
from ontology_map.db.topic_reference_schema import APPROVED_TOPIC_DEFINITIONS
from ontology_map.extraction_contracts import Ontology
from ontology_map.extraction_promotion import ProductResult, ResolutionProposer
from ontology_map.extraction_runner import (
    GenerationPreflight,
    RunnerResult,
    RuntimeInput,
    run_extraction,
)
from ontology_map.followup_runner import ProviderPreflight as FollowupPreflight
from ontology_map.initial_publication_coordinator import (
    InitialPublicationRunResult,
    run_initial_publication,
)
from ontology_map.initial_publication_handoff import (
    ExtractionPublicationResult,
    finalize_extraction_with_initial_publication,
)
from ontology_map.insight_runner import ProviderPreflight as InsightPreflight
from ontology_map.kimi_response_archive import response_task
from ontology_map.node_context_runner import ProviderPreflight as ContextPreflight
from ontology_map.pilot_budget import PilotBudget


def _require_ready(engine: Engine, ontology: Ontology | None = None) -> None:
    with engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            with Session(bind=connection) as session:
                require_runtime_ready(session, ontology)


def run_document(
    engine: Engine,
    document_id: int,
    worker_name: str,
    execution: extraction_tasks.ExecutionInput,
    runtime: RuntimeInput,
    helpers: harness.ExtractionModels,
    prepare_generation: GenerationPreflight,
    propose_resolution: ResolutionProposer,
    prepare_node_context_provider: ContextPreflight,
    prepare_followup_provider: FollowupPreflight,
    prepare_insight_provider: InsightPreflight,
    *,
    pilot: PilotBudget,
    finalization_lock: AbstractContextManager[object] | None = None,
    before_finalization: Callable[[], None] | None = None,
    after_promotion: Callable[[ProductResult], None] | None = None,
) -> RunnerResult | ExtractionPublicationResult:
    """One claim only; durable status/slot owners decide retries and terminal state."""
    with pilot.activate():
        _require_ready(engine, runtime.ontology)
        if runtime.document.document_id != str(document_id):
            raise ValueError("RUNNER_SOURCE_MISMATCH")
        if (
            execution.runtime_settings.get("extraction_runner")
            != runtime.identity_settings()
        ):
            raise ValueError("RUNNER_SETTINGS_MISMATCH")
        with Session(engine) as session, session.begin():
            task = extraction_tasks.enqueue_extraction(session, document_id, execution)
        runner = run_extraction(
            engine,
            task.task_id,
            worker_name,
            execution,
            runtime,
            helpers,
            prepare_generation,
        )
        pilot.require_active()
        if runner.disposition not in {
            "VERIFIED_RUNTIME",
            "ZERO_RESULT",
            "ALL_BLOCKED",
        }:
            return runner
        if before_finalization is not None:
            before_finalization()
        with response_task(task.task_id):
            result = finalize_extraction_with_initial_publication(
                engine,
                runner,
                execution,
                runtime,
                propose_resolution,
                worker_name,
                prepare_node_context_provider=prepare_node_context_provider,
                prepare_followup_provider=prepare_followup_provider,
                prepare_insight_provider=prepare_insight_provider,
                promotion_write_lock=finalization_lock,
                after_promotion=after_promotion,
            )
        pilot.require_active()
        return result


def resume_publication(
    engine: Engine,
    batch_id: int,
    worker_name: str,
    *,
    prepare_node_context_provider: ContextPreflight,
    prepare_followup_provider: FollowupPreflight,
    prepare_insight_provider: InsightPreflight,
    pilot: PilotBudget,
) -> InitialPublicationRunResult:
    """Resume an existing committed batch without repeating extraction."""
    with pilot.activate():
        _require_ready(engine)
        result = run_initial_publication(
            engine,
            batch_id,
            worker_name,
            prepare_node_context_provider=prepare_node_context_provider,
            prepare_followup_provider=prepare_followup_provider,
            prepare_insight_provider=prepare_insight_provider,
        )
        pilot.require_active()
        return result


def dry_run() -> dict[str, object]:
    """Offline plan: no engine, transaction, queue, or provider is constructed."""
    hashes = {
        kind: sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for kind, value in output_schemas().items()
    }
    return {
        "mode": "DRY_RUN",
        "database_reads": 0,
        "database_writes": 0,
        "provider_sends": 0,
        "active_database_checked": False,
        "output_schema_sha256": hashes,
        "approved_ontology_counts": {
            "relations": len(RELATION_DEFINITIONS),
            "attributes": len(ATTRIBUTE_DEFINITIONS),
            "topics": len(APPROVED_TOPIC_DEFINITIONS),
        },
        "blocking_gap": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.parse_args(argv)
    print(json.dumps(dry_run(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
