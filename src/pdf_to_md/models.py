from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


DocumentType = Literal["native_text", "mixed", "scanned"]
QualityStatus = Literal["passed", "review_required", "failed"]


@dataclass(frozen=True)
class PdfProfile:
    source: Path
    page_count: int
    page_text_chars: list[int]
    low_text_page_numbers: list[int]
    text_chars: int
    low_text_pages: int
    low_text_page_ratio: float
    document_type: DocumentType
    metadata: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source"] = str(self.source)
        return data


@dataclass(frozen=True)
class EngineOutput:
    markdown: str
    engine: str
    engine_version: str
    warnings: list[str] = field(default_factory=list)
    page_markers: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityReport:
    status: QualityStatus
    score: int
    checks: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConversionOutcome:
    status: str
    document_id: str
    version_id: str | None
    engine: str | None
    quality_status: str | None
    output_path: Path | None
    manifest_path: Path | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("output_path", "manifest_path"):
            if data[key] is not None:
                data[key] = str(data[key])
        return data
