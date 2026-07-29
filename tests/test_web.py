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
    document.drawString(72, 680, "General background information for new staff.")
    document.drawString(72, 650, "The service team works across several departments.")
    document.drawString(72, 620, "This paragraph provides additional context.")
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

    def _upload(self, *, mode: str = "full"):
        return self.client.post(
            "/convert",
            data={"mode": mode},
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
        self.assertIn("data-drop-zone", home.text)
        self.assertIn("完整转换", home.text)
        self.assertIn("重点摘要", home.text)
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
        self.assertEqual(result_data["mode"], "full")
        self.assertTrue(result_data["comparison"]["passed"])
        self.assertEqual(result_data["comparison"]["mode"], "full")
        self.assertEqual(
            result_data["comparison"]["checks"]["source_pages"],
            1,
        )
        self.assertEqual(
            result_data["comparison"]["checks"]["page_mapping_complete"],
            True,
        )

        result = self.client.get(result_url)
        self.assertEqual(result.status_code, 200)
        self.assertIn("完整转换已完成", result.text)
        self.assertIn("Service must start within 30 minutes", result.text)
        self.assertIn("返回上一级", result.text)
        self.assertIn("正常页面", result.text)
        self.assertIn("与原 PDF 文本相似度", result.text)
        self.assertIn("主要改动", result.text)

        markdown = self.client.get(f"/result/{task_id}/download")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("# Customer Service Manual", markdown.text)
        self.assertIn("CS-2026", markdown.text)

    def test_summary_mode_extracts_key_points_and_reports_changes(self) -> None:
        response = self._upload(mode="summary")
        self.assertEqual(response.status_code, 303)
        task_id = response.headers["location"].rsplit("/", 1)[-1]

        result_data = self.client.get(f"/api/result/{task_id}").json()
        self.assertEqual(result_data["status"], "completed")
        self.assertEqual(result_data["mode"], "summary")
        comparison = result_data["comparison"]
        self.assertEqual(comparison["mode"], "summary")
        self.assertTrue(comparison["passed"])
        self.assertGreater(
            comparison["summary_metadata"]["omitted_lines"],
            0,
        )
        self.assertTrue(comparison["changes"])

        markdown = self.client.get(f"/result/{task_id}/download").text
        self.assertIn("重点摘要", markdown)
        self.assertIn("CS-2026", markdown)
        self.assertNotIn("General background information", markdown)

        result = self.client.get(f"/result/{task_id}")
        self.assertIn("重点摘要已生成", result.text)
        self.assertIn("抽取重点行", result.text)
        self.assertIn("主动省略行", result.text)

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

        invalid_mode = self.client.post(
            "/convert",
            data={"mode": "unsupported"},
            files={
                "pdf": (
                    "manual.pdf",
                    make_pdf_bytes(),
                    "application/pdf",
                )
            },
        )
        self.assertEqual(invalid_mode.status_code, 400)


if __name__ == "__main__":
    unittest.main()
