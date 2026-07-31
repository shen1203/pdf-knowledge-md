from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - optional test dependency
    canvas = None

from pdf_to_md.pipeline import (
    ConversionPipeline,
    PipelineConfig,
    make_document_id,
    normalize_business_document_id,
)
from pdf_to_md.cli import _discover_pdf_sources
from pdf_to_md.engines import PypdfEngine


@unittest.skipIf(canvas is None, "reportlab is required for PDF integration tests")
class ConversionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "knowledge"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_text_pdf(self, path: Path) -> None:
        document = canvas.Canvas(str(path), pagesize=letter)
        document.setTitle("Sample Operations Manual")
        for page_number in (1, 2):
            document.drawString(72, 750, "COMPANY CONFIDENTIAL")
            document.drawString(72, 700, f"{page_number}. Procedure")
            document.drawString(
                72,
                675,
                f"Follow the verified procedure on page {page_number}.",
            )
            document.drawString(72, 40, f"Page {page_number}")
            document.showPage()
        document.save()

    def _make_image_only_like_pdf(self, path: Path) -> None:
        document = canvas.Canvas(str(path), pagesize=letter)
        for _ in (1, 2):
            document.rect(72, 500, 300, 180, stroke=1, fill=0)
            document.showPage()
        document.save()

    def test_text_pdf_is_published_and_unchanged_source_is_skipped(self) -> None:
        source = self.root / "manual.pdf"
        self._make_text_pdf(source)
        pipeline = ConversionPipeline(
            PipelineConfig(output_root=self.output, engine="pypdf")
        )

        first = pipeline.convert(source)
        self.assertEqual(first.status, "published")
        self.assertEqual(first.quality_status, "passed")
        self.assertTrue(first.output_path and first.output_path.is_file())
        self.assertTrue(first.manifest_path and first.manifest_path.is_file())

        markdown = first.output_path.read_text(encoding="utf-8")
        self.assertIn("# Sample Operations Manual", markdown)
        self.assertEqual(markdown.count("<!-- source-page:"), 2)
        self.assertNotIn("COMPANY CONFIDENTIAL", markdown)
        self.assertNotIn("Page 1", markdown)
        self.assertNotIn("Page 2", markdown)

        current_path = self.output / first.document_id / "current.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        self.assertEqual(current["version_id"], first.version_id)

        manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(manifest["published"])
        self.assertEqual(manifest["document"]["page_count"], 2)
        self.assertEqual(manifest["quality"]["status"], "passed")
        self.assertTrue((first.manifest_path.parent / "source.pdf").is_file())

        second = pipeline.convert(source)
        self.assertEqual(second.status, "skipped")
        self.assertEqual(second.version_id, first.version_id)

    def test_cache_is_invalidated_when_pipeline_scope_changes(self) -> None:
        source = self.root / "manual.pdf"
        self._make_text_pdf(source)
        first = ConversionPipeline(
            PipelineConfig(output_root=self.output, engine="pypdf")
        ).convert(source)
        self.assertEqual(first.status, "published")

        second = ConversionPipeline(
            PipelineConfig(
                output_root=self.output,
                engine="pypdf",
                max_replacement_char_ratio=0.001,
            )
        ).convert(source)
        self.assertEqual(second.status, "published")
        self.assertNotEqual(second.version_id, first.version_id)

    def test_cache_is_invalidated_when_engine_signature_changes(self) -> None:
        source = self.root / "manual.pdf"
        self._make_text_pdf(source)
        first = ConversionPipeline(
            PipelineConfig(output_root=self.output, engine="pypdf")
        ).convert(source)
        self.assertEqual(first.status, "published")

        class FakeEngine:
            name = "pypdf"

            def __init__(self) -> None:
                self.convert_calls = 0

            def convert(self, fake_source: Path, profile):
                self.convert_calls += 1
                return PypdfEngine().convert(fake_source, profile)

        fake_engine = FakeEngine()

        with (
            patch("pdf_to_md.pipeline.select_engine", return_value=fake_engine),
            patch(
                "pdf_to_md.pipeline.engine_runtime_signature",
                return_value={"name": "pypdf", "version": "changed-version"},
            ),
        ):
            second = ConversionPipeline(
                PipelineConfig(output_root=self.output, engine="pypdf")
            ).convert(source)

        self.assertEqual(second.status, "published")
        self.assertEqual(fake_engine.convert_calls, 1)
        self.assertNotEqual(second.version_id, first.version_id)

    def test_completeness_problems_trigger_page_retry(self) -> None:
        source = self.root / "manual.pdf"
        self._make_text_pdf(source)
        retry_calls: list[tuple[tuple[int, ...], str]] = []

        class FakeEngine:
            name = "pypdf"

            def convert(self, fake_source: Path, profile):
                return PypdfEngine().convert(fake_source, profile)

        def fake_retry(
            fake_source: Path,
            profile,
            output,
            retry_pages,
            *,
            retry_reason: str,
        ):
            retry_calls.append((tuple(retry_pages), retry_reason))
            return output

        fake_report = {
            "status": "incomplete",
            "passed": False,
            "checks": {
                "problem_pages": [1],
                "unverifiable_pages": [2],
            },
            "warnings": [],
        }

        with (
            patch("pdf_to_md.pipeline.select_engine", return_value=FakeEngine()),
            patch(
                "pdf_to_md.pipeline.evaluate_completeness",
                return_value=fake_report,
            ),
            patch(
                "pdf_to_md.pipeline.retry_pages_with_paddleocr",
                side_effect=fake_retry,
            ),
        ):
            outcome = ConversionPipeline(
                PipelineConfig(output_root=self.output, engine="pypdf")
            ).convert(source)

        self.assertEqual(retry_calls, [((1, 2), "completeness_and_ocr_failure")])
        self.assertEqual(outcome.status, "published")

    def test_scan_like_pdf_is_saved_but_not_published(self) -> None:
        source = self.root / "scan.pdf"
        self._make_image_only_like_pdf(source)
        pipeline = ConversionPipeline(
            PipelineConfig(output_root=self.output, engine="pypdf")
        )

        outcome = pipeline.convert(source)
        self.assertEqual(outcome.status, "review_required")
        self.assertEqual(outcome.quality_status, "review_required")
        self.assertTrue(outcome.manifest_path and outcome.manifest_path.is_file())

        manifest = json.loads(outcome.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["document"]["document_type"], "scanned")
        self.assertFalse(manifest["published"])
        self.assertFalse(
            (self.output / outcome.document_id / "current.json").exists()
        )

    def test_document_id_is_stable_for_same_path(self) -> None:
        source = self.root / "操作手册.pdf"
        self.assertEqual(make_document_id(source), make_document_id(source))
        self.assertTrue(make_document_id(source).startswith("操作手册-"))
        self.assertEqual(
            make_document_id(self.root / "a.pdf", "客服手册.2026"),
            make_document_id(self.root / "another" / "b.pdf", "客服手册.2026"),
        )
        self.assertEqual(
            normalize_business_document_id(" 客服手册.2026 "),
            "客服手册.2026",
        )
        with self.assertRaises(ValueError):
            normalize_business_document_id("../secret")

    def test_recursive_discovery_excludes_versioned_output_pdfs(self) -> None:
        input_pdf = self.root / "manual.pdf"
        output_pdf = self.root / "knowledge" / "doc" / "versions" / "v1" / "source.pdf"
        output_pdf.parent.mkdir(parents=True)
        input_pdf.touch()
        output_pdf.touch()

        sources = _discover_pdf_sources(
            self.root,
            recursive=True,
            output_root=self.root / "knowledge",
        )
        self.assertEqual(sources, [input_pdf])


if __name__ == "__main__":
    unittest.main()
