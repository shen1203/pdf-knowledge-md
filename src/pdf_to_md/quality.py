from __future__ import annotations

from .models import EngineOutput, PdfProfile, QualityReport


def evaluate_quality(
    profile: PdfProfile,
    output: EngineOutput,
    *,
    max_replacement_char_ratio: float = 0.005,
) -> QualityReport:
    markdown = output.markdown
    visible_chars = sum(1 for char in markdown if not char.isspace())
    replacement_chars = markdown.count("\ufffd")
    replacement_ratio = (
        replacement_chars / visible_chars if visible_chars else 1.0
    )
    source_page_markers = markdown.count("<!-- source-page:")
    page_mapping_complete = (
        source_page_markers == profile.page_count if output.page_markers else None
    )

    warnings = list(profile.warnings) + list(output.warnings)
    score = 100
    status = "passed"

    if profile.page_count == 0 or visible_chars == 0:
        status = "failed"
        score = 0
        warnings.append("没有生成可用内容")
    else:
        if output.engine == "pypdf" and profile.document_type != "native_text":
            status = "review_required"
            score -= 35
            warnings.append(
                "文档疑似扫描件或混合型 PDF，但当前使用了基础文本引擎；"
                "发布前应安装并使用 OCR/版面解析引擎"
            )
        if replacement_ratio > max_replacement_char_ratio:
            status = "review_required"
            score -= 30
            warnings.append(
                "替换字符比例超过配置阈值"
            )
        if page_mapping_complete is False:
            status = "failed"
            score = min(score, 30)
            warnings.append("显式源页码映射不完整")
        if profile.low_text_pages:
            score -= min(20, profile.low_text_pages * 2)

    checks = {
        "source_page_count": profile.page_count,
        "output_visible_chars": visible_chars,
        "source_page_markers": source_page_markers,
        "page_mapping_complete": page_mapping_complete,
        "low_text_pages": profile.low_text_pages,
        "low_text_page_ratio": profile.low_text_page_ratio,
        "replacement_char_ratio": round(replacement_ratio, 6),
    }
    return QualityReport(
        status=status,
        score=max(0, score),
        checks=checks,
        warnings=warnings,
    )
