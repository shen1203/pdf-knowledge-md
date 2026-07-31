from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - optional test dependency
    canvas = None

from pdf_to_md.analyzer import analyze_pdf
from pdf_to_md.comparison import build_comparison_report
from pdf_to_md.completeness import evaluate_completeness
from pdf_to_md.engines import PaddleOcrEngine, PypdfEngine, retry_pages_with_paddleocr
from pdf_to_md.summarizer import create_extract_summary


class FakeStructurePipeline:
    init_count = 0
    predicted_files: list[str] = []

    def __init__(self, **_: object) -> None:
        type(self).init_count += 1

    def predict(self, *, input: str):
        self.predicted_files.append(input)
        return [
            SimpleNamespace(
                markdown={
                    "markdown_texts": (
                        "扫描页要求：必须在 15 分钟内处理，型号 OCR-2026。"
                    )
                },
                json={
                    "res": {
                        "overall_ocr_res": {
                            "rec_texts": [
                                "SCANNED SERVICE PAGE",
                                "Warning code: OCR-7788",
                                "Must respond within 15 minutes.",
                                "Device model: OCR-2026",
                            ],
                            "rec_scores": [0.96, 0.92, 0.94],
                        }
                    }
                },
            )
        ]


@unittest.skipIf(canvas is None, "reportlab is required for PDF integration tests")
class HybridOcrEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "mixed.pdf"
        document = canvas.Canvas(str(self.source), pagesize=letter)
        document.drawString(
            72,
            700,
            "Native text page contains enough characters for direct extraction.",
        )
        document.showPage()
        document.rect(72, 500, 300, 180, stroke=1, fill=0)
        document.showPage()
        document.save()
        FakeStructurePipeline.init_count = 0
        FakeStructurePipeline.predicted_files = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_only_low_text_page_uses_ocr_and_keeps_page_mapping(self) -> None:
        profile = analyze_pdf(self.source)
        self.assertEqual(profile.document_type, "mixed")
        self.assertEqual(profile.low_text_page_numbers, [2])

        fake_module = types.ModuleType("paddleocr")
        fake_module.PPStructureV3 = FakeStructurePipeline
        with (
            patch.dict(sys.modules, {"paddleocr": fake_module}),
            patch("pdf_to_md.engines.engine_available", return_value=True),
        ):
            output = PaddleOcrEngine().convert(self.source, profile)

        self.assertEqual(len(FakeStructurePipeline.predicted_files), 1)
        self.assertEqual(output.markdown.count("<!-- source-page:"), 2)
        self.assertEqual(output.markdown.count("extraction-method: ocr"), 1)
        self.assertIn("OCR-2026", output.markdown)
        self.assertIn(
            "Warning code: OCR-7788\n\nMust respond within 15 minutes.",
            output.markdown,
        )
        self.assertEqual(output.metadata["ocr_completed_pages"], [2])
        self.assertEqual(output.metadata["ocr_failed_pages"], [])
        self.assertEqual(
            output.metadata["page_processing"][1]["confidence"],
            0.94,
        )

        report = evaluate_completeness(
            self.source,
            output.markdown,
            reference_markdown=output.markdown,
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["checks"]["ocr_reference_pages"], [2])
        self.assertGreaterEqual(report["checks"]["text_coverage"], 0.95)

        summary, _ = create_extract_summary(
            output.markdown,
            fallback_title="mixed",
        )
        summary_report = evaluate_completeness(
            self.source,
            summary,
            reference_markdown=output.markdown,
        )
        self.assertNotIn(2, summary_report["checks"]["unverifiable_pages"])
        self.assertIn("extraction-method: ocr", summary)
        comparison = build_comparison_report(
            self.source,
            summary,
            mode="summary",
            reference_markdown=output.markdown,
            summary_metadata={
                "source_lines": 2,
                "selected_lines": 2,
                "omitted_lines": 0,
            },
        )
        self.assertTrue(comparison["passed"])
        self.assertEqual(comparison["checks"]["ocr_reference_pages"], [2])
        self.assertIn("不是 OCR 图片识别准确率", comparison["similarity_method"])

    def test_ocr_runtime_is_reused_across_conversions(self) -> None:
        profile = analyze_pdf(self.source)
        fake_module = types.ModuleType("paddleocr")
        fake_module.PPStructureV3 = FakeStructurePipeline
        with (
            patch.dict(sys.modules, {"paddleocr": fake_module}),
            patch("pdf_to_md.engines.engine_available", return_value=True),
        ):
            from pdf_to_md import engines

            engines._cached_paddle_runtime.cache_clear()
            PaddleOcrEngine().convert(self.source, profile)
            PaddleOcrEngine().convert(self.source, profile)

        self.assertEqual(FakeStructurePipeline.init_count, 1)
        self.assertEqual(len(FakeStructurePipeline.predicted_files), 2)

    def test_retry_pages_with_paddleocr_replaces_failed_page(self) -> None:
        profile = analyze_pdf(self.source)
        baseline = PypdfEngine().convert(self.source, profile)
        fake_module = types.ModuleType("paddleocr")
        fake_module.PPStructureV3 = FakeStructurePipeline
        with (
            patch.dict(sys.modules, {"paddleocr": fake_module}),
            patch("pdf_to_md.engines.engine_available", return_value=True),
        ):
            from pdf_to_md import engines

            engines._cached_paddle_runtime.cache_clear()
            retried = retry_pages_with_paddleocr(
                self.source,
                profile,
                baseline,
                [2],
                retry_reason="completeness_and_ocr_failure",
            )

        self.assertIn("retry-reason: completeness_and_ocr_failure", retried.markdown)
        self.assertIn("OCR-2026", retried.markdown)
        self.assertEqual(retried.metadata["page_retry_pages"], [2])
        self.assertEqual(retried.metadata["page_retry_completed_pages"], [2])
        self.assertEqual(retried.metadata["page_retry_failed_pages"], [])
        self.assertEqual(retried.metadata["ocr_completed_pages"], [2])
        self.assertEqual(retried.metadata["ocr_failed_pages"], [])


if __name__ == "__main__":
    unittest.main()
