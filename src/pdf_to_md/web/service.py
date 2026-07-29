from __future__ import annotations

import json
import zipfile
from pathlib import Path
from urllib.parse import quote

from ..pipeline import ConversionPipeline, PipelineConfig
from .database import TaskStore, utc_now
from .settings import WebSettings


TERMINAL_STATUSES = {
    "published",
    "review_required",
    "failed",
    "skipped",
    "error",
}


class TaskProcessor:
    def __init__(self, store: TaskStore, settings: WebSettings):
        self.store = store
        self.settings = settings

    def process(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if not task:
            return
        self.store.update_task(
            task_id,
            status="running",
            started_at=utc_now(),
            message="正在分析和转换 PDF",
            error=None,
        )
        try:
            pipeline = ConversionPipeline(
                PipelineConfig(
                    output_root=self.settings.knowledge_root,
                    engine=task["engine"],
                )
            )
            source_uri = (
                f"web-upload://{quote(task['document_id'])}/"
                f"{quote(task['original_filename'])}"
            )
            outcome = pipeline.convert(
                Path(task["stored_path"]),
                document_id=task["document_id"],
                source_uri=source_uri,
                original_filename=task["original_filename"],
            )
            self.store.update_task(
                task_id,
                status=outcome.status,
                quality_status=outcome.quality_status,
                message=outcome.message,
                version_id=outcome.version_id,
                output_path=str(outcome.output_path) if outcome.output_path else None,
                manifest_path=(
                    str(outcome.manifest_path) if outcome.manifest_path else None
                ),
                completed_at=utc_now(),
                error=None,
            )
        except Exception as exc:
            self.store.update_task(
                task_id,
                status="error",
                quality_status="failed",
                message="转换任务执行失败",
                error=f"{type(exc).__name__}: {exc}",
                completed_at=utc_now(),
            )


def load_manifest(task: dict[str, object]) -> dict[str, object] | None:
    raw_path = task.get("manifest_path")
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_markdown(task: dict[str, object]) -> str | None:
    raw_path = task.get("output_path")
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def build_export_zip(
    task: dict[str, object],
    settings: WebSettings,
) -> Path:
    manifest_path = Path(str(task["manifest_path"]))
    version_dir = manifest_path.parent
    output_path = settings.exports_root / f"{task['id']}.zip"
    temporary = output_path.with_suffix(".zip.tmp")

    candidates = {
        "source.pdf": version_dir / "source.pdf",
        "document.md": version_dir / "document.md",
        "manifest.json": version_dir / "manifest.json",
    }
    with zipfile.ZipFile(
        temporary, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for archive_name, path in candidates.items():
            if path.is_file():
                archive.write(path, arcname=archive_name)
    temporary.replace(output_path)
    return output_path
