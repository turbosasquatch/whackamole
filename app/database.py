from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '',
                    content_path TEXT NOT NULL,
                    mapped_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    verdict TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    added_on INTEGER NOT NULL DEFAULT 0,
                    completion_on INTEGER NOT NULL DEFAULT 0,
                    discovered_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_checked_at INTEGER,
                    next_check_at INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    ua_session_id TEXT NOT NULL DEFAULT '',
                    ua_args TEXT NOT NULL DEFAULT '',
                    ua_log TEXT NOT NULL DEFAULT '',
                    tracker_results TEXT NOT NULL DEFAULT '[]',
                    raw_torrent TEXT NOT NULL DEFAULT '{}',
                    ignored_reason TEXT NOT NULL DEFAULT '',
                    baseline INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(instance_id, hash)
                )
                """
            )

    def get_kv(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_kv(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def item_exists(self, instance_id: int, torrent_hash: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM items WHERE instance_id = ? AND hash = ?",
                (instance_id, torrent_hash),
            ).fetchone()
            return row is not None

    def insert_discovered(self, instance_id: int, torrent: Dict[str, Any], status: str, baseline: bool) -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO items(
                    instance_id, hash, name, category, tags, content_path, status, size,
                    added_on, completion_on, discovered_at, updated_at, raw_torrent, baseline
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    str(torrent.get("hash")),
                    str(torrent.get("name") or ""),
                    str(torrent.get("category") or ""),
                    str(torrent.get("tags") or ""),
                    str(torrent.get("content_path") or ""),
                    status,
                    int(torrent.get("size") or torrent.get("total_size") or 0),
                    int(torrent.get("added_on") or 0),
                    int(torrent.get("completion_on") or 0),
                    now,
                    now,
                    json.dumps(torrent),
                    1 if baseline else 0,
                ),
            )

    def count_active_queue(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM items WHERE status IN ('queued', 'deferred', 'checking')"
            ).fetchone()
            return int(row["count"])

    def get_due_items(self, limit: int) -> List[sqlite3.Row]:
        now = int(time.time())
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM items
                WHERE status IN ('queued', 'deferred', 'error')
                  AND (next_check_at IS NULL OR next_check_at <= ?)
                ORDER BY discovered_at ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()

    def list_items(self, statuses: Iterable[str], limit: int = 100) -> List[sqlite3.Row]:
        values = list(statuses)
        if not values:
            with self.connect() as conn:
                return conn.execute(
                    "SELECT * FROM items ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        placeholders = ",".join("?" for _ in values)
        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM items WHERE status IN ({placeholders}) ORDER BY updated_at DESC LIMIT ?",
                values + [limit],
            ).fetchall()

    def status_counts(self) -> Dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM items GROUP BY status").fetchall()
            return {str(row["status"]): int(row["count"]) for row in rows}

    def get_item(self, item_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    def update_status(
        self,
        item_id: int,
        status: str,
        verdict: str = "",
        reason: str = "",
        mapped_path: str = "",
        ua_args: str = "",
        ua_log: str = "",
        tracker_results: Optional[List[str]] = None,
        next_check_at: Optional[int] = None,
        increment_attempt: bool = False,
    ) -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE items
                SET status = ?, verdict = ?, reason = COALESCE(NULLIF(?, ''), reason),
                    mapped_path = COALESCE(NULLIF(?, ''), mapped_path),
                    ua_args = COALESCE(NULLIF(?, ''), ua_args),
                    ua_log = COALESCE(NULLIF(?, ''), ua_log),
                    tracker_results = ?,
                    next_check_at = ?, updated_at = ?,
                    last_checked_at = CASE WHEN ? THEN ? ELSE last_checked_at END,
                    attempt_count = attempt_count + ?
                WHERE id = ?
                """,
                (
                    status,
                    verdict,
                    reason,
                    mapped_path,
                    ua_args,
                    ua_log,
                    json.dumps(tracker_results or []),
                    next_check_at,
                    now,
                    1 if status in {"candidate", "blocked", "error"} else 0,
                    now,
                    1 if increment_attempt else 0,
                    item_id,
                ),
            )

    def requeue(self, item_id: int) -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE items
                SET status = 'queued', verdict = '', reason = 'Manual recheck requested',
                    next_check_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, item_id),
            )

    def ignore(self, item_id: int, reason: str = "Ignored from dashboard") -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                "UPDATE items SET status = 'ignored', ignored_reason = ?, updated_at = ? WHERE id = ?",
                (reason, now, item_id),
            )
