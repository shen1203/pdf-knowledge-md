from __future__ import annotations

import unittest

from pdf_to_md.summarizer import create_extract_summary


class ExtractiveSummarizerTests(unittest.TestCase):
    def test_keeps_key_lines_and_omits_non_key_context(self) -> None:
        markdown = """
# Service Manual

<!-- source-page: 1 -->

## Start procedure

General introduction for the service team.

Background information without an operational requirement.

The operator must enter model ZX-77 within 15 minutes.

<!-- source-page: 2 -->

## Safety

Never share the access code.

Additional company history.
""".strip()

        summary, metadata = create_extract_summary(
            markdown,
            fallback_title="Fallback",
        )

        self.assertIn("# Service Manual — 重点摘要", summary)
        self.assertIn("must enter model ZX-77 within 15 minutes", summary)
        self.assertIn("Never share the access code", summary)
        self.assertNotIn(
            "Background information without an operational requirement",
            summary,
        )
        self.assertGreater(metadata["omitted_lines"], 0)
        self.assertEqual(len(metadata["pages"]), 2)


if __name__ == "__main__":
    unittest.main()
