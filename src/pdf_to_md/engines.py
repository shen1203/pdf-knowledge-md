from __future__ import annotations

import importlib.util
import math
import re
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

from .models import EngineOutput, PdfProfile


class EngineUnavailableError(RuntimeError):
    pass


class ConversionEngine(Protocol):
    name: str

    def convert(self, source: Path, profile: PdfProfile) -> EngineOutput: ...


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"


def engine_available(name: str) -> bool:
    modules = {
        "pypdf": "pypdf",
        "docling": "docling",
        "paddleocr": "paddleocr",
    }
    module = modules.get(name)
    return bool(module and importlib.util.find_spec(module))


def engine_status() -> dict[str, dict[str, str | bool]]:
    packages = {
        "pypdf": "pypdf",
        "docling": "docling",
        "paddleocr": "paddleocr",
    }
    return {
        name: {
            "available": engine_available(name),
            "version": _package_version(package)
            if engine_available(name)
            else "not installed",
        }
        for name, package in packages.items()
    }


def _visible_chars(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def _line_key(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _find_repeated_marginal_lines(pages: list[list[str]]) -> set[str]:
    if len(pages) < 2:
        return set()
    counts: Counter[str] = Counter()
    for lines in pages:
        nonempty = [_line_key(line) for line in lines if _line_key(line)]
        candidates = set(nonempty[:2] + nonempty[-2:])
        for candidate in candidates:
            if 1 < len(candidate) <= 100:
                counts[candidate] += 1
    threshold = max(2, math.ceil(len(pages) * 0.6))
    return {line for line, count in counts.items() if count >= threshold}


_NUMBERED_HEADING = re.compile(
    r"^(?P<number>\d+(?:\.\d+){0,4})[.)、]?\s+(?P<title>\S.+)$"
)
_PAGE_NUMBER_LINE = re.compile(
    r"^(?:page\s*)?\d+(?:\s*(?:/|of)\s*\d+)?$|^第?\s*\d+\s*页$",
    re.IGNORECASE,
)


def _as_markdown_line(line: str) -> str:
    line = line.strip()
    if not line:
        return ""
    if line.startswith(("• ", "· ", "● ", "▪ ", "◦ ")):
        return f"- {line[2:].strip()}"
    match = _NUMBERED_HEADING.match(line)
    if match and len(line) <= 120:
        depth = min(6, 2 + match.group("number").count("."))
        return f"{'#' * depth} {line}"
    if (
        len(line) <= 80
        and any(char.isalpha() for char in line)
        and line.upper() == line
        and not line.endswith((".", "。", "!", "！", "?", "？"))
    ):
        return f"## {line.title()}"
    return line


class PypdfEngine:
    """Dependency-light baseline for PDFs that already contain extractable text."""

    name = "pypdf"

    def convert(self, source: Path, profile: PdfProfile) -> EngineOutput:
        reader = PdfReader(str(source))
        raw_pages: list[list[str]] = []
        warnings: list[str] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                text = ""
                warnings.append(
                    f"第 {page_number} 页：pypdf 文本提取失败（{exc}）"
                )
            raw_pages.append(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))

        repeated_lines = _find_repeated_marginal_lines(raw_pages)
        title = profile.metadata.get("Title") or source.stem
        parts = [f"# {title.strip()}", ""]
        removed_page_numbers = 0

        for page_number, lines in enumerate(raw_pages, start=1):
            parts.extend([f"<!-- source-page: {page_number} -->", ""])
            page_lines: list[str] = []
            nonempty_indexes = [
                index for index, line in enumerate(lines) if _line_key(line)
            ]
            marginal_indexes = set(nonempty_indexes[:2] + nonempty_indexes[-2:])
            for index, line in enumerate(lines):
                key = _line_key(line)
                if key and key in repeated_lines:
                    continue
                if (
                    key
                    and index in marginal_indexes
                    and _PAGE_NUMBER_LINE.fullmatch(key)
                ):
                    removed_page_numbers += 1
                    continue
                markdown_line = _as_markdown_line(line)
                if markdown_line.startswith("#"):
                    if page_lines and page_lines[-1]:
                        page_lines.append("")
                    page_lines.extend([markdown_line, ""])
                    continue
                if markdown_line or (page_lines and page_lines[-1]):
                    page_lines.append(markdown_line)
            while page_lines and not page_lines[-1]:
                page_lines.pop()
            if page_lines:
                parts.extend(page_lines)
            else:
                parts.append("<!-- no extractable text on this page -->")
                warnings.append(f"第 {page_number} 页：没有可提取文本")
            parts.append("")

        markdown = "\n".join(parts).strip() + "\n"
        if repeated_lines:
            warnings.append(
                f"已移除 {len(repeated_lines)} 条重复页眉或页脚"
            )
        if removed_page_numbers:
            warnings.append(f"已移除 {removed_page_numbers} 个页面边缘页码")
        return EngineOutput(
            markdown=markdown,
            engine=self.name,
            engine_version=_package_version("pypdf"),
            warnings=warnings,
            page_markers=True,
        )


class DoclingEngine:
    name = "docling"

    def convert(self, source: Path, profile: PdfProfile) -> EngineOutput:
        if not engine_available(self.name):
            raise EngineUnavailableError(
                "Docling is not installed. Install with: pip install -e .[docling]"
            )
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(source))
        markdown = result.document.export_to_markdown().strip() + "\n"
        return EngineOutput(
            markdown=markdown,
            engine=self.name,
            engine_version=_package_version("docling"),
            warnings=[
                "当前 Docling 输出尚未包含显式源页码标记"
            ],
            page_markers=False,
        )


class PaddleOcrEngine:
    name = "paddleocr"

    def convert(self, source: Path, profile: PdfProfile) -> EngineOutput:
        if not engine_available(self.name):
            raise EngineUnavailableError(
                "PaddleOCR is not installed. Install the platform-appropriate "
                "PaddlePaddle build and then: pip install -e .[ocr]"
            )
        from paddleocr import PPStructureV3

        pipeline = PPStructureV3()
        results = list(pipeline.predict(str(source)))
        markdown_pages = [result.markdown for result in results]
        merged = pipeline.concatenate_markdown_pages(markdown_pages)
        if isinstance(merged, tuple):
            merged = merged[0]
        if isinstance(merged, dict):
            merged = merged.get("markdown_texts", "")
        markdown = str(merged).strip() + "\n"
        return EngineOutput(
            markdown=markdown,
            engine=self.name,
            engine_version=_package_version("paddleocr"),
            warnings=[
                "PaddleOCR 首次使用时可能下载模型权重",
                "MVP 0.2 尚未持久化 PaddleOCR 提取的 Markdown 图片",
            ],
            page_markers=False,
        )


def select_engine(requested: str, profile: PdfProfile) -> ConversionEngine:
    requested = requested.lower()
    if requested == "auto":
        if profile.document_type in {"scanned", "mixed"} and engine_available(
            "paddleocr"
        ):
            return PaddleOcrEngine()
        if engine_available("docling"):
            return DoclingEngine()
        return PypdfEngine()
    if requested == "pypdf":
        return PypdfEngine()
    if requested == "docling":
        return DoclingEngine()
    if requested == "paddleocr":
        return PaddleOcrEngine()
    raise ValueError(f"Unknown engine: {requested}")
