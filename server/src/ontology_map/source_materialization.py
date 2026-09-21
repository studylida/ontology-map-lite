from __future__ import annotations

import gzip
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from ontology_map.db.source_materialization import (
    EvidenceGroupConflict,
    EvidenceGroupNotFound,
    SourceDocumentInput,
    materialize_source_document,
)

_TIME_PRECISIONS = frozenset({"INSTANT", "DAY", "MONTH", "YEAR", "UNKNOWN"})
_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class PreparationFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SelectionItem:
    test_item_id: str
    discovery_key: str
    source_key: str
    artifact_key: str
    canonical_url: str
    title: str
    publisher: str
    author_text: str | None
    original_language: str
    published_at: datetime | None
    published_precision: str
    source_modified_at: datetime | None
    modified_precision: str
    body_sha256: str
    qualification_ref: str
    lineage_ref: str


@dataclass(frozen=True)
class MaterializationApproval:
    qualification_ref: str
    lineage_ref: str
    evidence_group_id: int | None
    create_new_group: bool = False


@dataclass(frozen=True)
class MaterializationResult:
    test_item_id: str
    status: str
    source_document_id: int | None = None
    version_no: int | None = None
    failure_code: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True)
class PreparedSelectionItem:
    item: SelectionItem
    document: SourceDocumentInput
    evidence_group_id: int | None


def normalize_source_body(value: str) -> str:
    """Apply the exact normalization used when the fixed #131 corpus was created."""

    normalized = unicodedata.normalize(
        "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
    )
    lines = [line.rstrip() for line in normalized.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _nonblank(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PreparationFailure("MANIFEST_INVALID", f"{key} must be a nonblank string")
    return value.strip()


def _optional_nonblank(record: Mapping[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PreparationFailure("MANIFEST_INVALID", f"{key} must be null or nonblank")
    return value.strip()


def _parse_timestamp(value: Any, *, field: str, precision: str) -> datetime | None:
    if precision == "UNKNOWN":
        if value is not None:
            raise PreparationFailure(
                "MANIFEST_INVALID", f"{field} must be null when precision is UNKNOWN"
            )
        return None
    if not isinstance(value, str) or not value.strip():
        raise PreparationFailure(
            "MANIFEST_INVALID", f"{field} is required when precision is {precision}"
        )
    text = value.strip()
    if precision == "DAY" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise PreparationFailure("MANIFEST_INVALID", f"invalid {field}") from error
    if parsed.tzinfo is None:
        raise PreparationFailure("MANIFEST_INVALID", f"{field} must include timezone")
    return parsed.astimezone(UTC)


def parse_selection_item(record: Mapping[str, Any]) -> SelectionItem:
    published_precision = _nonblank(record, "published_precision").upper()
    modified_precision_raw = record.get("modified_precision", "UNKNOWN")
    if (
        not isinstance(modified_precision_raw, str)
        or not modified_precision_raw.strip()
    ):
        raise PreparationFailure(
            "MANIFEST_INVALID", "modified_precision must be a nonblank string"
        )
    modified_precision = modified_precision_raw.strip().upper()
    if published_precision not in _TIME_PRECISIONS:
        raise PreparationFailure("MANIFEST_INVALID", "unsupported published_precision")
    if modified_precision not in _TIME_PRECISIONS:
        raise PreparationFailure("MANIFEST_INVALID", "unsupported modified_precision")

    body_sha256 = _nonblank(record, "body_sha256").lower()
    if not _SHA256_HEX.fullmatch(body_sha256):
        raise PreparationFailure("MANIFEST_INVALID", "body_sha256 must be 64 hex chars")

    return SelectionItem(
        test_item_id=_nonblank(record, "test_item_id"),
        discovery_key=_nonblank(record, "discovery_key"),
        source_key=_nonblank(record, "source_key"),
        artifact_key=_nonblank(record, "artifact_key"),
        canonical_url=_nonblank(record, "canonical_url"),
        title=_nonblank(record, "title"),
        publisher=_nonblank(record, "publisher"),
        author_text=_optional_nonblank(record, "author_text"),
        original_language=_nonblank(record, "original_language"),
        published_at=_parse_timestamp(
            record.get("published_at"),
            field="published_at",
            precision=published_precision,
        ),
        published_precision=published_precision,
        source_modified_at=_parse_timestamp(
            record.get("source_modified_at"),
            field="source_modified_at",
            precision=modified_precision,
        ),
        modified_precision=modified_precision,
        body_sha256=body_sha256,
        qualification_ref=_nonblank(record, "qualification_ref"),
        lineage_ref=_nonblank(record, "lineage_ref"),
    )


def _artifact_path(artifact_root: Path, artifact_key: str) -> Path:
    pure_key = PurePosixPath(artifact_key)
    if pure_key.is_absolute() or any(
        part in {"", ".", ".."} for part in pure_key.parts
    ):
        raise PreparationFailure(
            "ARTIFACT_PATH_INVALID", "artifact_key must be relative"
        )
    try:
        root = artifact_root.resolve(strict=True)
    except OSError as error:
        raise PreparationFailure(
            "ARTIFACT_ROOT_UNREADABLE", "artifact root unavailable"
        ) from error
    candidate = root.joinpath(*pure_key.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise PreparationFailure(
            "ARTIFACT_MISSING", "artifact is unavailable"
        ) from error
    if not resolved.is_file():
        raise PreparationFailure("ARTIFACT_MISSING", "artifact is not a regular file")
    return resolved


def _read_artifact_text(path: Path) -> str:
    try:
        raw = path.read_bytes()
        if path.name.endswith(".txt.gz"):
            raw = gzip.decompress(raw)
        elif path.suffix != ".txt":
            raise PreparationFailure(
                "ARTIFACT_FORMAT_UNSUPPORTED", "artifact must be .txt or .txt.gz"
            )
        return raw.decode("utf-8")
    except PreparationFailure:
        raise
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeDecodeError) as error:
        raise PreparationFailure(
            "ARTIFACT_UNREADABLE", "artifact could not be read"
        ) from error


def prepare_selection_item(
    *,
    item: SelectionItem,
    artifact_root: Path,
    approval: MaterializationApproval | None,
) -> PreparedSelectionItem:
    if approval is None:
        raise PreparationFailure(
            "APPROVAL_MISSING", "qualification and lineage approval is required"
        )
    if approval.create_new_group != (approval.evidence_group_id is None) or (
        approval.evidence_group_id is not None and approval.evidence_group_id <= 0
    ):
        raise PreparationFailure(
            "APPROVAL_INVALID", "approve either a new group or a positive existing ID"
        )
    if approval.qualification_ref != item.qualification_ref:
        raise PreparationFailure(
            "QUALIFICATION_NOT_CONFIRMED", "qualification reference does not match"
        )
    if approval.lineage_ref != item.lineage_ref:
        raise PreparationFailure(
            "LINEAGE_NOT_CONFIRMED", "lineage reference does not match"
        )

    artifact_path = _artifact_path(artifact_root, item.artifact_key)
    normalized_body = normalize_source_body(_read_artifact_text(artifact_path))
    if not normalized_body:
        raise PreparationFailure("ARTIFACT_EMPTY", "normalized artifact body is empty")
    body_hash = sha256(normalized_body.encode("utf-8")).digest()
    if body_hash.hex() != item.body_sha256:
        raise PreparationFailure(
            "CHECKSUM_MISMATCH", "artifact body checksum does not match"
        )

    return PreparedSelectionItem(
        item=item,
        evidence_group_id=approval.evidence_group_id,
        document=SourceDocumentInput(
            source_key=item.source_key,
            canonical_url=item.canonical_url,
            publisher_name=item.publisher,
            title=item.title,
            author_text=item.author_text,
            original_language=item.original_language,
            normalized_body=normalized_body,
            body_hash=body_hash,
            published_at=item.published_at,
            published_precision=item.published_precision,
            source_modified_at=item.source_modified_at,
            modified_precision=item.modified_precision,
        ),
    )


def _failed_result(
    test_item_id: str, failure: PreparationFailure
) -> MaterializationResult:
    return MaterializationResult(
        test_item_id=test_item_id,
        status="FAILED",
        failure_code=failure.code,
        failure_message=str(failure),
    )


def _parse_manifest_line(
    line: str,
    *,
    line_number: int,
    only_test_item_ids: frozenset[str] | None,
) -> SelectionItem | MaterializationResult | None:
    fallback_id = f"line:{line_number}"
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return _failed_result(
            fallback_id,
            PreparationFailure("MANIFEST_INVALID", "manifest line is not valid JSON"),
        )
    if not isinstance(raw, dict):
        return _failed_result(
            fallback_id,
            PreparationFailure("MANIFEST_INVALID", "manifest line must be an object"),
        )
    raw_id = raw.get("test_item_id")
    if isinstance(raw_id, str) and raw_id.strip():
        fallback_id = raw_id.strip()
    if only_test_item_ids is not None and fallback_id not in only_test_item_ids:
        return None
    try:
        return parse_selection_item(raw)
    except PreparationFailure as failure:
        return _failed_result(fallback_id, failure)


def _duplicate_test_item_ids(
    parsed_items: list[SelectionItem | MaterializationResult],
) -> frozenset[str]:
    counts: dict[str, int] = {}
    for parsed in parsed_items:
        counts[parsed.test_item_id] = counts.get(parsed.test_item_id, 0) + 1
    return frozenset(
        test_item_id for test_item_id, count in counts.items() if count > 1
    )


def _prepare_item_result(
    item: SelectionItem,
    *,
    artifact_root: Path,
    approvals: Mapping[str, MaterializationApproval],
) -> PreparedSelectionItem | MaterializationResult:
    try:
        return prepare_selection_item(
            item=item,
            artifact_root=artifact_root,
            approval=approvals.get(item.test_item_id),
        )
    except PreparationFailure as failure:
        return _failed_result(item.test_item_id, failure)


def _write_prepared_item(
    engine: Engine,
    *,
    prepared: PreparedSelectionItem,
    checked_at: datetime,
) -> MaterializationResult:
    item = prepared.item
    try:
        with engine.begin() as connection:
            write_result = materialize_source_document(
                connection,
                document=prepared.document,
                evidence_group_id=prepared.evidence_group_id,
                checked_at=checked_at,
            )
    except EvidenceGroupNotFound:
        return _failed_result(
            item.test_item_id,
            PreparationFailure(
                "EVIDENCE_GROUP_NOT_FOUND",
                "approved Evidence Group is unavailable",
            ),
        )
    except EvidenceGroupConflict:
        return _failed_result(
            item.test_item_id,
            PreparationFailure(
                "EVIDENCE_GROUP_CONFLICT",
                "source_key is already bound to another Evidence Group",
            ),
        )
    except SQLAlchemyError:
        return _failed_result(
            item.test_item_id,
            PreparationFailure("DATABASE_WRITE_FAILED", "source document write failed"),
        )
    return MaterializationResult(
        test_item_id=item.test_item_id,
        status="CREATED" if write_result.created else "REUSED",
        source_document_id=write_result.source_document_id,
        version_no=write_result.version_no,
    )


def materialize_selection_manifest(
    engine: Engine,
    *,
    manifest_path: Path,
    artifact_root: Path,
    approvals: Mapping[str, MaterializationApproval],
    checked_at: datetime | None = None,
    only_test_item_ids: frozenset[str] | None = None,
) -> list[MaterializationResult]:
    """Persist manifest items independently without any network fallback."""

    effective_checked_at = checked_at or datetime.now(UTC)
    if effective_checked_at.tzinfo is None:
        raise ValueError("checked_at must be timezone-aware")
    effective_checked_at = effective_checked_at.astimezone(UTC)

    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PreparationFailure(
            "MANIFEST_UNREADABLE", "selection manifest is unavailable"
        ) from error

    parsed_items: list[SelectionItem | MaterializationResult] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parsed = _parse_manifest_line(
            line,
            line_number=line_number,
            only_test_item_ids=only_test_item_ids,
        )
        if parsed is not None:
            parsed_items.append(parsed)

    duplicate_ids = _duplicate_test_item_ids(parsed_items)
    results: list[MaterializationResult] = []
    for parsed in parsed_items:
        if parsed.test_item_id in duplicate_ids:
            results.append(
                _failed_result(
                    parsed.test_item_id,
                    PreparationFailure(
                        "MANIFEST_DUPLICATE_ITEM",
                        "test_item_id appears more than once in selected manifest rows",
                    ),
                )
            )
            continue
        if isinstance(parsed, MaterializationResult):
            results.append(parsed)
            continue
        prepared = _prepare_item_result(
            parsed, artifact_root=artifact_root, approvals=approvals
        )
        if isinstance(prepared, MaterializationResult):
            results.append(prepared)
            continue
        results.append(
            _write_prepared_item(
                engine, prepared=prepared, checked_at=effective_checked_at
            )
        )
    return results
