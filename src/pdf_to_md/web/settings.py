from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class WebSettings:
    storage_root: Path
    max_upload_bytes: int = 50 * 1024 * 1024
    default_engine: str = "auto"
    worker_count: int = 1
    process_inline: bool = False

    @classmethod
    def from_env(cls) -> "WebSettings":
        storage_root = Path(os.getenv("PDF_MD_STORAGE_ROOT", "storage")).resolve()
        max_upload_mb = int(os.getenv("PDF_MD_MAX_UPLOAD_MB", "50"))
        worker_count = max(1, int(os.getenv("PDF_MD_WORKERS", "1")))
        return cls(
            storage_root=storage_root,
            max_upload_bytes=max_upload_mb * 1024 * 1024,
            default_engine=os.getenv("PDF_MD_ENGINE", "auto"),
            worker_count=worker_count,
            process_inline=_env_bool("PDF_MD_PROCESS_INLINE", False),
        )

    @property
    def database_path(self) -> Path:
        return self.storage_root / "web.sqlite3"

    @property
    def uploads_root(self) -> Path:
        return self.storage_root / "uploads"

    @property
    def knowledge_root(self) -> Path:
        return self.storage_root / "knowledge"

    def prepare(self) -> None:
        for directory in (
            self.storage_root,
            self.uploads_root,
            self.knowledge_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
