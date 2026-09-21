import threading
from contextlib import nullcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from ontology_map import source_worker
from ontology_map.settings import Settings
from ontology_map.source_worker import (
    SourceWorker,
    _safe_error,
    submit_source_processing,
    try_submit_source_processing,
)


def _settings(**values: object) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://user:pass@127.0.0.1/db",
        environment="test",
        **values,
    )


def test_source_processing_requires_explicit_uncapped_mode_and_private_path() -> None:
    with pytest.raises(ValidationError):
        _settings(source_processing_enabled=True)
    with pytest.raises(ValidationError):
        _settings(
            source_processing_enabled=True,
            demo_unbounded_provider=True,
            provider_ledger_dir=Path("relative"),
        )
    configured = _settings(
        source_processing_enabled=True,
        demo_unbounded_provider=True,
        provider_ledger_dir=Path("/tmp/private-ledgers"),
    )
    assert configured.source_processing_workers == 4


def test_worker_boundary_exposes_only_safe_errors() -> None:
    class SafeError(Exception):
        code = "JEV_UNAVAILABLE"

    assert _safe_error(SafeError()) == "JEV_UNAVAILABLE"
    assert _safe_error(RuntimeError("secret provider body")) == (
        "SOURCE_PROCESSING_FAILED"
    )
    with pytest.raises(RuntimeError, match="SOURCE_WORKER_NOT_RUNNING"):
        submit_source_processing(1)
    assert try_submit_source_processing(1) is False


def test_publication_exception_marks_preparing_batch_failed(monkeypatch) -> None:
    class Session:
        values = iter((11, "PREPARING"))

        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def begin(self):
            return nullcontext()

        def scalar(self, _statement):
            return next(self.values)

    marked = []
    monkeypatch.setattr(source_worker, "Session", Session)
    monkeypatch.setattr(
        source_worker.initial_publication,
        "mark_initial_publication_failed",
        lambda _session, batch_id, reason: marked.append((batch_id, reason)),
    )
    worker = object.__new__(SourceWorker)
    worker._engine = object()
    worker._finalization_lock = threading.Lock()

    worker._fail_preparing_publication(7, "PUBLICATION_EXCEPTION")

    assert marked == [(11, "PUBLICATION_EXCEPTION")]
