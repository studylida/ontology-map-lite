from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest

import ontology_map.source_intake as intake
from ontology_map.db.source_materialization import SourceDocumentWriteResult
from ontology_map.db.source_processing import SourceProcessingJob, elapsed_seconds

NOW = datetime(2026, 9, 21, tzinfo=UTC)


def test_upload_creates_job_in_source_materialization_transaction(monkeypatch) -> None:
    connection = object()

    class Engine:
        def begin(self):
            return nullcontext(connection)

    write = SourceDocumentWriteResult(42, 1, True)
    job = SourceProcessingJob(
        source_processing_job_id=7,
        source_document_id=42,
        status="QUEUED",
        stage="QUEUED",
        extraction_task_id=None,
        promotion_batch_id=None,
        error_code=None,
        created_at=NOW,
        started_at=None,
        finished_at=None,
    )
    seen: list[object] = []

    def materialize(actual_connection, **_kwargs):
        seen.append(actual_connection)
        return write

    def create_job(actual_connection, *, source_document_id):
        seen.append(actual_connection)
        assert source_document_id == 42
        return job

    monkeypatch.setattr(intake, "materialize_source_document", materialize)
    monkeypatch.setattr(intake, "create_or_reuse_processing_job", create_job)

    result = intake.materialize_uploaded_source(
        Engine(),
        payload="본문".encode(),
        metadata=intake.SourceIntakeMetadata(filename="source.txt"),
        checked_at=NOW,
    )

    assert seen == [connection, connection]
    assert result.processing_job == job


def test_processing_elapsed_time_uses_started_and_finished_times() -> None:
    job = SourceProcessingJob(
        source_processing_job_id=7,
        source_document_id=42,
        status="READY",
        stage="COMPLETE",
        extraction_task_id=9,
        promotion_batch_id=11,
        error_code=None,
        created_at=NOW,
        started_at=NOW + timedelta(seconds=2),
        finished_at=NOW + timedelta(seconds=7.5),
    )

    assert elapsed_seconds(job) == pytest.approx(5.5)
