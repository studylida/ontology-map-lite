"""Parse uploaded source files and persist their normalized document body."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

import sqlalchemy as sa
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from sqlalchemy.engine import Engine

from ontology_map.db.source_materialization import (
    EvidenceGroupConflict,
    EvidenceGroupNotFound,
    SourceDocumentInput,
    SourceDocumentWriteResult,
    materialize_source_document,
)
from ontology_map.db.source_processing import (
    SourceProcessingJob,
    create_or_reuse_processing_job,
)
from ontology_map.extraction_contracts import SourceSpan
from ontology_map.source_materialization import normalize_source_body

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_TEXT_EXTENSIONS = {".txt", ".md"}
_PDF_EXTENSIONS = {".pdf"}
_DOCX_EXTENSIONS = {".docx"}


class SourceIntakeError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class SourceIntakeMetadata:
    filename: str
    title: str | None = None
    publisher_name: str = "사용자 업로드"
    author_text: str | None = None
    original_language: str = "ko"
    canonical_url: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True)
class SourceIntakeResult:
    write: SourceDocumentWriteResult
    processing_job: SourceProcessingJob
    normalized_body: str
    body_hash: str
    canonical_url: str | None


def _suffix(filename: str) -> str:
    return Path(filename).suffix.lower()


def _text_from_docx(payload: bytes) -> str:
    try:
        document = Document(io.BytesIO(payload))
        blocks: list[str] = []
        body = document.element.body
        for child in body.iterchildren():
            if isinstance(child, CT_P):
                blocks.append(Paragraph(child, document).text)
            elif isinstance(child, CT_Tbl):
                table = Table(child, document)
                blocks.extend(
                    "\t".join(cell.text.strip() for cell in row.cells)
                    for row in table.rows
                )
        return "\n".join(blocks)
    except Exception as error:
        raise SourceIntakeError(
            "SOURCE_TEXT_UNAVAILABLE", "DOCX 본문을 읽을 수 없습니다."
        ) from error


def _text_from_pdf(payload: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(payload))
        if reader.is_encrypted:
            raise SourceIntakeError(
                "SOURCE_TEXT_UNAVAILABLE", "암호화된 PDF는 처리할 수 없습니다."
            )
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except SourceIntakeError:
        raise
    except Exception as error:
        raise SourceIntakeError(
            "SOURCE_TEXT_UNAVAILABLE", "PDF 본문을 읽을 수 없습니다."
        ) from error


def extract_normalized_body(
    payload: bytes, *, filename: str, content_type: str | None = None
) -> str:
    """Extract text without OCR and apply the corpus normalization contract."""

    if len(payload) > MAX_UPLOAD_BYTES:
        raise SourceIntakeError(
            "SOURCE_TOO_LARGE",
            "파일은 10 MiB 이하만 업로드할 수 있습니다.",
            status_code=413,
        )
    suffix = _suffix(filename)
    mime = (content_type or "").split(";", 1)[0].lower()
    if suffix in _TEXT_EXTENSIONS or mime in {"text/plain", "text/markdown"}:
        try:
            raw = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SourceIntakeError(
                "SOURCE_TEXT_UNAVAILABLE", "텍스트 파일은 UTF-8이어야 합니다."
            ) from error
    elif suffix in _PDF_EXTENSIONS or mime == "application/pdf":
        raw = _text_from_pdf(payload)
    elif suffix in _DOCX_EXTENSIONS or mime == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        raw = _text_from_docx(payload)
    else:
        raise SourceIntakeError(
            "SOURCE_FORMAT_UNSUPPORTED",
            "TXT, MD, PDF, DOCX 파일만 업로드할 수 있습니다.",
            status_code=415,
        )
    normalized = normalize_source_body(raw)
    if not normalized:
        raise SourceIntakeError("SOURCE_EMPTY", "본문을 추출하지 못했습니다.")
    return normalized


def split_source_spans(body: str) -> tuple[SourceSpan, ...]:
    """Project the normalized body into the line spans used by Jev and extraction."""

    return tuple(
        SourceSpan(
            source_id=f"s{index}",
            start=match.start(),
            end=match.end(),
            quote=match.group(),
            quote_hash=sha256(match.group().encode()).hexdigest(),
            paragraph_id=f"p{index}",
        )
        for index, match in enumerate(
            match for match in re.finditer(r"[^\n]+", body) if match.group().strip()
        )
    )


def parse_published_at(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = date.fromisoformat(text)
        return datetime.combine(parsed, datetime.min.time(), UTC)
    except ValueError:
        try:
            parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise SourceIntakeError(
                "INVALID_PUBLISHED_AT", "게시일은 ISO 날짜 또는 날짜시간이어야 합니다."
            ) from error
        if parsed_datetime.tzinfo is None:
            raise SourceIntakeError(
                "INVALID_PUBLISHED_AT", "날짜시간에는 시간대가 필요합니다."
            ) from None
        return parsed_datetime.astimezone(UTC)


def _nonblank(value: str | None, *, field: str, fallback: str | None = None) -> str:
    candidate = value.strip() if value is not None else ""
    if not candidate:
        candidate = fallback or ""
    if not candidate:
        raise SourceIntakeError("INVALID_METADATA", f"{field}을 입력해 주세요.")
    return candidate


def _title(metadata: SourceIntakeMetadata) -> str:
    filename = Path(metadata.filename).name
    stem = Path(filename).stem
    return _nonblank(metadata.title, field="제목", fallback=stem or filename)


def materialize_uploaded_source(
    engine: Engine,
    *,
    payload: bytes,
    metadata: SourceIntakeMetadata,
    content_type: str | None = None,
    checked_at: datetime | None = None,
) -> SourceIntakeResult:
    normalized_body = extract_normalized_body(
        payload, filename=metadata.filename, content_type=content_type
    )
    body_hash = sha256(normalized_body.encode("utf-8")).digest()
    canonical_url = metadata.canonical_url.strip() if metadata.canonical_url else None
    if canonical_url == "":
        canonical_url = None
    publisher = _nonblank(
        metadata.publisher_name, field="발행처", fallback="사용자 업로드"
    )
    language = _nonblank(metadata.original_language, field="언어", fallback="ko")
    author = metadata.author_text.strip() if metadata.author_text else None
    if author == "":
        author = None
    document = SourceDocumentInput(
        source_key=f"upload:{body_hash.hex()}",
        canonical_url=canonical_url,
        publisher_name=publisher,
        title=_title(metadata),
        author_text=author,
        original_language=language,
        normalized_body=normalized_body,
        body_hash=body_hash,
        published_at=metadata.published_at,
        published_precision="DAY" if metadata.published_at is not None else "UNKNOWN",
        source_modified_at=None,
        modified_precision="UNKNOWN",
    )
    effective_checked_at = checked_at or datetime.now(UTC)
    if effective_checked_at.tzinfo is None:
        raise ValueError("checked_at must be timezone-aware")
    try:
        with engine.begin() as connection:
            write = materialize_source_document(
                connection,
                document=document,
                evidence_group_id=None,
                checked_at=effective_checked_at.astimezone(UTC),
            )
            processing_job = create_or_reuse_processing_job(
                connection, source_document_id=write.source_document_id
            )
    except (EvidenceGroupConflict, EvidenceGroupNotFound) as error:
        raise SourceIntakeError(
            "SOURCE_LINEAGE_CONFLICT", "같은 자료의 계보가 충돌합니다.", status_code=409
        ) from error
    except sa.exc.SQLAlchemyError as error:
        raise SourceIntakeError(
            "SOURCE_WRITE_FAILED", "자료를 저장하지 못했습니다.", status_code=500
        ) from error
    return SourceIntakeResult(
        write=write,
        processing_job=processing_job,
        normalized_body=normalized_body,
        body_hash=body_hash.hex(),
        canonical_url=canonical_url,
    )
