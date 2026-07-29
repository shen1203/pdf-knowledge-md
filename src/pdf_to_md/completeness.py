from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader


KEY_SIGNALS = (
    "必须",
    "禁止",
    "不得",
    "注意",
    "要求",
    "条件",
    "步骤",
    "期限",
    "金额",
    "电话",
    "地址",
    "时间",
    "日期",
    "版本",
    "参数",
    "must",
    "required",
    "warning",
    "prohibited",
    "step",
    "deadline",
)
FACT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:[A-Za-z]*\d[A-Za-z0-9._/:%％-]*|"
    r"\d+(?:[.,:/-]\d+)*(?:%|％|[A-Za-z]+|[\u4e00-\u9fff]{1,4})?)"
    r"(?![A-Za-z0-9])"
)
SOURCE_MARKER_PATTERN = re.compile(r"<!--\s*source-page:\s*(\d+)\s*-->")


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _ngrams(text: str, size: int = 4) -> set[str]:
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _normalize(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(value.strip())
    return result


def evaluate_completeness(source: Path, markdown: str) -> dict[str, Any]:
    reader = PdfReader(str(source))
    page_texts = [(page.extract_text() or "") for page in reader.pages]
    source_text = "\n".join(page_texts)
    source_normalized = _normalize(source_text)
    markdown_normalized = _normalize(markdown)

    mapped_pages = {
        int(match.group(1)) for match in SOURCE_MARKER_PATTERN.finditer(markdown)
    }
    page_mapping_complete: bool | None = None
    if mapped_pages:
        page_mapping_complete = mapped_pages == set(range(1, len(page_texts) + 1))

    facts = _unique_preserving_order(FACT_PATTERN.findall(source_text))
    found_facts = [
        fact for fact in facts if _normalize(fact) in markdown_normalized
    ]

    key_lines = _unique_preserving_order(
        [
            line
            for line in source_text.splitlines()
            if len(_normalize(line)) >= 4
            and any(signal in line.casefold() for signal in KEY_SIGNALS)
        ]
    )
    found_key_lines = [
        line for line in key_lines if _normalize(line) in markdown_normalized
    ]

    source_ngrams = _ngrams(source_normalized)
    markdown_ngrams = _ngrams(markdown_normalized)
    text_coverage = (
        len(source_ngrams & markdown_ngrams) / len(source_ngrams)
        if source_ngrams
        else 0.0
    )
    fact_coverage = len(found_facts) / len(facts) if facts else 1.0
    key_line_coverage = (
        len(found_key_lines) / len(key_lines) if key_lines else 1.0
    )

    warnings: list[str] = []
    if not source_normalized:
        status = "limited"
        passed = False
        warnings.append(
            "PDF 没有可直接提取的文本，自动检查无法验证关键信息；"
            "扫描件需要 OCR 引擎"
        )
    else:
        passed = (
            bool(markdown_normalized)
            and page_mapping_complete is not False
            and text_coverage >= 0.85
            and fact_coverage >= 0.98
            and key_line_coverage >= 1.0
        )
        status = "complete" if passed else "incomplete"
        if not markdown_normalized:
            warnings.append("Markdown 没有可用正文")
        if page_mapping_complete is False:
            warnings.append("部分 PDF 页面没有对应的 Markdown 页码标记")
        if text_coverage < 0.85:
            warnings.append("正文覆盖率低于 85%，可能存在内容遗漏")
        if fact_coverage < 0.98:
            warnings.append("部分数字、日期、编号或参数未在 Markdown 中找到")
        if key_line_coverage < 1.0:
            warnings.append("部分关键要求、注意事项或步骤未在 Markdown 中找到")

    checks = {
        "source_pages": len(page_texts),
        "mapped_pages": len(mapped_pages),
        "page_mapping_complete": page_mapping_complete,
        "source_text_characters": len(source_normalized),
        "markdown_text_characters": len(markdown_normalized),
        "text_coverage": round(text_coverage, 4),
        "critical_facts_total": len(facts),
        "critical_facts_found": len(found_facts),
        "critical_fact_coverage": round(fact_coverage, 4),
        "key_statements_total": len(key_lines),
        "key_statements_found": len(found_key_lines),
        "key_statement_coverage": round(key_line_coverage, 4),
    }

    if status == "complete":
        summary = (
            f"自动检查通过：共 {len(page_texts)} 页，正文覆盖率 "
            f"{text_coverage:.0%}，关键数字/编号保留 "
            f"{len(found_facts)}/{len(facts)}。"
        )
    elif status == "limited":
        summary = "转换已完成，但原 PDF 无可提取文本，无法自动验证关键信息。"
    else:
        summary = (
            f"转换已完成，但自动检查发现可能遗漏：正文覆盖率 "
            f"{text_coverage:.0%}，关键数字/编号保留 "
            f"{len(found_facts)}/{len(facts)}。"
        )

    return {
        "status": status,
        "passed": passed,
        "summary": summary,
        "checks": checks,
        "warnings": warnings,
    }
