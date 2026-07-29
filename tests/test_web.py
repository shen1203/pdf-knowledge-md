from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
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
    document.drawString(72, 710, "Open the service page and select Start.")
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

    def _upload(self, *, document_id: str = "CS-MANUAL-001"):
        return self.client.post(
            "/tasks",
            data={
                "document_id": document_id,
                "engine": "pypdf",
                "category": "售后服务",
                "business_version": "2026.1",
                "effective_date": "2026-07-29",
            },
            files={
                "pdf": (
                    "操作手册.pdf",
                    make_pdf_bytes(),
                    "application/pdf",
                )
            },
            follow_redirects=False,
        )

    def test_dashboard_and_health_endpoints(self) -> None:
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("企业知识文档转换工作台", dashboard.text)
        self.assertEqual(self.client.get("/health/live").json()["status"], "ok")
        self.assertEqual(
            self.client.get("/health/ready").json()["status"],
            "ready",
        )

    def test_upload_convert_preview_and_download(self) -> None:
        response = self._upload()
        self.assertEqual(response.status_code, 303)
        task_url = response.headers["location"]
        task_id = task_url.rsplit("/", 1)[-1]

        task = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(task.status_code, 200)
        task_data = task.json()
        self.assertEqual(task_data["status"], "published")
        self.assertEqual(task_data["document_id"], "CS-MANUAL-001")
        self.assertEqual(task_data["quality_status"], "passed")
        self.assertEqual(
            task_data["manifest"]["source"]["original_filename"],
            "操作手册.pdf",
        )
        self.assertTrue(
            task_data["manifest"]["source"]["uri"].startswith("web-upload://")
        )

        detail = self.client.get(task_url)
        self.assertEqual(detail.status_code, 200)
        self.assertIn("自动质量分", detail.text)

        preview = self.client.get(f"/tasks/{task_id}/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertIn("Start service", preview.text)

        markdown = self.client.get(f"/tasks/{task_id}/download/markdown")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("# Customer Service Manual", markdown.text)

        archive = self.client.get(f"/tasks/{task_id}/download/zip")
        self.assertEqual(archive.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
            self.assertEqual(
                set(zipped.namelist()),
                {"source.pdf", "document.md", "manifest.json"},
            )
            manifest = json.loads(zipped.read("manifest.json"))
            self.assertEqual(manifest["document_id"], "CS-MANUAL-001")

    def test_rejects_invalid_document_id_and_non_pdf_content(self) -> None:
        invalid_id = self._upload(document_id="../escape")
        self.assertEqual(invalid_id.status_code, 400)

        invalid_pdf = self.client.post(
            "/tasks",
            data={"document_id": "SAFE-001", "engine": "pypdf"},
            files={"pdf": ("fake.pdf", b"not a pdf", "application/pdf")},
        )
        self.assertEqual(invalid_pdf.status_code, 400)
        self.assertEqual(self.client.get("/api/tasks").json()["tasks"], [])


if __name__ == "__main__":
    unittest.main()
