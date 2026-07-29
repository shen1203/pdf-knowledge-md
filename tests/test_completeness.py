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


if __name__ == "__main__":
    unittest.main()
