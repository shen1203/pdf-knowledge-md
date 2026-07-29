from __future__ import annotations

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import TaskStore
from .service import TERMINAL_STATUSES, TaskProcessor, load_manifest, load_markdown
from .settings import WebSettings


PACKAGE_ROOT = Path(__file__).resolve().parent
SAFE_FILENAME = re.compile(r"[^\w.\-]+", flags=re.UNICODE)
STATUS_LABELS = {
    "queued": "等待转换",
    "running": "正在转换",
    "completed": "转换完成",
    "incomplete": "检查未通过",
    "error": "转换失败",
}


def _safe_pdf_filename(filename: str | None) -> str:
    name = Path(filename or "document.pdf").name
    stem = SAFE_FILENAME.sub("_", Path(name).stem).strip("._") or "document"
    return f"{stem[:100]}.pdf"


def _automatic_document_id(filename: str, task_id: str) -> str:
    stem = Path(_safe_pdf_filename(filename)).stem[:80]
    return f"{stem}-{task_id[:8]}"


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
        title="PDF to Markdown",
        version="0.3.0",
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
    async def home(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "max_upload_mb": settings.max_upload_bytes // (1024 * 1024),
            },
        )

    @app.post("/convert")
    async def convert_pdf(pdf: UploadFile = File(...)):
        if not pdf.filename or Path(pdf.filename).suffix.lower() != ".pdf":
            await pdf.close()
            raise HTTPException(400, "只允许上传 .pdf 文件")

        task_id = uuid.uuid4().hex
        original_filename = Path(pdf.filename).name
        document_id = _automatic_document_id(original_filename, task_id)
        upload_dir = settings.uploads_root / task_id
        stored_path = upload_dir / _safe_pdf_filename(original_filename)
        await _save_upload(
            pdf,
            stored_path,
            max_bytes=settings.max_upload_bytes,
        )
        store.create_task(
            task_id=task_id,
            document_id=document_id,
            original_filename=original_filename,
            stored_path=stored_path,
            engine=settings.default_engine,
            category=None,
            business_version=None,
            effective_date=None,
        )
        if settings.process_inline:
            processor.process(task_id)
        else:
            executor.submit(processor.process, task_id)
        return RedirectResponse(url=f"/result/{task_id}", status_code=303)

    @app.get("/result/{task_id}", response_class=HTMLResponse)
    async def result(request: Request, task_id: str):
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(404, "转换任务不存在")
        manifest = load_manifest(task)
        completeness = manifest.get("completeness") if manifest else None
        return templates.TemplateResponse(
            request=request,
            name="task_detail.html",
            context={
                "task": task,
                "markdown_text": load_markdown(task),
                "completeness": completeness,
                "terminal_statuses": TERMINAL_STATUSES,
                "status_labels": STATUS_LABELS,
            },
        )

    @app.get("/result/{task_id}/download")
    async def download_markdown(task_id: str):
        task = store.get_task(task_id)
        if not task or not task.get("output_path"):
            raise HTTPException(404, "Markdown 尚未生成")
        path = Path(task["output_path"])
        if not path.is_file():
            raise HTTPException(404, "Markdown 文件不存在")
        filename = f"{Path(task['original_filename']).stem}.md"
        return FileResponse(
            path,
            media_type="text/markdown; charset=utf-8",
            filename=filename,
        )

    @app.get("/api/result/{task_id}")
    async def api_result(task_id: str):
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(404, "转换任务不存在")
        manifest = load_manifest(task)
        task["completeness"] = (
            manifest.get("completeness") if manifest else None
        )
        return task

    @app.get("/health/live")
    async def health_live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def health_ready():
        ready = store.ping() and os.access(settings.storage_root, os.W_OK)
        if not ready:
            raise HTTPException(503, "服务尚未就绪")
        return {"status": "ready"}

    return app


def run() -> None:
    import uvicorn

    host = os.getenv("PDF_MD_HOST", "127.0.0.1")
    port = int(os.getenv("PDF_MD_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)
