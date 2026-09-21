"""Private full HTTP bodies and safe sidecars; never a product result or ledger.

All IO is best effort. Reuse the pilot's private directory by default and the
existing exclusive/0600/no-follow writer pattern. No request or headers enter
this module. Call context carries identifiers only, never changes task state.
"""

import json
import logging
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ontology_map.llm_config import MODEL_VERSION, PROVIDER
from ontology_map.llm_diagnostics import attach_response, failure_diagnostic

_task: ContextVar[tuple[int | None, int | None]] = ContextVar(
    "kimi_response_task", default=(None, None)
)


def _positive(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


@contextmanager
def response_task(task_id: int | None, slot_no: int | None = None) -> Iterator[None]:
    """The slot is a provider reservation number, not a fabricated attempt row."""
    token = _task.set((_positive(task_id), _positive(slot_no)))
    try:
        yield
    finally:
        _task.reset(token)


def _directory(path: Path) -> int:
    """Walk with directory FDs: no symlinks or Git-worktree ancestor allowed."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("PRIVATE_ARCHIVE_PATH")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            try:
                os.stat(".git", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("ARCHIVE_INSIDE_GIT")
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("PRIVATE_ARCHIVE_PERMISSIONS")
        try:
            os.stat(".git", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return fd
        raise ValueError("ARCHIVE_INSIDE_GIT")
    except BaseException:
        os.close(fd)
        raise


def _write(directory: Path, name: str, content: bytes) -> None:
    fd = _directory(directory)
    try:
        handle = os.open(
            name + ".part",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=fd,
        )
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Each response has its own exclusive directory; never replace originals.
        os.link(name + ".part", name, src_dir_fd=fd, dst_dir_fd=fd)
        os.unlink(name + ".part", dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)


def _warning() -> None:
    try:
        logging.getLogger(__name__).warning("LLM_RESPONSE_ARCHIVE_FAILED")
    except Exception:
        pass  # Even a broken logging handler must not change a product outcome.


@dataclass(repr=False)
class ResponseArchive:
    """One response, not one hash: repeated requests never overwrite each other."""

    response_id: str | None = None
    status: str = "NO_RESPONSE"
    http_status: int | None = None
    directory: Path | None = field(default=None, repr=False)

    def capture(
        self,
        body: bytes,
        *,
        role: str,
        provider: str = PROVIDER,
        model: str = MODEL_VERSION,
        headers: dict[str, str] | None = None,
        schema_name: str,
        request_hash: str,
        http_status: int,
        pilot_path: Path | None,
        pilot_sequence: int | None,
    ) -> None:
        try:
            self.http_status = http_status
            self.response_id = uuid4().hex
            self.status = "FAILED"
            response_dir = (
                "jev-responses" if provider == "typesafe" else f"{provider}-responses"
            )
            default = (
                pilot_path.parent / response_dir
                if pilot_path is not None
                else Path.home() / ".local/state/ontology-map" / response_dir
            )
            env_name = (
                "ONTOLOGY_MAP_JEV_RESPONSE_DIR"
                if provider == "typesafe"
                else "ONTOLOGY_MAP_OPENAI_RESPONSE_DIR"
            )
            root = Path(os.environ.get(env_name, str(default)))
            root_fd = _directory(root)
            try:
                os.mkdir(self.response_id, 0o700, dir_fd=root_fd)
            finally:
                os.close(root_fd)
            self.directory = root / self.response_id
            _write(self.directory, "response.body", body)
            task_id, slot_no = _task.get()
            metadata = {
                "version": 1,
                "response_id": self.response_id,
                "role": role,
                "provider": provider,
                "request_model": model,
                "rate_limit_headers": headers or {},
                "schema_name": schema_name,
                "request_sha256": request_hash,
                "response_sha256": sha256(body).hexdigest(),
                "response_bytes": len(body),
                "http_status": http_status,
                "received_at": datetime.now(UTC).isoformat(),
                "model_task_id": task_id,
                "provider_slot_no": slot_no,
                "pilot_sequence": pilot_sequence,
                "pilot_ledger_name": pilot_path.name if pilot_path else None,
            }
            _write(self.directory, "metadata.json", json.dumps(metadata).encode())
            self.status = "SAVED"
        except Exception:
            _warning()

    def annotate(self, metadata: dict[str, object]) -> None:
        """A separate safe sidecar; never replace captured bytes or metadata."""
        try:
            if self.directory is not None:
                _write(self.directory, "envelope.json", json.dumps(metadata).encode())
        except Exception:
            _warning()

    def failed(self, error: BaseException) -> None:
        """Persist only the existing safe diagnostic, never the exception itself."""
        try:
            attach_response(error, self.response_id, self.status)
            if self.directory is not None:
                _write(
                    self.directory,
                    "failure.json",
                    json.dumps(failure_diagnostic(error)).encode(),
                )
        except Exception:
            _warning()
