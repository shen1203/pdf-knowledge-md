from __future__ import annotations

import re
import unicodedata
from typing import Any

from .completeness import FACT_PATTERN, KEY_SIGNALS, SOURCE_MARKER_PATTERN


LIST_PATTERN = re.compile(r"^(?:[-*+]|\d+[.)、]|[（(]?\d+[）)])\s*")


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _split_pages(markdown: str) -> tuple[str, list[tuple[int, str]]]:
    matches = list(SOURCE_MARKER_PATTERN.finditer(markdown))
    prefix = markdown[: matches[0].start()] if matches else ""
    pages: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        pages.append(
            (
                int(match.group(1)),
                markdown[match.end() : end],
            )
        )
    if not pages:
        pages.append((1, markdown))
    return prefix, pages


def _document_title(prefix: str, fallback: str) -> str:
    for line in prefix.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _selection_reason(line: str, substantive_index: int) -> str | None:
    stripped = line.strip()
    lowered = stripped.casefold()
    if stripped.startswith("#"):
        return "heading"
    if any(signal in lowered for signal in KEY_SIGNALS):
        return "key_statement"
    if FACT_PATTERN.search(stripped):
        return "critical_fact"
    if LIST_PATTERN.match(stripped):
        return "step_or_list"
    if "|" in stripped and stripped.count("|") >= 2:
        return "table"
    if substantive_index < 2:
        return "page_lead"
    return None


def create_extract_summary(
    markdown: str,
    *,
    fallback_title: str,
) -> tuple[str, dict[str, Any]]:
    prefix, pages = _split_pages(markdown)
    title = _document_title(prefix, fallback_title)
    output = [
        f"# {title} — 重点摘要",
        "",
        "> 本摘要从原文中抽取重点句，不新增或改写业务事实。",
        "",
    ]
    total_lines = 0
    selected_lines = 0
    reason_counts: dict[str, int] = {}
    page_stats: list[dict[str, Any]] = []

    for page_number, page_markdown in pages:
        candidates = [
            line.strip()
            for line in page_markdown.splitlines()
            if line.strip() and not line.lstrip().startswith("<!--")
        ]
        total_lines += len(candidates)
        selected: list[tuple[str, str]] = []
        seen: set[str] = set()
        for index, line in enumerate(candidates):
            reason = _selection_reason(line, index)
            normalized = _normalize(line)
            if not reason or not normalized or normalized in seen:
                continue
            seen.add(normalized)
            selected.append((line, reason))
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        selected_lines += len(selected)
        page_stats.append(
            {
                "page": page_number,
                "source_lines": len(candidates),
                "selected_lines": len(selected),
                "omitted_lines": len(candidates) - len(selected),
            }
        )
        output.extend(
            [
                f"<!-- source-page: {page_number} -->",
                "",
                f"## 第 {page_number} 页重点",
                "",
            ]
        )
        if selected:
            for line, _ in selected:
                clean_line = re.sub(r"^#{1,6}\s*", "", line).strip()
                output.append(f"- {clean_line}")
        else:
            output.append("- 本页未识别到可抽取的重点文字。")
        output.append("")

    summary = "\n".join(output).strip() + "\n"
    metadata = {
        "method": "extractive_rules_v1",
        "source_lines": total_lines,
        "selected_lines": selected_lines,
        "omitted_lines": total_lines - selected_lines,
        "selection_reasons": reason_counts,
        "pages": page_stats,
    }
    return summary, metadata
