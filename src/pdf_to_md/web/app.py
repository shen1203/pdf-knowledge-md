from __future__ import annotations

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..engines import engine_status
from ..pipeline import normalize_business_document_id
from .database import TaskStore
from .service import (
    TERMINAL_STATUSES,
    TaskProcessor,
    build_export_zip,
    load_manifest,
    load_markdown,
)
from .settings import WebSettings


PACKAGE_ROOT = Path(__file__).resolve().parent
SAFE_FILENAME = re.compile(r"[^\w.\-]+", flags=re.UNICODE)
STATUS_LABELS = {
    "queued": "排队中",
    "running": "转换中",
    "published": "已发布",
    "review_required": "待复核",
    "failed": "未通过",
    "skipped": "已跳过",
    "error": "执行失败",
}
QUALITY_LABELS = {
    "passed": "通过",
    "review_required": "待复核",
    "failed": "未通过",
}
DOCUMENT_TYPE_LABELS = {
    "native_text": "原生文本",
    "mixed": "混合型",
    "scanned": "扫描件",
}


def _safe_pdf_filename(filename: str | None) -> str:
    name = Path(filename or "document.pdf").name
    stem = SAFE_FILENAME.sub("_", Path(name).stem).strip("._") or "document"
    return f"{stem[:100]}.pdf"


async def _save_upload(
    upload: UploadFile,
    destination: Path,
    *,
    max_bytes: int,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=False)
    written = 0
    first_chunk = True
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                if first_chunk:
                    first_chunk = False
                    if b"%PDF-" not in chunk[:1024]:
                        raise HTTPException(400, "文件内容不是有效的 PDF")
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(413, "PDF 超过允许的上传大小")
                output.write(chunk)
    except Exception:
        if destination.is_file():
            destination.unlink()
        if destination.parent.is_dir() and not any(destination.parent.iterdir()):
            destination.parent.rmdir()
        raise
    finally:
        await upload.close()
    if written == 0:
        if destination.is_file():
            destination.unlink()
        raise HTTPException(400, "上传的 PDF 是空文件")
    return written


def create_app(settings: WebSettings | None = None) -> FastAPI:
    settings = settings or WebSettings.from_env()
    settings.prepare()
    store = TaskStore(settings.database_path)
    processor = TaskProcessor(store, settings)
    executor = ThreadPoolExecutor(
        max_workers=settings.worker_count,
        thread_name_prefix="pdf-converter",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        executor.shutdown(wait=False, cancel_futures=False)

    app = FastAPI(
        title="PDF Knowledge MD",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.processor = processor
    app.state.executor = executor

    templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
    app.mount(
        "/static",
        StaticFiles(directory=PACKAGE_ROOT / "static"),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "tasks": store.list_tasks(),
                "engines": engine_status(),
                "available_engine_count": sum(
                    1 for state in engine_status().values() if state["available"]
                ),
                "status_labels": STATUS_LABELS,
                "default_engine": settings.default_engine,
                "max_upload_mb": settings.max_upload_bytes // (1024 * 1024),
            },
        )

    @app.post("/tasks")
    async def create_task(
        document_id: Annotated[str, Form()],
        engine: Annotated[str, Form()] = "auto",
        category: Annotated[str | None, Form()] = None,
        business_version: Annotated[str | None, Form()] = None,
        effective_date: Annotated[str | None, Form()] = None,
        pdf: UploadFile = File(...),
    ):
        try:
            normalized_document_id = normalize_business_document_id(document_id)
        except ValueError as exc:
            await pdf.close()
            raise HTTPException(400, str(exc)) from exc
        if engine not in {"auto", "pypdf", "docling", "paddleocr"}:
            await pdf.close()
            raise HTTPException(400, "不支持的解析器")
        if not pdf.filename or Path(pdf.filename).suffix.lower() != ".pdf":
            await pdf.close()
            raise HTTPException(400, "只允许上传 .pdf 文件")

        task_id = uuid.uuid4().hex
        original_filename = Path(pdf.filename).name
        upload_dir = settings.uploads_root / task_id
        stored_path = upload_dir / _safe_pdf_filename(original_filename)
        await _save_upload(
            pdf,
            stored_path,
            max_bytes=settings.max_upload_bytes,
        )
        store.create_task(
            task_id=task_id,
            document_id=normalized_document_id,
            original_filename=original_filename,
            stored_path=stored_path,
            engine=engine,
            category=category,
            business_version=business_version,
            effective_date=effective_date,
        )
        if settings.process_inline:
            processor.process(task_id)
        else:
            executor.submit(processor.process, task_id)
        return RedirectResponse(url=f"/tasks/{task_id}", status_code=303)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_detail(request: Request, task_id: str):
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return templates.TemplateResponse(
            request=request,
            name="task_detail.html",
            context={
                "task": task,
                "manifest": load_manifest(task),
                "terminal_statuses": TERMINAL_STATUSES,
                "status_labels": STATUS_LABELS,
                "quality_labels": QUALITY_LABELS,
                "document_type_labels": DOCUMENT_TYPE_LABELS,
            },
        )

    @app.get("/tasks/{task_id}/preview", response_class=HTMLResponse)
    async def markdown_preview(request: Request, task_id: str):
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        markdown = load_markdown(task)
        if markdown is None:
            raise HTTPException(404, "Markdown 尚未生成")
        return templates.TemplateResponse(
            request=request,
            name="preview.html",
            context={"task": task, "markdown_text": markdown},
        )

    @app.get("/tasks/{task_id}/download/markdown")
    async def download_markdown(task_id: str):
        task = store.get_task(task_id)
        if not task or not task.get("output_path"):
            raise HTTPException(404, "Markdown 尚未生成")
        path = Path(task["output_path"])
        if not path.is_file():
            raise HTTPException(404, "Markdown 文件不存在")
        return FileResponse(
            path,
            media_type="text/markdown; charset=utf-8",
            filename=f"{task['document_id']}.md",
        )

    @app.get("/tasks/{task_id}/download/zip")
    async def download_zip(task_id: str):
        task = store.get_task(task_id)
        if not task or not task.get("manifest_path"):
            raise HTTPException(404, "转换产物尚未生成")
        path = build_export_zip(task, settings)
        return FileResponse(
            path,
            media_type="application/zip",
            filename=f"{task['document_id']}-{task['version_id']}.zip",
        )

    @app.get("/api/tasks")
    async def api_tasks():
        return {"tasks": store.list_tasks()}

    @app.get("/api/tasks/{task_id}")
    async def api_task(task_id: str):
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        task["manifest"] = load_manifest(task)
        return task

    @app.get("/api/engines")
    async def api_engines():
        return engine_status()

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready():
        ready = store.ping() and os.access(settings.storage_root, os.W_OK)
        if not ready:
            raise HTTPException(503, "服务尚未就绪")
        return {
            "status": "ready",
            "storage_root": str(settings.storage_root),
        }

    return app


def run() -> None:
    import uvicorn

    host = os.getenv("PDF_MD_HOST", "127.0.0.1")
    port = int(os.getenv("PDF_MD_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
