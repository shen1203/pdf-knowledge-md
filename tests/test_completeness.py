from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    from pdf_to_md.completeness import evaluate_completeness

    TESTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    TESTS_AVAILABLE = False


@unittest.skipUnless(TESTS_AVAILABLE, "Completeness test dependencies unavailable")
class CompletenessTests(unittest.TestCase):
    def test_detects_missing_key_fact_and_statement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path = Path(temporary) / "manual.pdf"
            document = canvas.Canvas(str(pdf_path), pagesize=letter)
            document.drawString(
                72,
                740,
                "Warning: Model CS-2026 must restart within 30 minutes.",
            )
            document.showPage()
            document.save()

            report = evaluate_completeness(
                pdf_path,
                "<!-- source-page: 1 -->\n\n# Restart instructions\n",
            )

        self.assertFalse(report["passed"])
        self.assertEqual(report["status"], "incomplete")
        self.assertLess(report["checks"]["critical_fact_coverage"], 1.0)
        self.assertLess(report["checks"]["key_statement_coverage"], 1.0)
        self.assertTrue(report["warnings"])

    def test_detects_one_missing_page_even_when_other_page_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path = Path(temporary) / "two-pages.pdf"
            document = canvas.Canvas(str(pdf_path), pagesize=letter)
            document.drawString(72, 740, "Page one general instructions.")
            document.showPage()
            document.drawString(
                72,
                740,
                "Warning: Model ZX-900 must stop within 15 minutes.",
            )
            document.showPage()
            document.save()

            report = evaluate_completeness(
                pdf_path,
                (
                    "<!-- source-page: 1 -->\n\n"
                    "Page one general instructions.\n"
                ),
            )

        self.assertFalse(report["passed"])
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"]["problem_pages"], [2])
        self.assertEqual(report["pages"][1]["status"], "missing")

    def test_does_not_claim_page_level_success_without_page_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path = Path(temporary) / "unmapped.pdf"
            document = canvas.Canvas(str(pdf_path), pagesize=letter)
            document.drawString(72, 740, "Required value is ZX-42.")
            document.showPage()
            document.save()

            report = evaluate_completeness(
                pdf_path,
                "Required value is ZX-42.\n",
            )

        self.assertFalse(report["passed"])
        self.assertEqual(report["status"], "limited")
        self.assertEqual(report["checks"]["unverifiable_pages"], [1])
        self.assertEqual(report["pages"][0]["status"], "unverifiable")


if __name__ == "__main__":
    unittest.main()
