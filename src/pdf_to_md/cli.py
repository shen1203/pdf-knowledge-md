from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engines import engine_status
from .pipeline import ConversionPipeline, PipelineConfig


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _pipeline_from_args(args: argparse.Namespace) -> ConversionPipeline:
    return ConversionPipeline(
        PipelineConfig(
            output_root=Path(args.output),
            engine=args.engine,
            min_text_chars_per_page=args.min_text_chars_per_page,
            publish_review_required=args.allow_review_required,
        )
    )


def _add_common_conversion_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        default="knowledge",
        help="Versioned output root (default: knowledge)",
    )
    parser.add_argument(
        "--engine",
        choices=["auto", "pypdf", "docling", "paddleocr"],
        default="auto",
        help="Parser engine (default: auto)",
    )
    parser.add_argument(
        "--min-text-chars-per-page",
        type=int,
        default=30,
        help="Page text-density threshold used for PDF classification",
    )
    parser.add_argument(
        "--allow-review-required",
        action="store_true",
        help="Publish output even when automatic quality checks require review",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Convert even if the current published source SHA-256 is unchanged",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-to-md",
        description="Auditable PDF-to-Markdown ingestion for RAG knowledge bases",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Classify a PDF before conversion"
    )
    inspect_parser.add_argument("pdf", type=Path)
    inspect_parser.add_argument(
        "--min-text-chars-per-page", type=int, default=30
    )
    inspect_parser.add_argument("--output", default="knowledge")
    inspect_parser.add_argument("--engine", default="auto")
    inspect_parser.set_defaults(allow_review_required=False)

    convert_parser = subparsers.add_parser(
        "convert", help="Convert and version one PDF"
    )
    convert_parser.add_argument("pdf", type=Path)
    _add_common_conversion_args(convert_parser)
    convert_parser.add_argument(
        "--document-id",
        help="Stable business document ID; recommended for portable deployments",
    )
    convert_parser.add_argument(
        "--source-uri",
        help="Portable source identifier recorded in the manifest",
    )

    batch_parser = subparsers.add_parser(
        "batch", help="Convert every PDF in a directory"
    )
    batch_parser.add_argument("directory", type=Path)
    batch_parser.add_argument(
        "--recursive", action="store_true", help="Search nested directories"
    )
    _add_common_conversion_args(batch_parser)

    subparsers.add_parser(
        "engines", help="Show locally available parser engines"
    )
    return parser


def _run_inspect(args: argparse.Namespace) -> int:
    pipeline = _pipeline_from_args(args)
    _print_json(pipeline.inspect(args.pdf).to_dict())
    return 0


def _run_convert(args: argparse.Namespace) -> int:
    pipeline = _pipeline_from_args(args)
    outcome = pipeline.convert(
        args.pdf,
        force=args.force,
        document_id=args.document_id,
        source_uri=args.source_uri,
    )
    _print_json(outcome.to_dict())
    return 0 if outcome.status in {"published", "skipped"} else 2


def _discover_pdf_sources(
    directory: Path, *, recursive: bool, output_root: Path
) -> list[Path]:
    directory = directory.resolve()
    output_root = output_root.resolve()
    if directory.is_relative_to(output_root):
        raise ValueError(
            "Output root cannot be the input directory or one of its parents"
        )
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(
        source
        for source in directory.glob(pattern)
        if not source.resolve().is_relative_to(output_root)
    )


def _run_batch(args: argparse.Namespace) -> int:
    directory = args.directory.resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"Input directory does not exist: {directory}")
    sources = _discover_pdf_sources(
        directory,
        recursive=args.recursive,
        output_root=Path(args.output),
    )
    pipeline = _pipeline_from_args(args)
    outcomes = []
    exit_code = 0
    for source in sources:
        try:
            outcome = pipeline.convert(
                source,
                force=args.force,
            )
            outcomes.append(outcome.to_dict())
            if outcome.status not in {"published", "skipped"}:
                exit_code = 2
        except Exception as exc:
            outcomes.append(
                {
                    "status": "error",
                    "source": str(source),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            exit_code = 1
    _print_json(
        {
            "input_directory": str(directory),
            "pdf_count": len(sources),
            "outcomes": outcomes,
        }
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            return _run_inspect(args)
        if args.command == "convert":
            return _run_convert(args)
        if args.command == "batch":
            return _run_batch(args)
        if args.command == "engines":
            _print_json(engine_status())
            return 0
    except (FileNotFoundError, NotADirectoryError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser.error(f"Unknown command: {args.command}")
    return 2
