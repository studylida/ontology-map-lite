# server/src/ontology_map/services/document_parser.py
"""문서(PDF, DOCX, TXT) 및 웹 URL 텍스트 추출기."""

import io
import re
from typing import Tuple
import httpx
from pypdf import PdfReader
import docx


def clean_html_to_text(html: str) -> str:
    """HTML 문자열에서 태그, 스크립트, 스타일을 제거하고 본문 텍스트만 추출합니다."""
    # <script>, <style> 블록 제거
    cleaned = re.sub(r"<(script|style).*?>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # 모든 HTML 태그 제거
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # 연속된 공백 및 줄바꿈 압축
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def parse_url(url: str) -> Tuple[str, str]:
    """웹 URL에서 본문 텍스트를 크롤링하여 (제목, 본문) 형태로 반환합니다."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()

    title_match = re.search(r"<title>(.*?)</title>", resp.text, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else url
    content = clean_html_to_text(resp.text)
    return title, content


def parse_pdf(file_bytes: bytes) -> str:
    """PDF 파일 바이너리에서 모든 페이지의 텍스트를 추출합니다."""
    reader = PdfReader(io.BytesIO(file_bytes))
    pages_text: list[str] = []

    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            pages_text.append(text)

    return "\n\n".join(pages_text)


def parse_docx(file_bytes: bytes) -> str:
    """DOCX 워드 파일 바이너리에서 단락 텍스트를 추출합니다."""
    doc = docx.Document(io.BytesIO(file_bytes))
    paragraphs: list[str] = []

    for p in doc.paragraphs:
        t = p.text.strip()
        if t:
            paragraphs.append(t)

    return "\n\n".join(paragraphs)


def extract_document_text(
    source_type: str,
    raw_data: bytes | str,
    filename_or_url: str | None = None,
) -> Tuple[str, str]:
    """입력 타입(url, file, text)에 따라 문서 제목과 텍스트 본문을 정규화하여 반환합니다."""
    if source_type == "url":
        url = str(raw_data).strip()
        return parse_url(url)

    if source_type == "file":
        if not isinstance(raw_data, bytes):
            raw_data = str(raw_data).encode("utf-8")
        
        name = (filename_or_url or "uploaded_file").lower()
        if name.endswith(".pdf"):
            return filename_or_url or "PDF Document", parse_pdf(raw_data)
        elif name.endswith(".docx"):
            return filename_or_url or "Word Document", parse_docx(raw_data)
        else:
            # 기본 .txt 및 일반 텍스트 파일
            return filename_or_url or "Text Document", raw_data.decode("utf-8", errors="replace")

    # 기본 source_type == "text"
    content = str(raw_data).strip()
    title = (filename_or_url or content[:30]).replace("\n", " ").strip()
    return title, content
