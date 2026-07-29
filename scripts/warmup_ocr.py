from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from paddleocr import PPStructureV3

from pdf_to_md.engines import OCR_PIPELINE_DEFAULTS


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pdf-md-ocr-warmup-") as temporary:
        sample = Path(temporary) / "warmup.png"
        image = Image.new("RGB", (1200, 800), "white")
        draw = ImageDraw.Draw(image)
        draw.text((80, 100), "PDF to Markdown OCR warmup 2026", fill="black")
        image.save(sample)

        pipeline = PPStructureV3(**OCR_PIPELINE_DEFAULTS)
        results = list(pipeline.predict(input=str(sample)))
        if not results:
            raise RuntimeError("OCR warm-up did not return a result")

    cache = Path.home() / ".paddlex"
    print("OCR warm-up completed.")
    print(f"Model cache: {cache}")


if __name__ == "__main__":
    main()
