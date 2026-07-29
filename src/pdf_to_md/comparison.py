from __future__ import annotations

from pathlib import Path
from typing import Any

from .completeness import evaluate_completeness


MODE_LABELS = {
    "full": "完整转换",
    "summary": "重点摘要",
}


def build_comparison_report(
    source: Path,
    markdown: str,
    *,
    mode: str,
    reference_markdown: str | None = None,
    conversion_warnings: list[str] | None = None,
    summary_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    coverage = evaluate_completeness(
        source,
        markdown,
        reference_markdown=reference_markdown,
    )
    checks = coverage["checks"]
    similarity = float(checks["text_coverage"])
    ocr_pages = list(checks.get("ocr_reference_pages", []))
    warnings = list(coverage["warnings"])
    changes: list[str] = []

    if mode == "summary":
        metadata = summary_metadata or {}
        selected = int(metadata.get("selected_lines", 0))
        source_lines = int(metadata.get("source_lines", 0))
        omitted = int(metadata.get("omitted_lines", 0))
        passed = bool(markdown.strip()) and selected > 0
        status = "summary_complete" if passed else "incomplete"
        summary = (
            f"重点摘要已生成：从 {source_lines} 行中抽取 {selected} 行重点，"
            f"与原 PDF 的文本保留相似度为 {similarity:.0%}。"
        )
        changes.extend(
            [
                f"抽取 {selected} 行重点内容，主动省略 {omitted} 行非重点内容",
                "保留标题、步骤、关键要求以及带数字、日期、型号或编号的句子",
                "按原 PDF 页码重新组织为 Markdown 重点列表",
                "仅抽取原文，不新增或改写业务事实",
                (
                    f"保留关键数字/编号 "
                    f"{checks['critical_facts_found']}/"
                    f"{checks['critical_facts_total']}"
                ),
            ]
        )
        if ocr_pages:
            changes.append(
                "扫描页先经 OCR 提取全文，再从 OCR 结果中抽取重点"
            )
        warnings = []
        if checks["unverifiable_pages"]:
            warnings.append(
                "以下页面没有可提取原文，无法计算可靠相似度："
                + "、".join(
                    str(page) for page in checks["unverifiable_pages"]
                )
            )
        if checks["critical_facts_found"] < checks["critical_facts_total"]:
            warnings.append("部分关键数字或编号没有进入摘要，请查看改动说明")
        if not passed:
            warnings.append("未能抽取到可用的重点内容")
    else:
        passed = bool(coverage["passed"])
        status = str(coverage["status"])
        summary = (
            f"完整转换已完成：与原 PDF 的文本保留相似度为 "
            f"{similarity:.0%}。"
        )
        changes.extend(
            [
                "未主动摘要或删减可提取正文",
                "将识别出的标题、列表和段落转换为 Markdown 结构",
                "为各页增加源页码注释，便于回查 PDF",
            ]
        )
        if ocr_pages:
            changes.append(
                "仅对无文字层页面使用 OCR，其他页面继续读取 PDF 文字层"
            )

    for warning in conversion_warnings or []:
        if warning not in changes:
            changes.append(warning)

    return {
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "status": status,
        "passed": passed,
        "similarity": round(similarity, 4),
        "similarity_percent": round(similarity * 100),
        "similarity_method": (
            f"{checks['similarity_basis']}的四字符片段在结果中的覆盖率；"
            "这是文本保留指标，不是语义正确率"
            + (
                "，也不是 OCR 图片识别准确率"
                if ocr_pages
                else ""
            )
        ),
        "summary": summary,
        "changes": changes,
        "checks": checks,
        "pages": coverage["pages"],
        "warnings": warnings,
        "summary_metadata": summary_metadata,
    }
