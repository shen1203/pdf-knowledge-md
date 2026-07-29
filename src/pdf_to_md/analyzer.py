from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import PdfProfile


def _clean_metadata(metadata: Any) -> dict[str, str]:
    if not metadata:
        return {}
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in dict(metadata).items():
        if raw_value is None:
            continue
        key = str(raw_key).lstrip("/")
        value = str(raw_value).strip()
        if value:
            cleaned[key] = value
    return cleaned


def _count_visible_chars(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def analyze_pdf(
    source: Path,
    *,
    min_text_chars_per_page: int = 30,
    scanned_page_ratio: float = 0.8,
    mixed_page_ratio: float = 0.2,
) -> PdfProfile:
    """Inspect text density without rendering or mutating the document."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"PDF does not exist: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError(f"Only PDF input is supported: {source}")

    reader = PdfReader(str(source))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover - depends on encryption type
            raise ValueError(f"Encrypted PDF cannot be opened: {source}") from exc

    warnings: list[str] = []
    page_text_chars: list[int] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = ""
            warnings.append(f"第 {page_number} 页：文本提取失败（{exc}）")
        page_text_chars.append(_count_visible_chars(text))

    page_count = len(page_text_chars)
    low_text_page_numbers = [
        page_number
        for page_number, char_count in enumerate(page_text_chars, start=1)
        if char_count < min_text_chars_per_page
    ]
    low_text_pages = len(low_text_page_numbers)
    low_text_page_ratio = low_text_pages / page_count if page_count else 1.0

    if low_text_page_ratio >= scanned_page_ratio:
        document_type = "scanned"
    elif low_text_page_ratio >= mixed_page_ratio:
        document_type = "mixed"
    else:
        document_type = "native_text"

    if page_count == 0:
        warnings.append("PDF 不包含任何页面")

    return PdfProfile(
        source=source,
        page_count=page_count,
        page_text_chars=page_text_chars,
        low_text_page_numbers=low_text_page_numbers,
        text_chars=sum(page_text_chars),
        low_text_pages=low_text_pages,
        low_text_page_ratio=round(low_text_page_ratio, 4),
        document_type=document_type,
        metadata=_clean_metadata(reader.metadata),
        warnings=warnings,
    )
