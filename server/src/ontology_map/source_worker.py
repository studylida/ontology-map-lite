"""In-process execution of uploaded Korean source documents."""

from __future__ import annotations

import os
import re
import stat
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast

import sqlalchemy as sa
from pydantic import JsonValue, SecretStr
from sqlalchemy import Connection, Engine
from sqlalchemy.orm import Session

from ontology_map import application_execution, extraction
from ontology_map.db import extraction_promotion as references
from ontology_map.db import (
    extraction_tasks,
    followup_tasks,
    initial_publication,
    insight_tasks,
    product_lint,
    schema,
    source_processing,
)
from ontology_map.db.ontology_reference_data import (
    ATTRIBUTE_DEFINITIONS,
    NODE_TYPE_DEFINITIONS,
    RELATION_DEFINITIONS,
)
from ontology_map.db.topic_reference_schema import APPROVED_TOPIC_DEFINITIONS
from ontology_map.extraction_contracts import (
    AttributeRule,
    NodeType,
    Ontology,
    RelationRule,
    SourceDocument,
)
from ontology_map.extraction_runner import RuntimeInput
from ontology_map.initial_publication_handoff import ExtractionPublicationResult
from ontology_map.jev_adapters import (
    JevClaimReviewModels,
    JevExistingCandidateProposer,
    JevJudgmentPolicy,
)
from ontology_map.jev_judgment import PublicationSource
from ontology_map.jev_selection import select_body_sources
from ontology_map.kimi_clients import OpenAIClients, openai_clients
from ontology_map.llm_config import MAX_INPUT_TOKENS
from ontology_map.llm_pacing import ProcessPacer, pacing_scope
from ontology_map.model_studio import Budget, CallLimits
from ontology_map.pilot_budget import PilotBudget
from ontology_map.settings import Settings
from ontology_map.source_intake import split_source_spans

_BODY_LIMITS = CallLimits(MAX_INPUT_TOKENS, 512, 8 * 1024 * 1024)
_GENERATION_LIMITS = CallLimits(MAX_INPUT_TOKENS, 8_192, 8 * 1024 * 1024)
_JUDGMENT_LIMITS = CallLimits(MAX_INPUT_TOKENS, 8_192, 8 * 1024 * 1024)
_EXTRACTION_LIMITS = extraction.ExtractionLimits(
    body=_BODY_LIMITS,
    generation=_GENERATION_LIMITS,
    judgment=_JUDGMENT_LIMITS,
    max_candidates=8,
)
_WORKER_LOCK_ID = 721_540_254
_active_worker: SourceWorker | None = None
_active_worker_lock = threading.Lock()


@dataclass(frozen=True)
class _SourceSnapshot:
    document: SourceDocument
    title: str
    publisher: str
    language: str


def _ontology_snapshot(session: Session) -> Ontology:
    relations: list[RelationRule] = []
    for relation_definition in RELATION_DEFINITIONS:
        active_relation = references.relation_rule(session, relation_definition.code)
        description = session.scalar(
            sa.text(
                "SELECT display_name FROM relation_type_revision "
                "WHERE relation_type_revision_id=:id"
            ),
            {"id": active_relation.revision_id},
        )
        relations.append(
            RelationRule(
                code=active_relation.code,
                version_no=active_relation.version_no,
                revision_id=active_relation.revision_id,
                description=str(description),
                direction=cast(
                    Literal["DIRECTED", "SYMMETRIC"], active_relation.direction
                ),
                endpoints=cast(
                    tuple[tuple[NodeType, NodeType], ...], active_relation.endpoints
                ),
            )
        )
    attributes: list[AttributeRule] = []
    for attribute_definition in ATTRIBUTE_DEFINITIONS:
        active_attribute = references.attribute_rule(session, attribute_definition.code)
        description = session.scalar(
            sa.text(
                "SELECT display_name FROM attribute_revision "
                "WHERE attribute_revision_id=:id"
            ),
            {"id": active_attribute.revision_id},
        )
        attributes.append(
            AttributeRule(
                code=active_attribute.code,
                version_no=active_attribute.version_no,
                revision_id=active_attribute.revision_id,
                description=str(description),
                node_type=cast(NodeType, active_attribute.target_node_type),
                value_kind=cast(
                    Literal["STRING", "NUMBER", "BOOLEAN", "DATE", "PERIOD"],
                    active_attribute.value_kind,
                ),
                units=active_attribute.units,
            )
        )
    return Ontology(
        node_types=cast(
            tuple[NodeType, ...],
            tuple(code for code, _ in NODE_TYPE_DEFINITIONS),
        ),
        relations=tuple(relations),
        attributes=tuple(attributes),
        topics=tuple(name for _, name in APPROVED_TOPIC_DEFINITIONS),
    )


def _source_snapshot(session: Session, document_id: int) -> _SourceSnapshot:
    row = (
        session.execute(
            sa.select(
                schema.source_document.c.normalized_body,
                schema.source_document.c.body_hash,
                schema.source_document.c.title,
                schema.source_document.c.publisher_name,
                schema.source_document.c.original_language,
            ).where(schema.source_document.c.source_document_id == document_id)
        )
        .mappings()
        .one()
    )
    body = str(row["normalized_body"])
    body_hash = sha256(body.encode()).digest()
    if body_hash != bytes(row["body_hash"]):
        raise ValueError("SOURCE_HASH_MISMATCH")
    spans = split_source_spans(body)
    return _SourceSnapshot(
        SourceDocument(
            document_id=str(document_id),
            body=body,
            body_hash=body_hash.hex(),
            sources=spans,
        ),
        str(row["title"]),
        str(row["publisher_name"]),
        str(row["original_language"]),
    )


def _safe_error(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Z0-9_]+", code):
        return code
    return "SOURCE_PROCESSING_FAILED"


class SourceWorker:
    """A demo worker owned by one FastAPI process."""

    def __init__(self, engine: Engine, settings: Settings) -> None:
        if not settings.source_processing_enabled:
            raise ValueError("SOURCE_PROCESSING_DISABLED")
        ledger_dir = settings.provider_ledger_dir
        if ledger_dir is None:
            raise ValueError("SOURCE_PROCESSING_LEDGER_DIR_REQUIRED")
        self._engine = engine
        self._ledger_dir = ledger_dir
        self._executor = ThreadPoolExecutor(
            max_workers=settings.source_processing_workers,
            thread_name_prefix="source-processing",
        )
        self._pacer = ProcessPacer(
            settings.provider_min_interval_seconds,
            max_concurrency=settings.source_processing_workers,
        )
        self._finalization_lock = threading.Lock()
        self._stopping = threading.Event()
        self._futures: set[Future[None]] = set()
        self._futures_lock = threading.Lock()
        self._ownership: Connection | None = None

    def start(self) -> None:
        info = self._ledger_dir.stat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("SOURCE_PROCESSING_LEDGER_DIR_PERMISSIONS")
        ownership = self._engine.connect()
        if not ownership.scalar(
            sa.text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": _WORKER_LOCK_ID},
        ):
            ownership.close()
            raise RuntimeError("SOURCE_WORKER_ALREADY_ACTIVE")
        try:
            with self._engine.begin() as connection:
                job_ids = connection.scalars(
                    sa.select(
                        schema.source_processing_job.c.source_processing_job_id
                    ).where(
                        schema.source_processing_job.c.status.in_(("QUEUED", "RUNNING"))
                    )
                ).all()
                for job_id in job_ids:
                    source_processing.finish_processing_job(
                        connection,
                        job_id=int(job_id),
                        status="INTERRUPTED",
                        stage="INTERRUPTED",
                        error_code="PROCESS_RESTARTED",
                    )
            global _active_worker
            with _active_worker_lock:
                if _active_worker is not None:
                    raise RuntimeError("SOURCE_WORKER_ALREADY_ACTIVE")
                _active_worker = self
            self._ownership = ownership
        except Exception:
            ownership.close()
            raise

    def submit(self, job_id: int) -> None:
        if self._stopping.is_set():
            raise RuntimeError("SOURCE_WORKER_STOPPING")
        future = self._executor.submit(self._process, job_id)
        with self._futures_lock:
            self._futures.add(future)
        future.add_done_callback(self._discard_future)

    def _discard_future(self, future: Future[None]) -> None:
        with self._futures_lock:
            self._futures.discard(future)

    def shutdown(self) -> None:
        global _active_worker
        with _active_worker_lock:
            if _active_worker is self:
                _active_worker = None
        self._stopping.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        if self._ownership is not None:
            with suppress(Exception):
                self._ownership.execute(
                    sa.text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": _WORKER_LOCK_ID},
                )
            self._ownership.close()
            self._ownership = None

    def _finish(
        self,
        job_id: int,
        status: source_processing.TerminalProcessingStatus,
        stage: str,
        error_code: str | None = None,
    ) -> None:
        with self._engine.begin() as connection:
            source_processing.finish_processing_job(
                connection,
                job_id=job_id,
                status=status,
                stage=stage,
                error_code=error_code,
            )

    def _process(self, job_id: int) -> None:
        pilot: PilotBudget | None = None
        try:
            with self._engine.begin() as connection:
                job = source_processing.start_processing_job(
                    connection, job_id=job_id, stage="BODY_SELECTION"
                )
            if job is None:
                return
            pilot = PilotBudget(
                f"source-job-{job_id}",
                None,
                None,
                self._ledger_dir / f"source-job-{job_id}.jsonl",
                demo_uncapped=True,
            )
            with (
                self._engine.connect().execution_options(
                    isolation_level="REPEATABLE READ"
                ) as connection,
                connection.begin(),
            ):
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                with Session(bind=connection) as session:
                    source = _source_snapshot(session, job.source_document_id)
                    ontology = _ontology_snapshot(session)
            if source.language.lower().split("-", 1)[0] != "ko":
                self._finish(job_id, "EXCLUDED_LANGUAGE", "EXCLUDED_LANGUAGE")
                return
            typesafe_key = SecretStr(
                os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY", "")
            )
            with pilot.activate(), pacing_scope(self._pacer):
                selection = select_body_sources(
                    source.document.sources,
                    title=source.title,
                    publisher=source.publisher,
                    api_key=typesafe_key,
                ).selection
            runtime = RuntimeInput(
                source.document,
                ontology,
                _EXTRACTION_LIMITS,
                body_selection=selection,
            )
            judgment_policy = JevJudgmentPolicy(PublicationSource("OTHER"))
            execution = extraction_tasks.ExecutionInput(
                validator_version=product_lint.VALIDATOR_VERSION,
                runtime_settings={
                    "extraction_runner": runtime.identity_settings(),
                    "source_provenance": {"kind": "OTHER"},
                    "jev_judgment": cast(
                        dict[str, JsonValue], judgment_policy.identity_settings()
                    ),
                },
                execution_generation=f"source-intake-v1-job-{job_id}",
            )
            with Session(self._engine) as session, session.begin():
                task = extraction_tasks.enqueue_extraction(
                    session, job.source_document_id, execution
                )
            with self._engine.begin() as connection:
                source_processing.update_processing_job(
                    connection,
                    job_id=job_id,
                    stage="EXTRACTION",
                    extraction_task_id=task.task_id,
                )
            openai_key = SecretStr(os.environ.get("OPENAI_API_KEY", ""))
            with openai_clients(
                Budget(None, None, demo_uncapped=True),
                api_key=openai_key,
                pacer=self._pacer,
            ) as clients:
                helpers = JevClaimReviewModels(
                    clients.helpers, typesafe_key, judgment_policy
                )
                proposer = JevExistingCandidateProposer(
                    clients.resolution_proposer(_JUDGMENT_LIMITS),
                    typesafe_key,
                    judgment_policy,
                )
                result = self._run_until_finalized(
                    job_id,
                    job.source_document_id,
                    task.task_id,
                    execution,
                    runtime,
                    helpers,
                    proposer,
                    clients,
                    pilot,
                )
                if result is not None:
                    self._complete_result(job_id, result, clients, pilot)
        except Exception as error:
            with suppress(Exception):
                self._fail_preparing_publication(job_id, "PUBLICATION_EXCEPTION")
            with suppress(Exception):
                self._finish(job_id, "FAILED", "FAILED", _safe_error(error))
        finally:
            if pilot is not None:
                pilot.close()

    def _run_until_finalized(
        self,
        job_id: int,
        document_id: int,
        task_id: int,
        execution: extraction_tasks.ExecutionInput,
        runtime: RuntimeInput,
        helpers: JevClaimReviewModels,
        proposer: JevExistingCandidateProposer,
        clients: OpenAIClients,
        pilot: PilotBudget,
    ) -> ExtractionPublicationResult | None:
        while not self._stopping.is_set():
            result = application_execution.run_document(
                self._engine,
                document_id,
                f"source-worker-{job_id}",
                execution,
                runtime,
                helpers,
                clients.generation.prepare,
                proposer,
                clients.node_context.prepare,
                clients.followup.prepare,
                clients.insight.prepare,
                pilot=pilot,
                finalization_lock=self._finalization_lock,
                before_finalization=lambda: self._stage(job_id, "PROMOTION"),
                after_promotion=lambda product: self._record_promotion(
                    job_id, product.promotion_batch_id
                ),
            )
            if isinstance(result, ExtractionPublicationResult):
                if result.product.disposition not in {"RETRY_WAIT", "LEASE_LOST"}:
                    return result
                delay = self._task_retry_delay(task_id)
                if delay is None:
                    return result
                self._stopping.wait(delay)
                continue
            if (
                result.task_status == "FINAL_FAILED"
                or result.disposition == "LEASE_LOST"
            ):
                self._finish(
                    job_id,
                    "FAILED",
                    "EXTRACTION_FAILED",
                    result.error_code or f"EXTRACTION_{result.disposition}",
                )
                return None
            delay = self._task_retry_delay(task_id)
            if delay is None:
                self._finish(
                    job_id, "FAILED", "EXTRACTION_FAILED", "EXTRACTION_NOT_RESUMABLE"
                )
                return None
            self._stopping.wait(delay)
        self._finish(job_id, "INTERRUPTED", "INTERRUPTED", "PROCESS_STOPPED")
        return None

    def _stage(self, job_id: int, stage: str) -> None:
        with self._engine.begin() as connection:
            source_processing.update_processing_job(
                connection, job_id=job_id, stage=stage
            )

    def _record_promotion(self, job_id: int, batch_id: int | None) -> None:
        if batch_id is None:
            return
        with self._engine.begin() as connection:
            source_processing.update_processing_job(
                connection,
                job_id=job_id,
                stage="PUBLICATION",
                promotion_batch_id=batch_id,
            )

    def _task_retry_delay(self, task_id: int) -> float | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                sa.select(
                    schema.model_task.c.status,
                    schema.model_task.c.next_attempt_at,
                    schema.model_task.c.lease_expires_at,
                ).where(schema.model_task.c.model_task_id == task_id)
            ).one()
        status = str(row.status)
        if status in {"FINAL_FAILED", "SUCCESS"}:
            return None
        target = row.next_attempt_at or row.lease_expires_at
        if target is None:
            return 0.05
        return max(
            0.05,
            (cast(datetime, target) - datetime.now(UTC)).total_seconds(),
        )

    def _complete_result(
        self,
        job_id: int,
        result: ExtractionPublicationResult,
        clients: OpenAIClients,
        pilot: PilotBudget,
    ) -> None:
        batch_id = result.product.promotion_batch_id
        if result.product.disposition != "SUCCESS" or batch_id is None:
            error = result.product.error_code or "NO_PUBLISHABLE_CLAIMS"
            self._finish(job_id, "FAILED", "PROMOTION_FAILED", error)
            return
        publication = result.publication
        failure_reason: str | None = None
        while publication is not None and not publication.ready:
            if publication.publication_status == "FAILED":
                failure_reason = "PUBLICATION_TASK_FAILED"
                break
            delay = self._publication_retry_delay(batch_id)
            if delay is None:
                failure_reason = "PUBLICATION_TASK_FAILED"
                break
            if self._stopping.wait(delay):
                break
            publication = application_execution.resume_publication(
                self._engine,
                batch_id,
                f"source-worker-{job_id}",
                prepare_node_context_provider=clients.node_context.prepare,
                prepare_followup_provider=clients.followup.prepare,
                prepare_insight_provider=clients.insight.prepare,
                pilot=pilot,
            )
        if publication is not None and publication.ready:
            self._finish(job_id, "READY", "READY")
        elif self._stopping.is_set():
            self._finish(job_id, "INTERRUPTED", "INTERRUPTED", "PROCESS_STOPPED")
        else:
            self._fail_preparing_publication(
                job_id, failure_reason or "PUBLICATION_NOT_READY"
            )
            self._finish(
                job_id,
                "FAILED",
                "PUBLICATION_FAILED",
                failure_reason or "PUBLICATION_NOT_READY",
            )

    def _fail_preparing_publication(self, job_id: int, reason: str) -> None:
        with (
            self._finalization_lock,
            Session(self._engine) as session,
            session.begin(),
        ):
            batch_id = session.scalar(
                sa.select(schema.source_processing_job.c.promotion_batch_id).where(
                    schema.source_processing_job.c.source_processing_job_id == job_id
                )
            )
            if batch_id is None:
                return
            status = session.scalar(
                sa.select(schema.promotion_batch.c.publication_status).where(
                    schema.promotion_batch.c.promotion_batch_id == batch_id
                )
            )
            if status == "PREPARING":
                initial_publication.mark_initial_publication_failed(
                    session, int(batch_id), reason
                )

    def _publication_retry_delay(self, batch_id: int) -> float | None:
        with Session(self._engine) as session, session.begin():
            affected = session.execute(
                sa.select(
                    schema.publication_affected_node.c.node_id,
                    schema.publication_affected_node.c.node_context_id,
                    schema.promotion_batch.c.committed_at,
                )
                .join(
                    schema.promotion_batch,
                    schema.promotion_batch.c.promotion_batch_id
                    == schema.publication_affected_node.c.promotion_batch_id,
                )
                .where(
                    schema.publication_affected_node.c.promotion_batch_id == batch_id
                )
            ).mappings()
            task_ids: list[int] = []
            for item in affected:
                context_id = item["node_context_id"]
                as_of_at = cast(datetime, item["committed_at"])
                if context_id is None:
                    prepared = initial_publication.prepare_node_context(
                        session,
                        promotion_batch_id=batch_id,
                        node_id=int(item["node_id"]),
                    )
                    task_ids.append(
                        initial_publication.ensure_node_context_task(
                            session, prepared
                        ).model_task_id
                    )
                else:
                    context_task_id = session.scalar(
                        sa.select(schema.node_context.c.model_task_id).where(
                            schema.node_context.c.node_context_id == context_id
                        )
                    )
                    if context_task_id is not None:
                        task_ids.append(int(context_task_id))
                    followup = followup_tasks.enqueue_followup(
                        session,
                        int(context_id),
                        as_of_at,
                    )
                    task_ids.append(followup.task_id)
                    insight = insight_tasks.enqueue_insight(
                        session,
                        batch_id,
                        int(item["node_id"]),
                        as_of_at,
                    )
                    task_ids.append(insight.task_id)
            rows = (
                session.execute(
                    sa.select(
                        schema.model_task.c.status,
                        schema.model_task.c.next_attempt_at,
                        schema.model_task.c.lease_expires_at,
                    ).where(schema.model_task.c.model_task_id.in_(task_ids))
                )
                .mappings()
                .all()
                if task_ids
                else []
            )
        if any(row["status"] in {"FINAL_FAILED", "VALIDATION_BLOCKED"} for row in rows):
            return None
        targets = [
            row["next_attempt_at"] or row["lease_expires_at"]
            for row in rows
            if row["status"] in {"RETRY_WAIT", "RUNNING"}
        ]
        targets = [target for target in targets if target is not None]
        if targets:
            target = cast(datetime, min(targets))
            return max(0.05, (target - datetime.now(UTC)).total_seconds())
        if any(row["status"] == "PENDING" for row in rows):
            return 0.05
        with self._engine.connect() as connection:
            publication_status = connection.scalar(
                sa.select(schema.promotion_batch.c.publication_status).where(
                    schema.promotion_batch.c.promotion_batch_id == batch_id
                )
            )
        return 1.0 if publication_status == "PREPARING" else None


def submit_source_processing(job_id: int) -> None:
    """Submit only after the API transaction that created the job commits."""
    with _active_worker_lock:
        worker = _active_worker
    if worker is None:
        raise RuntimeError("SOURCE_WORKER_NOT_RUNNING")
    worker.submit(job_id)


def try_submit_source_processing(job_id: int) -> bool:
    """Return false when automatic processing is intentionally disabled."""
    with _active_worker_lock:
        worker = _active_worker
    if worker is None:
        return False
    worker.submit(job_id)
    return True
