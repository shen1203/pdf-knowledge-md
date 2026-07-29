from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analyzer import analyze_pdf
from .engines import select_engine
from .models import ConversionOutcome, PdfProfile
from .quality import evaluate_quality


@dataclass(frozen=True)
class PipelineConfig:
    output_root: Path = Path("knowledge")
    engine: str = "auto"
    min_text_chars_per_page: int = 30
    scanned_page_ratio: float = 0.8
    mixed_page_ratio: float = 0.2
    max_replacement_char_ratio: float = 0.005
    publish_review_required: bool = False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_business_document_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ValueError("Business document ID cannot be empty")
    if len(normalized) > 128:
        raise ValueError("Business document ID cannot exceed 128 characters")
    if normalized in {".", ".."} or normalized.startswith("."):
        raise ValueError("Business document ID cannot start with a dot")
    invalid = [
        char
        for char in normalized
        if not (char.isalnum() or char in {"-", "_", "."})
    ]
    if invalid:
        raise ValueError(
            "Business document ID may only contain Unicode letters, numbers, "
            "hyphens, underscores, and dots"
        )
    return normalized


def make_document_id(source: Path, business_id: str | None = None) -> str:
    if business_id is not None:
        return normalize_business_document_id(business_id)
    normalized = unicodedata.normalize("NFKC", source.stem)
    safe_stem = "".join(
        char.lower() if char.isalnum() else "-" for char in normalized
    )
    safe_stem = re.sub(r"-+", "-", safe_stem).strip("-")[:64] or "document"
    path_hash = hashlib.sha256(
        str(source.resolve()).casefold().encode("utf-8")
    ).hexdigest()[:10]
    return f"{safe_stem}-{path_hash}"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _remove_failed_staging(staging: Path, document_root: Path) -> None:
    resolved_staging = staging.resolve()
    resolved_root = document_root.resolve()
    if (
        resolved_staging.parent != resolved_root
        or not resolved_staging.name.startswith(".staging-")
    ):
        raise RuntimeError(f"Refusing to remove unexpected path: {resolved_staging}")
    shutil.rmtree(resolved_staging)


class ConversionPipeline:
    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or PipelineConfig()

    def inspect(self, source: Path) -> PdfProfile:
        return analyze_pdf(
            source,
            min_text_chars_per_page=self.config.min_text_chars_per_page,
            scanned_page_ratio=self.config.scanned_page_ratio,
            mixed_page_ratio=self.config.mixed_page_ratio,
        )

    def convert(
        self,
        source: Path,
        *,
        force: bool = False,
        document_id: str | None = None,
        source_uri: str | None = None,
        original_filename: str | None = None,
    ) -> ConversionOutcome:
        started = time.perf_counter()
        source = source.resolve()
        source_hash = sha256_file(source)
        document_id = make_document_id(source, document_id)
        document_root = self.config.output_root.resolve() / document_id
        current_pointer = document_root / "current.json"
        current = _read_json(current_pointer)

        if (
            not force
            and current
            and current.get("source", {}).get("sha256") == source_hash
        ):
            current_version = str(current.get("version_id"))
            current_dir = document_root / "versions" / current_version
            return ConversionOutcome(
                status="skipped",
                document_id=document_id,
                version_id=current_version,
                engine=current.get("engine"),
                quality_status=current.get("quality_status"),
                output_path=current_dir / "document.md",
                manifest_path=current_dir / "manifest.json",
                message="源文件 SHA-256 与当前已发布版本一致，已跳过重复转换",
            )

        profile = self.inspect(source)
        engine = select_engine(self.config.engine, profile)
        output = engine.convert(source, profile)
        quality = evaluate_quality(
            profile,
            output,
            max_replacement_char_ratio=self.config.max_replacement_char_ratio,
        )

        now = datetime.now(timezone.utc)
        version_id = (
            now.strftime("%Y%m%dT%H%M%S")
            + f"{now.microsecond:06d}Z-{source_hash[:12]}"
        )
        versions_root = document_root / "versions"
        staging = document_root / f".staging-{version_id}-{uuid.uuid4().hex}"
        version_dir = versions_root / version_id
        staging.mkdir(parents=True, exist_ok=False)

        should_publish = quality.status == "passed" or (
            quality.status == "review_required"
            and self.config.publish_review_required
        )
        markdown_hash = sha256_text(output.markdown)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        manifest = {
            "schema_version": 1,
            "document_id": document_id,
            "version_id": version_id,
            "created_at": now.isoformat(),
            "published": should_publish,
            "source": {
                "path": str(source),
                "uri": source_uri,
                "filename": source.name,
                "original_filename": original_filename or source.name,
                "sha256": source_hash,
                "bytes": source.stat().st_size,
            },
            "document": profile.to_dict(),
            "engine": {
                "requested": self.config.engine,
                "selected": output.engine,
                "version": output.engine_version,
                "metadata": output.metadata,
            },
            "markdown": {
                "filename": "document.md",
                "sha256": markdown_hash,
                "bytes": len(output.markdown.encode("utf-8")),
            },
            "quality": quality.to_dict(),
            "pipeline": {
                "elapsed_ms": elapsed_ms,
                "min_text_chars_per_page": self.config.min_text_chars_per_page,
                "scanned_page_ratio": self.config.scanned_page_ratio,
                "mixed_page_ratio": self.config.mixed_page_ratio,
                "max_replacement_char_ratio": (
                    self.config.max_replacement_char_ratio
                ),
            },
        }

        try:
            shutil.copy2(source, staging / "source.pdf")
            with (staging / "document.md").open(
                "w", encoding="utf-8", newline="\n"
            ) as handle:
                handle.write(output.markdown)
            _atomic_write_json(staging / "manifest.json", manifest)
            versions_root.mkdir(parents=True, exist_ok=True)
            os.replace(staging, version_dir)
        except Exception:
            if staging.exists():
                _remove_failed_staging(staging, document_root)
            raise

        if should_publish:
            pointer = {
                "schema_version": 1,
                "document_id": document_id,
                "version_id": version_id,
                "updated_at": now.isoformat(),
                "engine": output.engine,
                "quality_status": quality.status,
                "source": {"sha256": source_hash},
                "paths": {
                    "markdown": f"versions/{version_id}/document.md",
                    "manifest": f"versions/{version_id}/manifest.json",
                    "source": f"versions/{version_id}/source.pdf",
                },
            }
            _atomic_write_json(current_pointer, pointer)
            status = "published"
            message = "转换通过质量检查，当前发布版本已更新"
        else:
            status = quality.status
            message = (
                "候选版本已保存，但因需要质量复核，当前发布版本未更新"
                if quality.status == "review_required"
                else "候选版本未通过质量检查"
            )

        return ConversionOutcome(
            status=status,
            document_id=document_id,
            version_id=version_id,
            engine=output.engine,
            quality_status=quality.status,
            output_path=version_dir / "document.md",
            manifest_path=version_dir / "manifest.json",
            message=message,
        )
