"""Durable state for processing an uploaded source document."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy import Connection

from ontology_map.db import schema

ProcessingStatus = Literal[
    "QUEUED",
    "RUNNING",
    "READY",
    "FAILED",
    "INTERRUPTED",
    "EXCLUDED_LANGUAGE",
]
TerminalProcessingStatus = Literal[
    "READY", "FAILED", "INTERRUPTED", "EXCLUDED_LANGUAGE"
]
_REUSABLE_STATUSES = ("QUEUED", "RUNNING", "READY", "EXCLUDED_LANGUAGE")


@dataclass(frozen=True)
class SourceProcessingJob:
    source_processing_job_id: int
    source_document_id: int
    status: ProcessingStatus
    stage: str
    extraction_task_id: int | None
    promotion_batch_id: int | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _job(row: sa.RowMapping) -> SourceProcessingJob:
    return SourceProcessingJob(
        source_processing_job_id=int(row["source_processing_job_id"]),
        source_document_id=int(row["source_document_id"]),
        status=cast(ProcessingStatus, row["status"]),
        stage=str(row["stage"]),
        extraction_task_id=(
            None
            if row["extraction_task_id"] is None
            else int(row["extraction_task_id"])
        ),
        promotion_batch_id=(
            None
            if row["promotion_batch_id"] is None
            else int(row["promotion_batch_id"])
        ),
        error_code=None if row["error_code"] is None else str(row["error_code"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _select_job(job_id: int) -> sa.Select:
    return sa.select(schema.source_processing_job).where(
        schema.source_processing_job.c.source_processing_job_id == job_id
    )


def create_or_reuse_processing_job(
    connection: Connection, *, source_document_id: int
) -> SourceProcessingJob:
    """Create one attempt unless this document already has reusable work."""

    source_exists = connection.scalar(
        sa.select(schema.source_document.c.source_document_id)
        .where(schema.source_document.c.source_document_id == source_document_id)
        .with_for_update()
    )
    if source_exists is None:
        raise LookupError("SOURCE_DOCUMENT_NOT_FOUND")
    reusable = (
        connection.execute(
            sa.select(schema.source_processing_job)
            .where(
                schema.source_processing_job.c.source_document_id == source_document_id,
                schema.source_processing_job.c.status.in_(_REUSABLE_STATUSES),
            )
            .order_by(schema.source_processing_job.c.source_processing_job_id.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    if reusable is not None:
        return _job(reusable)
    row = (
        connection.execute(
            schema.source_processing_job.insert()
            .values(source_document_id=source_document_id)
            .returning(schema.source_processing_job)
        )
        .mappings()
        .one()
    )
    return _job(row)


def latest_processing_job(
    connection: Connection, *, source_document_id: int
) -> SourceProcessingJob | None:
    row = (
        connection.execute(
            sa.select(schema.source_processing_job)
            .where(
                schema.source_processing_job.c.source_document_id == source_document_id
            )
            .order_by(schema.source_processing_job.c.source_processing_job_id.desc())
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else _job(row)


def get_processing_job(
    connection: Connection, *, job_id: int
) -> SourceProcessingJob | None:
    row = connection.execute(_select_job(job_id)).mappings().one_or_none()
    return None if row is None else _job(row)


def start_processing_job(
    connection: Connection,
    *,
    job_id: int,
    stage: str,
    now: datetime | None = None,
) -> SourceProcessingJob | None:
    started_at = (now or datetime.now(UTC)).astimezone(UTC)
    row = (
        connection.execute(
            schema.source_processing_job.update()
            .where(
                schema.source_processing_job.c.source_processing_job_id == job_id,
                schema.source_processing_job.c.status == "QUEUED",
            )
            .values(status="RUNNING", stage=stage, started_at=started_at)
            .returning(schema.source_processing_job)
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else _job(row)


def update_processing_job(
    connection: Connection,
    *,
    job_id: int,
    stage: str,
    extraction_task_id: int | None = None,
    promotion_batch_id: int | None = None,
) -> SourceProcessingJob | None:
    values: dict[str, object] = {"stage": stage}
    if extraction_task_id is not None:
        values["extraction_task_id"] = extraction_task_id
    if promotion_batch_id is not None:
        values["promotion_batch_id"] = promotion_batch_id
    row = (
        connection.execute(
            schema.source_processing_job.update()
            .where(
                schema.source_processing_job.c.source_processing_job_id == job_id,
                schema.source_processing_job.c.status == "RUNNING",
            )
            .values(**values)
            .returning(schema.source_processing_job)
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else _job(row)


def finish_processing_job(
    connection: Connection,
    *,
    job_id: int,
    status: TerminalProcessingStatus,
    stage: str,
    error_code: str | None = None,
    now: datetime | None = None,
) -> SourceProcessingJob | None:
    if (status in {"FAILED", "INTERRUPTED"}) != (error_code is not None):
        raise ValueError("terminal status and error_code disagree")
    finished_at = (now or datetime.now(UTC)).astimezone(UTC)
    allowed_statuses = (
        ("QUEUED", "RUNNING") if status == "INTERRUPTED" else ("RUNNING",)
    )
    row = (
        connection.execute(
            schema.source_processing_job.update()
            .where(
                schema.source_processing_job.c.source_processing_job_id == job_id,
                schema.source_processing_job.c.status.in_(allowed_statuses),
            )
            .values(
                status=status,
                stage=stage,
                error_code=error_code,
                finished_at=finished_at,
            )
            .returning(schema.source_processing_job)
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else _job(row)


def elapsed_seconds(
    job: SourceProcessingJob, *, now: datetime | None = None
) -> float | None:
    if job.started_at is None:
        return None
    end = job.finished_at or (now or datetime.now(UTC)).astimezone(UTC)
    return max(0.0, (end - job.started_at).total_seconds())
