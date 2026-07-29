from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    from pdf_to_md.web.app import create_app
    from pdf_to_md.web.settings import WebSettings

    WEB_TESTS_AVAILABLE = True
except ImportError:  # pragma: no cover - permits core-only test environments
    WEB_TESTS_AVAILABLE = False


def make_pdf_bytes() -> bytes:
    buffer = io.BytesIO()
    document = canvas.Canvas(buffer, pagesize=letter)
    document.setTitle("Customer Service Manual")
    document.drawString(72, 740, "1. Start service")
    document.drawString(
        72,
        710,
        "Service must start within 30 minutes. Model CS-2026 is required.",
    )
    document.showPage()
    document.save()
    return buffer.getvalue()


@unittest.skipUnless(WEB_TESTS_AVAILABLE, "Web test dependencies are not installed")
class WebApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        settings = WebSettings(
            storage_root=self.root / "storage",
            max_upload_bytes=2 * 1024 * 1024,
            default_engine="pypdf",
            worker_count=1,
            process_inline=True,
        )
        self.client_context = TestClient(create_app(settings))
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary.cleanup()

    def _upload(self):
        return self.client.post(
            "/convert",
            files={
                "pdf": (
                    "操作手册.pdf",
                    make_pdf_bytes(),
                    "application/pdf",
                )
            },
            follow_redirects=False,
        )

    def test_home_is_single_upload_experience(self) -> None:
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("上传 PDF，直接得到 Markdown", home.text)
        self.assertNotIn("业务文档 ID", home.text)
        self.assertNotIn("质量报告", home.text)
        self.assertNotIn("解析器状态", home.text)
        self.assertEqual(self.client.get("/health/live").json()["status"], "ok")
        self.assertEqual(
            self.client.get("/health/ready").json()["status"],
            "ready",
        )

    def test_upload_converts_checks_previews_and_downloads(self) -> None:
        response = self._upload()
        self.assertEqual(response.status_code, 303)
        result_url = response.headers["location"]
        self.assertTrue(result_url.startswith("/result/"))
        task_id = result_url.rsplit("/", 1)[-1]

        result_api = self.client.get(f"/api/result/{task_id}")
        self.assertEqual(result_api.status_code, 200)
        result_data = result_api.json()
        self.assertEqual(result_data["status"], "completed")
        self.assertTrue(result_data["completeness"]["passed"])
        self.assertEqual(
            result_data["completeness"]["checks"]["source_pages"],
            1,
        )
        self.assertEqual(
            result_data["completeness"]["checks"]["page_mapping_complete"],
            True,
        )

        result = self.client.get(result_url)
        self.assertEqual(result.status_code, 200)
        self.assertIn("自动检查通过", result.text)
        self.assertIn("Service must start within 30 minutes", result.text)

        markdown = self.client.get(f"/result/{task_id}/download")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("# Customer Service Manual", markdown.text)
        self.assertIn("CS-2026", markdown.text)

    def test_rejects_non_pdf_content(self) -> None:
        invalid_extension = self.client.post(
            "/convert",
            files={"pdf": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(invalid_extension.status_code, 400)

        invalid_pdf = self.client.post(
            "/convert",
            files={"pdf": ("fake.pdf", b"not a pdf", "application/pdf")},
        )
        self.assertEqual(invalid_pdf.status_code, 400)


if __name__ == "__main__":
    unittest.main()
