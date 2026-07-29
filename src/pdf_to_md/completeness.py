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
    "严禁",
    "应当",
    "请勿",
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
    "shall",
    "do not",
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
OCR_MARKER_PATTERN = re.compile(
    r"<!--\s*extraction-method:\s*ocr\s*-->"
)
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)
GLOBAL_TEXT_THRESHOLD = 0.85
PAGE_TEXT_THRESHOLD = 0.80
FACT_THRESHOLD = 0.98


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _ngrams(text: str, size: int = 4) -> set[str]:
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _coverage(source: str, target: str) -> float:
    source_ngrams = _ngrams(_normalize(source))
    if not source_ngrams:
        return 0.0
    return len(source_ngrams & _ngrams(_normalize(target))) / len(source_ngrams)


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _normalize(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(value.strip())
    return result


def _facts(text: str) -> list[str]:
    return _unique_preserving_order(FACT_PATTERN.findall(text))


def _key_lines(text: str) -> list[str]:
    return _unique_preserving_order(
        [
            line
            for line in text.splitlines()
            if len(_normalize(line)) >= 4
            and any(signal in line.casefold() for signal in KEY_SIGNALS)
        ]
    )


def _split_markdown_pages(markdown: str) -> dict[int, str]:
    matches = list(SOURCE_MARKER_PATTERN.finditer(markdown))
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        page_number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        pages[page_number] = markdown[match.end() : end]
    return pages


def _without_internal_comments(markdown: str) -> str:
    return HTML_COMMENT_PATTERN.sub("", markdown).strip()


def _page_result(
    page_number: int,
    source_text: str,
    markdown_text: str | None,
    *,
    verification_basis: str,
) -> dict[str, Any]:
    source_normalized = _normalize(source_text)
    if not source_normalized:
        return {
            "page": page_number,
            "status": "unverifiable",
            "text_coverage": None,
            "critical_facts_total": 0,
            "critical_facts_found": 0,
            "key_statements_total": 0,
            "key_statements_found": 0,
            "verification_basis": verification_basis,
            "issues": ["该页没有可提取原文，可能是空白页或扫描页"],
        }
    if markdown_text is None:
        return {
            "page": page_number,
            "status": "missing",
            "text_coverage": 0.0,
            "critical_facts_total": len(_facts(source_text)),
            "critical_facts_found": 0,
            "key_statements_total": len(_key_lines(source_text)),
            "key_statements_found": 0,
            "verification_basis": verification_basis,
            "issues": ["Markdown 缺少该页"],
        }

    markdown_normalized = _normalize(markdown_text)
    facts = _facts(source_text)
    key_lines = _key_lines(source_text)
    found_facts = [fact for fact in facts if _normalize(fact) in markdown_normalized]
    found_key_lines = [
        line for line in key_lines if _normalize(line) in markdown_normalized
    ]
    text_coverage = _coverage(source_text, markdown_text)
    fact_coverage = len(found_facts) / len(facts) if facts else 1.0
    key_line_coverage = (
        len(found_key_lines) / len(key_lines) if key_lines else 1.0
    )

    issues: list[str] = []
    if text_coverage < PAGE_TEXT_THRESHOLD:
        issues.append(f"正文覆盖率 {text_coverage:.0%}，低于 80%")
    if fact_coverage < FACT_THRESHOLD:
        issues.append("部分数字、日期、型号或编号未找到")
    if key_line_coverage < 1.0:
        issues.append("部分关键要求、注意事项或步骤未找到")

    return {
        "page": page_number,
        "status": "complete" if not issues else "incomplete",
        "text_coverage": round(text_coverage, 4),
        "critical_facts_total": len(facts),
        "critical_facts_found": len(found_facts),
        "key_statements_total": len(key_lines),
        "key_statements_found": len(found_key_lines),
        "verification_basis": verification_basis,
        "missing_critical_facts": [
            fact for fact in facts if fact not in found_facts
        ][:10],
        "issues": issues,
    }


def evaluate_completeness(
    source: Path,
    markdown: str,
    *,
    reference_markdown: str | None = None,
) -> dict[str, Any]:
    reader = PdfReader(str(source))
    native_page_texts = [(page.extract_text() or "") for page in reader.pages]
    reference_pages = (
        _split_markdown_pages(reference_markdown)
        if reference_markdown
        else {}
    )
    page_texts: list[str] = []
    verification_bases: list[str] = []
    ocr_reference_pages: list[int] = []
    for page_number, native_text in enumerate(native_page_texts, start=1):
        if _normalize(native_text):
            page_texts.append(native_text)
            verification_bases.append("pdf_text_layer")
            continue
        reference_page = reference_pages.get(page_number, "")
        if OCR_MARKER_PATTERN.search(reference_page):
            ocr_text = _without_internal_comments(reference_page)
            if _normalize(ocr_text):
                page_texts.append(ocr_text)
                verification_bases.append("ocr_extraction")
                ocr_reference_pages.append(page_number)
                continue
        page_texts.append("")
        verification_bases.append("unavailable")

    source_text = "\n".join(page_texts)
    source_normalized = _normalize(source_text)
    markdown_normalized = _normalize(markdown)
    markdown_pages = _split_markdown_pages(markdown)

    if markdown_pages:
        page_results = [
            _page_result(
                page_number,
                page_text,
                markdown_pages.get(page_number),
                verification_basis=verification_bases[page_number - 1],
            )
            for page_number, page_text in enumerate(page_texts, start=1)
        ]
    else:
        page_results = [
            {
                "page": page_number,
                "status": "unverifiable",
                "text_coverage": None,
                "critical_facts_total": len(_facts(page_text)),
                "critical_facts_found": 0,
                "key_statements_total": len(_key_lines(page_text)),
                "key_statements_found": 0,
                "verification_basis": verification_bases[page_number - 1],
                "issues": ["转换结果没有源页标记，无法逐页检查"],
            }
            for page_number, page_text in enumerate(page_texts, start=1)
        ]
    problem_pages = [
        result["page"]
        for result in page_results
        if result["status"] in {"missing", "incomplete"}
    ]
    unverifiable_pages = [
        result["page"]
        for result in page_results
        if result["status"] == "unverifiable"
    ]

    expected_pages = set(range(1, len(page_texts) + 1))
    mapped_pages = set(markdown_pages)
    page_mapping_complete: bool | None = (
        mapped_pages == expected_pages if mapped_pages else None
    )

    facts = _facts(source_text)
    found_facts = [
        fact for fact in facts if _normalize(fact) in markdown_normalized
    ]
    key_lines = _key_lines(source_text)
    found_key_lines = [
        line for line in key_lines if _normalize(line) in markdown_normalized
    ]
    text_coverage = _coverage(source_text, markdown)
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
            and not problem_pages
            and not unverifiable_pages
            and text_coverage >= GLOBAL_TEXT_THRESHOLD
            and fact_coverage >= FACT_THRESHOLD
            and key_line_coverage >= 1.0
        )
        status = "complete" if passed else (
            "limited" if unverifiable_pages and not problem_pages else "incomplete"
        )
        if not markdown_normalized:
            warnings.append("Markdown 没有可用正文")
        if page_mapping_complete is False:
            warnings.append("Markdown 的源页标记不完整")
        if problem_pages:
            warnings.append(
                "以下页面可能存在内容遗漏："
                + "、".join(str(page) for page in problem_pages)
            )
        if unverifiable_pages:
            warnings.append(
                "以下页面没有可提取原文，无法自动验证："
                + "、".join(str(page) for page in unverifiable_pages)
            )
        if text_coverage < GLOBAL_TEXT_THRESHOLD:
            warnings.append("整份文档正文覆盖率低于 85%")
        if fact_coverage < FACT_THRESHOLD:
            warnings.append("部分数字、日期、编号或参数未在 Markdown 中找到")
        if key_line_coverage < 1.0:
            warnings.append("部分关键要求、注意事项或步骤未在 Markdown 中找到")

    checks = {
        "source_pages": len(page_texts),
        "mapped_pages": len(mapped_pages),
        "page_mapping_complete": page_mapping_complete,
        "problem_pages": problem_pages,
        "unverifiable_pages": unverifiable_pages,
        "source_text_characters": len(source_normalized),
        "markdown_text_characters": len(markdown_normalized),
        "text_coverage": round(text_coverage, 4),
        "critical_facts_total": len(facts),
        "critical_facts_found": len(found_facts),
        "critical_fact_coverage": round(fact_coverage, 4),
        "key_statements_total": len(key_lines),
        "key_statements_found": len(found_key_lines),
        "key_statement_coverage": round(key_line_coverage, 4),
        "pdf_text_layer_pages": [
            page
            for page, basis in enumerate(verification_bases, start=1)
            if basis == "pdf_text_layer"
        ],
        "ocr_reference_pages": ocr_reference_pages,
        "similarity_basis": (
            "PDF 文字层与 OCR 提取全文"
            if ocr_reference_pages
            else "PDF 文字层"
        ),
    }

    if status == "complete":
        ocr_note = (
            f"，其中 {len(ocr_reference_pages)} 页使用 OCR"
            if ocr_reference_pages
            else ""
        )
        summary = (
            f"自动检查通过：{len(page_texts)}/{len(page_texts)} 页正常"
            f"{ocr_note}，正文覆盖率 {text_coverage:.0%}，"
            f"关键数字/编号保留 {len(found_facts)}/{len(facts)}。"
        )
    elif status == "limited":
        summary = (
            f"转换已完成，但有 {len(unverifiable_pages) or len(page_texts)} 页"
            "无法自动验证，可能需要 OCR。"
        )
    else:
        summary = (
            f"转换已完成，但第 "
            f"{'、'.join(str(page) for page in problem_pages) or '未知'} 页"
            "可能存在遗漏。"
        )

    return {
        "status": status,
        "passed": passed,
        "summary": summary,
        "checks": checks,
        "pages": page_results,
        "warnings": warnings,
    }
