from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pdf_to_md.web.database import TaskStore


class TaskStoreMigrationTests(unittest.TestCase):
    def test_adds_mode_to_existing_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "tasks.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    CREATE TABLE conversion_tasks (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL,
                        original_filename TEXT NOT NULL,
                        stored_path TEXT NOT NULL,
                        engine TEXT NOT NULL,
                        category TEXT,
                        business_version TEXT,
                        effective_date TEXT,
                        status TEXT NOT NULL,
                        quality_status TEXT,
                        message TEXT,
                        error TEXT,
                        version_id TEXT,
                        output_path TEXT,
                        manifest_path TEXT,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT
                    )
                    """
                )
                connection.commit()
            finally:
                connection.close()

            store = TaskStore(database_path)
            task = store.create_task(
                task_id="task-1",
                document_id="document-1",
                original_filename="manual.pdf",
                stored_path=Path(temporary) / "manual.pdf",
                engine="pypdf",
                mode="summary",
                category=None,
                business_version=None,
                effective_date=None,
            )

        self.assertEqual(task["mode"], "summary")


if __name__ == "__main__":
    unittest.main()
