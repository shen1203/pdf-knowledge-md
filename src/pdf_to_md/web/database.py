from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


TASK_FIELDS = {
    "status",
    "quality_status",
    "message",
    "error",
    "version_id",
    "output_path",
    "manifest_path",
    "started_at",
    "completed_at",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversion_tasks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'full',
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
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(conversion_tasks)"
                ).fetchall()
            }
            if "mode" not in columns:
                connection.execute(
                    "ALTER TABLE conversion_tasks "
                    "ADD COLUMN mode TEXT NOT NULL DEFAULT 'full'"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversion_tasks_created
                ON conversion_tasks(created_at DESC)
                """
            )

    def create_task(
        self,
        *,
        task_id: str,
        document_id: str,
        original_filename: str,
        stored_path: Path,
        engine: str,
        mode: str,
        category: str | None,
        business_version: str | None,
        effective_date: str | None,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversion_tasks (
                    id, document_id, original_filename, stored_path, engine, mode,
                    category, business_version, effective_date, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """,
                (
                    task_id,
                    document_id,
                    original_filename,
                    str(stored_path.resolve()),
                    engine,
                    mode,
                    category or None,
                    business_version or None,
                    effective_date or None,
                    created_at,
                ),
            )
        task = self.get_task(task_id)
        assert task is not None
        return task

    def update_task(self, task_id: str, **fields: Any) -> None:
        invalid = set(fields) - TASK_FIELDS
        if invalid:
            raise ValueError(f"Unsupported task fields: {sorted(invalid)}")
        if not fields:
            return
        assignments = ", ".join(f"{field} = ?" for field in fields)
        values = list(fields.values()) + [task_id]
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE conversion_tasks SET {assignments} WHERE id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown task: {task_id}")

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM conversion_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_tasks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversion_tasks
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def ping(self) -> bool:
        with self._connection() as connection:
            value = connection.execute("SELECT 1").fetchone()[0]
        return value == 1
