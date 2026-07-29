from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from ..comparison import build_comparison_report
from ..pipeline import ConversionPipeline, PipelineConfig, sha256_text
from ..summarizer import create_extract_summary
from .database import TaskStore, utc_now
from .settings import WebSettings


TERMINAL_STATUSES = {
    "completed",
    "incomplete",
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
                    publish_review_required=True,
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
            if not outcome.output_path or not outcome.manifest_path:
                raise RuntimeError("转换没有生成 Markdown 文件")
            markdown = outcome.output_path.read_text(encoding="utf-8")
            summary_metadata = None
            if task["mode"] == "summary":
                markdown, summary_metadata = create_extract_summary(
                    markdown,
                    fallback_title=Path(task["original_filename"]).stem,
                )
                outcome.output_path.write_text(
                    markdown,
                    encoding="utf-8",
                    newline="\n",
                )
            with outcome.manifest_path.open("r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            manifest["conversion_mode"] = task["mode"]
            manifest["markdown"]["sha256"] = sha256_text(markdown)
            manifest["markdown"]["bytes"] = len(markdown.encode("utf-8"))
            comparison = build_comparison_report(
                Path(task["stored_path"]),
                markdown,
                mode=task["mode"],
                conversion_warnings=manifest.get("quality", {}).get(
                    "warnings", []
                ),
                summary_metadata=summary_metadata,
            )
            manifest["comparison"] = comparison
            temporary_manifest = outcome.manifest_path.with_suffix(".json.tmp")
            with temporary_manifest.open(
                "w", encoding="utf-8", newline="\n"
            ) as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            temporary_manifest.replace(outcome.manifest_path)
            self.store.update_task(
                task_id,
                status=(
                    "completed" if comparison["passed"] else "incomplete"
                ),
                quality_status=(
                    "passed" if comparison["passed"] else "failed"
                ),
                message=comparison["summary"],
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
