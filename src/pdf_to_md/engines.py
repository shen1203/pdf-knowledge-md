from __future__ import annotations

import hashlib
import importlib.util
import math
import os
import re
from collections import Counter
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import fmean
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Protocol

from pypdf import PdfReader, PdfWriter

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


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def engine_available(name: str) -> bool:
    if name == "paddleocr":
        return bool(
            importlib.util.find_spec("paddleocr")
            and importlib.util.find_spec("paddle")
        )
    modules = {
        "pypdf": "pypdf",
        "docling": "docling",
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
_SOURCE_PAGE_MARKER = re.compile(r"<!--\s*source-page:\s*(\d+)\s*-->")
OCR_PIPELINE_DEFAULTS: dict[str, bool] = {
    "use_doc_orientation_classify": True,
    "use_doc_unwarping": False,
    "use_textline_orientation": True,
    "use_seal_recognition": False,
    "use_table_recognition": False,
    "use_formula_recognition": False,
    "use_chart_recognition": False,
    "use_region_detection": False,
    "format_block_content": True,
    "enable_mkldnn": False,
}


def resolve_ocr_config_path(config_path: str | None) -> str | None:
    if not config_path:
        return None
    resolved_config = Path(config_path).expanduser().resolve()
    if not resolved_config.is_file():
        raise EngineUnavailableError(f"OCR 配置文件不存在：{resolved_config}")
    return str(resolved_config)


def engine_runtime_signature(name: str) -> dict[str, Any]:
    signature: dict[str, Any] = {"name": name}
    packages = {
        "pypdf": "pypdf",
        "docling": "docling",
        "paddleocr": "paddleocr",
    }
    package = packages.get(name)
    signature["version"] = _package_version(package) if package else "unknown"
    if name == "paddleocr":
        config_path = resolve_ocr_config_path(os.getenv("PDF_MD_OCR_CONFIG"))
        signature["ocr_config"] = {
            "path": config_path,
            "sha256": _sha256_path(Path(config_path)) if config_path else None,
        }
    return signature


class _SharedPaddleRuntime:
    def __init__(self, pipeline: object) -> None:
        self._pipeline = pipeline
        self._predict_lock = Lock()

    def predict(self, source: Path) -> list[object]:
        with self._predict_lock:
            return list(self._pipeline.predict(input=str(source)))


@lru_cache(maxsize=None)
def _cached_paddle_runtime(config_path: str | None) -> _SharedPaddleRuntime:
    from paddleocr import PPStructureV3

    pipeline_options: dict[str, Any]
    if config_path:
        pipeline_options = {"paddlex_config": config_path}
    else:
        pipeline_options = dict(OCR_PIPELINE_DEFAULTS)
    try:
        pipeline = PPStructureV3(**pipeline_options)
    except Exception as exc:
        raise RuntimeError(
            "PaddleOCR 初始化失败；请确认模型已下载，或通过 "
            "PDF_MD_OCR_CONFIG 指向离线模型配置"
        ) from exc
    return _SharedPaddleRuntime(pipeline)


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


def _split_markdown_pages(markdown: str) -> tuple[str, dict[int, str]]:
    matches = list(_SOURCE_PAGE_MARKER.finditer(markdown))
    prefix = markdown[: matches[0].start()] if matches else markdown
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        pages[int(match.group(1))] = markdown[match.end() : end].strip()
    return prefix.strip(), pages


def _ocr_markdown(result: object) -> str:
    payload = _ocr_result_json(result)
    overall = payload.get("overall_ocr_res")
    if isinstance(overall, dict):
        recognized_lines = overall.get("rec_texts")
        if isinstance(recognized_lines, list):
            formatted_lines = [
                _as_markdown_line(str(line))
                for line in recognized_lines
                if str(line).strip()
            ]
            if formatted_lines:
                return "\n\n".join(formatted_lines)
    blocks = payload.get("parsing_res_list")
    if isinstance(blocks, list):
        contents = [
            str(block.get("block_content", "")).strip()
            for block in blocks
            if isinstance(block, dict)
            and str(block.get("block_content", "")).strip()
        ]
        if contents:
            return "\n\n".join(contents)
    markdown = getattr(result, "markdown", "")
    if callable(markdown):
        markdown = markdown()
    if isinstance(markdown, dict):
        markdown = markdown.get("markdown_texts") or markdown.get("text") or ""
    if isinstance(markdown, (tuple, list)):
        markdown = markdown[0] if markdown else ""
    return str(markdown).strip()


def _ocr_result_json(result: object) -> dict[str, Any]:
    payload = getattr(result, "json", {})
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("res")
    return nested if isinstance(nested, dict) else payload


def _ocr_confidence(result: object) -> float | None:
    payload = _ocr_result_json(result)
    overall = payload.get("overall_ocr_res")
    if not isinstance(overall, dict):
        return None
    raw_scores = overall.get("rec_scores")
    if raw_scores is None:
        return None
    try:
        scores = [float(score) for score in raw_scores]
    except (TypeError, ValueError):
        return None
    return round(fmean(scores), 4) if scores else None


def _ocr_page_batch(
    source: Path,
    profile: PdfProfile,
    page_numbers: list[int] | set[int],
    *,
    retry_reason: str | None = None,
) -> tuple[
    dict[int, str],
    list[dict[str, Any]],
    list[str],
    list[int],
    list[int],
]:
    target_pages = sorted(
        {
            page
            for page in page_numbers
            if 1 <= page <= profile.page_count
        }
    )
    if not target_pages:
        return {}, [], [], [], []

    config_path = resolve_ocr_config_path(os.getenv("PDF_MD_OCR_CONFIG"))
    pipeline = _cached_paddle_runtime(config_path)
    reader = PdfReader(str(source))
    updates: dict[int, str] = {}
    page_processing: list[dict[str, Any]] = []
    warnings: list[str] = []
    completed_pages: list[int] = []
    failed_pages: list[int] = []
    method_name = "ocr_retry" if retry_reason else "ocr"
    action_text = "已重新 OCR" if retry_reason else "已使用 OCR"

    with TemporaryDirectory(prefix="pdf-md-ocr-") as temporary:
        temporary_root = Path(temporary)
        for page_number in target_pages:
            single_page_pdf = temporary_root / f"page-{page_number}.pdf"
            writer = PdfWriter()
            writer.add_page(reader.pages[page_number - 1])
            with single_page_pdf.open("wb") as handle:
                writer.write(handle)

            try:
                results = pipeline.predict(single_page_pdf)
                page_markdown = _ocr_markdown(results[0]) if results else ""
                confidence = _ocr_confidence(results[0]) if results else None
                if not page_markdown:
                    raise RuntimeError("OCR 没有返回可用文字")
                metadata_lines = ["<!-- extraction-method: ocr -->"]
                if retry_reason:
                    metadata_lines.append(
                        f"<!-- retry-reason: {retry_reason} -->"
                    )
                if confidence is not None:
                    metadata_lines.append(
                        f"<!-- ocr-confidence: {confidence:.4f} -->"
                    )
                updates[page_number] = (
                    "\n".join(metadata_lines) + "\n\n" + page_markdown
                )
                page_processing.append(
                    {
                        "page": page_number,
                        "method": method_name,
                        "status": "completed",
                        "characters": _visible_chars(page_markdown),
                        "confidence": confidence,
                    }
                )
                completed_pages.append(page_number)
                confidence_text = (
                    f"，平均识别置信度 {confidence:.0%}"
                    if confidence is not None
                    else ""
                )
                warnings.append(
                    f"第 {page_number} 页{action_text}{confidence_text}"
                )
            except Exception as exc:
                page_processing.append(
                    {
                        "page": page_number,
                        "method": method_name,
                        "status": "failed",
                        "characters": 0,
                        "confidence": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                failed_pages.append(page_number)
                warnings.append(
                    f"第 {page_number} 页{action_text}失败（{exc}）"
                )

    return updates, page_processing, warnings, completed_pages, failed_pages


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
    """Keep native text pages and OCR only pages with insufficient text."""

    name = "paddleocr"

    def convert(self, source: Path, profile: PdfProfile) -> EngineOutput:
        if not engine_available(self.name):
            raise EngineUnavailableError(
                "PaddleOCR is not installed. Install the platform-appropriate "
                "PaddlePaddle build and then: pip install -e .[ocr]"
            )
        baseline = PypdfEngine().convert(source, profile)
        prefix, pages = _split_markdown_pages(baseline.markdown)
        low_text_pages = set(profile.low_text_page_numbers)
        warnings = [
            warning
            for warning in baseline.warnings
            if "没有可提取文本" not in warning
        ]
        page_processing: list[dict[str, Any]] = []
        config_path = resolve_ocr_config_path(os.getenv("PDF_MD_OCR_CONFIG"))

        for page_number in range(1, profile.page_count + 1):
            if page_number not in low_text_pages:
                page_processing.append(
                    {
                        "page": page_number,
                        "method": "text_layer",
                        "status": "completed",
                        "characters": profile.page_text_chars[page_number - 1],
                        "confidence": None,
                    }
                )
                continue

        ocr_updates, ocr_processing, ocr_warnings, _, _ = (
            _ocr_page_batch(source, profile, low_text_pages)
        )
        warnings.extend(ocr_warnings)
        page_processing.extend(ocr_processing)
        for page_number, page_markdown in ocr_updates.items():
            pages[page_number] = page_markdown

        parts = [prefix, ""] if prefix else []
        for page_number in range(1, profile.page_count + 1):
            parts.extend(
                [
                    f"<!-- source-page: {page_number} -->",
                    "",
                    pages.get(
                        page_number,
                        "<!-- no extractable text on this page -->",
                    ),
                    "",
                ]
            )
        markdown = "\n".join(parts).strip() + "\n"
        completed_ocr_pages = [
            item["page"]
            for item in page_processing
            if item["method"].startswith("ocr") and item["status"] == "completed"
        ]
        failed_ocr_pages = [
            item["page"]
            for item in page_processing
            if item["method"].startswith("ocr") and item["status"] == "failed"
        ]
        return EngineOutput(
            markdown=markdown,
            engine=self.name,
            engine_version=_package_version("paddleocr"),
            warnings=warnings,
            page_markers=True,
            metadata={
                "strategy": "text_layer_with_page_ocr_fallback",
                "page_processing": page_processing,
                "ocr_completed_pages": completed_ocr_pages,
                "ocr_failed_pages": failed_ocr_pages,
                "ocr_config": config_path,
            },
        )


def retry_pages_with_paddleocr(
    source: Path,
    profile: PdfProfile,
    output: EngineOutput,
    retry_pages: list[int],
    *,
    retry_reason: str,
) -> EngineOutput:
    if not retry_pages or not output.page_markers:
        return output
    if not engine_available("paddleocr"):
        return output

    page_updates, page_processing, retry_warnings, completed_pages, failed_pages = (
        _ocr_page_batch(source, profile, retry_pages, retry_reason=retry_reason)
    )
    if not page_updates and not page_processing:
        return output

    prefix, pages = _split_markdown_pages(output.markdown)
    for page_number, page_markdown in page_updates.items():
        pages[page_number] = page_markdown

    parts = [prefix, ""] if prefix else []
    for page_number in range(1, profile.page_count + 1):
        parts.extend(
            [
                f"<!-- source-page: {page_number} -->",
                "",
                pages.get(
                    page_number,
                    "<!-- no extractable text on this page -->",
                ),
                "",
            ]
        )
    markdown = "\n".join(parts).strip() + "\n"

    metadata = dict(output.metadata)
    existing_processing = list(metadata.get("page_processing", []))
    existing_processing.extend(page_processing)
    metadata["page_processing"] = existing_processing

    completed_ocr_pages = set(metadata.get("ocr_completed_pages", []))
    failed_ocr_pages = set(metadata.get("ocr_failed_pages", []))
    completed_ocr_pages.update(completed_pages)
    failed_ocr_pages.difference_update(completed_pages)
    failed_ocr_pages.update(failed_pages)
    metadata["ocr_completed_pages"] = sorted(completed_ocr_pages)
    metadata["ocr_failed_pages"] = sorted(failed_ocr_pages)
    metadata["page_retry_pages"] = sorted({page for page in retry_pages})
    metadata["page_retry_completed_pages"] = completed_pages
    metadata["page_retry_failed_pages"] = failed_pages
    metadata["page_retry_reason"] = retry_reason

    warnings = list(dict.fromkeys(list(output.warnings) + retry_warnings))
    return EngineOutput(
        markdown=markdown,
        engine=output.engine,
        engine_version=output.engine_version,
        warnings=warnings,
        page_markers=output.page_markers,
        metadata=metadata,
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
