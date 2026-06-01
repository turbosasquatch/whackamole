from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.inventory import build_inventory_meta, item_inventory_meta, sort_coverage_values
from app.reducer import TRACKER_BUCKETS


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
                    arr_results TEXT NOT NULL DEFAULT '{}',
                    inventory_meta TEXT NOT NULL DEFAULT '{}',
                    raw_torrent TEXT NOT NULL DEFAULT '{}',
                    ignored_reason TEXT NOT NULL DEFAULT '',
                    baseline INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(instance_id, hash)
                )
                """
            )
            self._ensure_column(conn, "items", "arr_results", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "items", "inventory_meta", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "items", "inventory_group_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "items", "inventory_media_type", "TEXT NOT NULL DEFAULT 'unknown'")
            self._ensure_column(conn, "items", "inventory_tracker_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "items", "inventory_tracker_label", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "items", "inventory_tracker_primary", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "items", "inventory_is_cross_seed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "items", "inventory_is_upload", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "items", "inventory_is_support", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "items", "check_stage", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "items", "check_results", "TEXT NOT NULL DEFAULT '{}'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_status_updated ON items(status, updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_inventory_group ON items(inventory_group_key)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_inventory_group_tracker "
                "ON items(inventory_group_key, inventory_tracker_key, inventory_tracker_primary)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_status_media_group "
                "ON items(status, inventory_media_type, inventory_group_key, updated_at DESC)"
            )

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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

    def backfill_inventory_columns(self) -> int:
        if self.get_kv("inventory_columns_backfilled_v1") == "true":
            return 0
        updated = 0
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM items").fetchall()
            for row in rows:
                meta = item_inventory_meta(dict(row))
                conn.execute(
                    """
                    UPDATE items
                    SET inventory_meta = ?, inventory_group_key = ?, inventory_media_type = ?,
                        inventory_tracker_key = ?, inventory_tracker_label = ?, inventory_tracker_primary = ?,
                        inventory_is_cross_seed = ?, inventory_is_upload = ?, inventory_is_support = ?
                    WHERE id = ?
                    """,
                    (*_inventory_values(meta), row["id"]),
                )
                updated += 1
            conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("inventory_columns_backfilled_v1", "true"),
            )
        return updated

    def item_exists(self, instance_id: int, torrent_hash: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM items WHERE instance_id = ? AND hash = ?",
                (instance_id, torrent_hash),
            ).fetchone()
            return row is not None

    def existing_hashes(self, instance_id: int, hashes: Iterable[str]) -> set[str]:
        values = [str(value) for value in hashes if str(value)]
        if not values:
            return set()
        placeholders = ",".join("?" for _ in values)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT hash FROM items WHERE instance_id = ? AND hash IN ({placeholders})",
                [instance_id] + values,
            ).fetchall()
            return {str(row["hash"]) for row in rows}

    def insert_discovered(
        self,
        instance_id: int,
        torrent: Dict[str, Any],
        status: str,
        baseline: bool,
        inventory_meta: Optional[Any] = None,
    ) -> None:
        now = int(time.time())
        torrent_hash = str(torrent.get("hash"))
        content_path = str(torrent.get("content_path") or torrent.get("contentPath") or "")
        meta = inventory_meta or build_inventory_meta(torrent)
        encoded_torrent = json.dumps(torrent)
        encoded_meta = json.dumps(meta)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO items(
                    instance_id, hash, name, category, tags, content_path, status, size,
                    added_on, completion_on, discovered_at, updated_at, raw_torrent, inventory_meta,
                    inventory_group_key, inventory_media_type, inventory_tracker_key,
                    inventory_tracker_label, inventory_tracker_primary, inventory_is_cross_seed,
                    inventory_is_upload, inventory_is_support, baseline
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    torrent_hash,
                    str(torrent.get("name") or ""),
                    str(torrent.get("category") or ""),
                    str(torrent.get("tags") or ""),
                    content_path,
                    status,
                    int(torrent.get("size") or torrent.get("total_size") or 0),
                    int(torrent.get("added_on") or 0),
                    int(torrent.get("completion_on") or 0),
                    now,
                    now,
                    encoded_torrent,
                    encoded_meta,
                    *_inventory_values(meta)[1:],
                    1 if baseline else 0,
                ),
            )
            conn.execute(
                """
                UPDATE items
                SET category = ?, tags = ?, content_path = ?, raw_torrent = ?, inventory_meta = ?,
                    inventory_group_key = ?, inventory_media_type = ?, inventory_tracker_key = ?,
                    inventory_tracker_label = ?, inventory_tracker_primary = ?,
                    inventory_is_cross_seed = ?, inventory_is_upload = ?, inventory_is_support = ?
                WHERE instance_id = ? AND hash = ?
                """,
                (
                    str(torrent.get("category") or ""),
                    str(torrent.get("tags") or ""),
                    content_path,
                    encoded_torrent,
                    encoded_meta,
                    *_inventory_values(meta)[1:],
                    instance_id,
                    torrent_hash,
                ),
            )

    def sync_torrent_metadata(self, instance_id: int, torrent: Dict[str, Any], inventory_meta: Optional[Any] = None) -> None:
        content_path = str(torrent.get("content_path") or torrent.get("contentPath") or "")
        meta = inventory_meta or build_inventory_meta(torrent)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE items
                SET category = ?, tags = ?, content_path = ?, raw_torrent = ?, inventory_meta = ?,
                    inventory_group_key = ?, inventory_media_type = ?, inventory_tracker_key = ?,
                    inventory_tracker_label = ?, inventory_tracker_primary = ?,
                    inventory_is_cross_seed = ?, inventory_is_upload = ?, inventory_is_support = ?
                WHERE instance_id = ? AND hash = ?
                """,
                (
                    str(torrent.get("category") or ""),
                    str(torrent.get("tags") or ""),
                    content_path,
                    json.dumps(torrent),
                    json.dumps(meta),
                    *_inventory_values(meta)[1:],
                    instance_id,
                    str(torrent.get("hash")),
                ),
            )

    def list_items_filtered(
        self,
        statuses: Iterable[str],
        limit: int = 100,
        offset: int = 0,
        media: str = "all",
        missing: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
        due_errors_only: bool = False,
        q: str = "",
    ) -> List[sqlite3.Row]:
        where_sql, params = self._filtered_where(statuses, media, missing, hide_any_primary, due_errors_only, q)
        offset = max(0, int(offset or 0))
        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM items AS i {where_sql} ORDER BY i.updated_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

    def count_items_filtered(
        self,
        statuses: Iterable[str],
        media: str = "all",
        missing: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
        due_errors_only: bool = False,
        q: str = "",
    ) -> int:
        where_sql, params = self._filtered_where(statuses, media, missing, hide_any_primary, due_errors_only, q)
        with self.connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM items AS i {where_sql}", params).fetchone()
            return int(row["count"])

    def coverage_for_group_keys(self, group_keys: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
        values = [str(value) for value in dict.fromkeys(group_keys) if str(value)]
        if not values:
            return {}
        placeholders = ",".join("?" for _ in values)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT inventory_group_key, inventory_tracker_key, inventory_tracker_label, inventory_tracker_primary
                FROM items
                WHERE inventory_group_key IN ({placeholders})
                  AND inventory_tracker_key <> ''
                """,
                values,
            ).fetchall()
        grouped: Dict[str, List[Dict[str, Any]]] = {value: [] for value in values}
        seen: set[Tuple[str, str]] = set()
        for row in rows:
            group_key = str(row["inventory_group_key"] or "")
            tracker_key = str(row["inventory_tracker_key"] or "")
            dedupe_key = (group_key, tracker_key)
            if not group_key or not tracker_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            grouped.setdefault(group_key, []).append(
                {
                    "key": tracker_key,
                    "label": str(row["inventory_tracker_label"] or tracker_key),
                    "primary": bool(row["inventory_tracker_primary"]),
                }
            )
        return {key: sort_coverage_values(value) for key, value in grouped.items()}

    def bulk_requeue_baseline_filtered(
        self,
        media: str = "all",
        missing: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
    ) -> int:
        return self.bulk_requeue_filtered(
            ["baseline"],
            media=media,
            missing=missing,
            hide_any_primary=hide_any_primary,
            reason="Bulk recheck requested from baseline filtered set",
        )

    def bulk_requeue_filtered(
        self,
        statuses: Iterable[str],
        media: str = "all",
        missing: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
        reason: str = "Bulk recheck requested from filtered set",
        q: str = "",
    ) -> int:
        where_sql, params = self._filtered_where(statuses, media, missing, hide_any_primary, q=q)
        now = int(time.time())
        with self.connect() as conn:
            rows = conn.execute(f"SELECT i.id FROM items AS i {where_sql}", params).fetchall()
            ids = [int(row["id"]) for row in rows]
            if not ids:
                return 0
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE items
                SET status = 'queued', verdict = '', reason = ?,
                    tracker_results = '[]', arr_results = '{{}}', check_stage = '', check_results = '{{}}',
                    next_check_at = NULL, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                [reason, now] + ids,
            )
            return len(ids)

    def _filtered_where(
        self,
        statuses: Iterable[str],
        media: str = "all",
        missing: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
        due_errors_only: bool = False,
        q: str = "",
    ) -> Tuple[str, List[Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        values = list(statuses)
        if values:
            now = int(time.time())
            non_error_values = [value for value in values if value != "error"]
            status_clauses: List[str] = []
            if non_error_values:
                placeholders = ",".join("?" for _ in non_error_values)
                status_clauses.append(f"i.status IN ({placeholders})")
                params.extend(non_error_values)
            if "error" in values:
                if due_errors_only:
                    status_clauses.append("i.status = 'error' AND (i.next_check_at IS NULL OR i.next_check_at <= ?)")
                    params.append(now)
                else:
                    status_clauses.append("i.status = 'error'")
            clauses.append(f"({' OR '.join(status_clauses)})")
        media = (media or "all").lower()
        if media not in {"", "all"}:
            clauses.append("i.inventory_media_type = ?")
            params.append(media)
        selected_missing = [str(value).upper() for value in (missing or []) if str(value).upper()]
        if selected_missing:
            placeholders = ",".join("?" for _ in selected_missing)
            clauses.append(
                "NOT EXISTS ("
                "SELECT 1 FROM items AS c "
                "WHERE c.inventory_group_key = i.inventory_group_key "
                "AND c.inventory_tracker_key IN (" + placeholders + ")"
                ")"
            )
            params.extend(selected_missing)
        if hide_any_primary:
            clauses.append(
                "NOT EXISTS ("
                "SELECT 1 FROM items AS c "
                "WHERE c.inventory_group_key = i.inventory_group_key "
                "AND c.inventory_tracker_primary = 1"
                ")"
            )
        query = str(q or "").strip().lower()
        if query:
            like = f"%{query}%"
            searchable_columns = [
                "i.name",
                "i.hash",
                "i.category",
                "i.tags",
                "i.content_path",
                "i.mapped_path",
                "i.status",
                "i.verdict",
                "i.reason",
                "i.tracker_results",
                "i.arr_results",
                "i.inventory_meta",
                "i.raw_torrent",
            ]
            clauses.append("(" + " OR ".join(f"LOWER({column}) LIKE ?" for column in searchable_columns) + ")")
            params.extend([like] * len(searchable_columns))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params

    def count_active_queue(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM items WHERE status IN ('queued', 'deferred', 'checking')"
            ).fetchone()
            return int(row["count"])

    def queue_counts(self) -> Dict[str, int]:
        now = int(time.time())
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) AS deferred,
                    SUM(CASE WHEN status = 'checking' THEN 1 ELSE 0 END) AS checking,
                    SUM(CASE WHEN status = 'error' AND (next_check_at IS NULL OR next_check_at <= ?) THEN 1 ELSE 0 END) AS due_errors,
                    SUM(CASE WHEN status = 'error' AND next_check_at > ? THEN 1 ELSE 0 END) AS waiting_errors
                FROM items
                """,
                (now, now),
            ).fetchone()
        counts = {
            "queued": int(rows["queued"] or 0),
            "deferred": int(rows["deferred"] or 0),
            "checking": int(rows["checking"] or 0),
            "due_errors": int(rows["due_errors"] or 0),
            "waiting_errors": int(rows["waiting_errors"] or 0),
        }
        counts["active"] = counts["queued"] + counts["deferred"] + counts["checking"] + counts["due_errors"]
        return counts

    def whacked_stats(self) -> Dict[str, int]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'inventory' AND inventory_is_cross_seed = 1 THEN 1 ELSE 0 END) AS cross_seed_count,
                    SUM(CASE WHEN status = 'inventory' AND inventory_is_upload = 1 THEN 1 ELSE 0 END) AS upload_count,
                    SUM(CASE WHEN status = 'inventory' AND inventory_is_support = 1 THEN 1 ELSE 0 END) AS support_total,
                    SUM(CASE WHEN status = 'covered' THEN 1 ELSE 0 END) AS covered_items
                FROM items
                """
            ).fetchone()
            covered_rows = conn.execute("SELECT check_results FROM items WHERE status = 'covered'").fetchall()

        holes_filled = 0
        for covered_row in covered_rows:
            check_results = _json_dict(covered_row["check_results"])
            resolution = check_results.get("coverage_resolution")
            if not isinstance(resolution, dict):
                continue
            trackers = resolution.get("resolved_trackers")
            if isinstance(trackers, list):
                holes_filled += len([tracker for tracker in trackers if str(tracker).strip()])

        return {
            "cross_seed_count": int(row["cross_seed_count"] or 0) if row else 0,
            "upload_count": int(row["upload_count"] or 0) if row else 0,
            "support_total": int(row["support_total"] or 0) if row else 0,
            "covered_items": int(row["covered_items"] or 0) if row else 0,
            "holes_filled": holes_filled,
        }

    def resolve_covered_candidates(self) -> Dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM items WHERE status = 'candidate'").fetchall()
        if not rows:
            return {"items": 0, "trackers": 0}

        row_payloads = [dict(row) for row in rows]
        group_keys = [
            str(row.get("inventory_group_key") or item_inventory_meta(row).get("group_key") or "")
            for row in row_payloads
        ]
        coverage = self.coverage_for_group_keys(group_keys)
        now = int(time.time())
        updates: List[Tuple[str, str, str, str, int, int, int]] = []
        resolved_tracker_count = 0

        for row in row_payloads:
            group_key = str(row.get("inventory_group_key") or item_inventory_meta(row).get("group_key") or "")
            present_trackers = {str(item.get("key") or "").upper() for item in coverage.get(group_key, [])}
            candidate_trackers = _candidate_trackers_for_resolution(row)
            if not candidate_trackers or not set(candidate_trackers).issubset(present_trackers):
                continue

            tracker_results = _covered_tracker_results(row.get("tracker_results"), row.get("verdict"), candidate_trackers)
            arr_results = _covered_arr_results(row.get("arr_results"), candidate_trackers, now)
            reason = f"Covered in QUI: {', '.join(candidate_trackers)}"
            check_results = _covered_check_results(
                row.get("check_results"),
                tracker_results,
                arr_results,
                candidate_trackers,
                reason,
                now,
            )
            updates.append(
                (
                    reason,
                    json.dumps(tracker_results),
                    json.dumps(arr_results),
                    json.dumps(check_results),
                    now,
                    now,
                    int(row["id"]),
                )
            )
            resolved_tracker_count += len(candidate_trackers)

        if updates:
            with self.connect() as conn:
                conn.executemany(
                    """
                    UPDATE items
                    SET status = 'covered',
                        verdict = 'covered',
                        reason = ?,
                        tracker_results = ?,
                        arr_results = ?,
                        check_results = ?,
                        check_stage = 'done',
                        next_check_at = NULL,
                        last_checked_at = COALESCE(last_checked_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    updates,
                )

        return {"items": len(updates), "trackers": resolved_tracker_count}

    def recover_stale_checking(self, next_check_at: int) -> int:
        now = int(time.time())
        reason = "Whackamole restarted while this check was running. It will retry after backoff."
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE items
                SET status = 'error',
                    verdict = 'interrupted_check',
                    reason = ?,
                    check_stage = 'interrupted',
                    next_check_at = ?,
                    updated_at = ?,
                    last_checked_at = ?
                WHERE status = 'checking'
                """,
                (reason, next_check_at, now, now),
            )
            return int(cursor.rowcount or 0)

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

    def list_items(self, statuses: Iterable[str], limit: int = 100, offset: int = 0) -> List[sqlite3.Row]:
        values = list(statuses)
        offset = max(0, int(offset or 0))
        if not values:
            with self.connect() as conn:
                return conn.execute(
                    "SELECT * FROM items ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        placeholders = ",".join("?" for _ in values)
        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM items WHERE status IN ({placeholders}) ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                values + [limit, offset],
            ).fetchall()

    def count_items(self, statuses: Iterable[str]) -> int:
        values = list(statuses)
        if not values:
            with self.connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS count FROM items").fetchone()
                return int(row["count"])
        placeholders = ",".join("?" for _ in values)
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM items WHERE status IN ({placeholders})",
                values,
            ).fetchone()
            return int(row["count"])

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
        tracker_results: Optional[Any] = None,
        arr_results: Optional[Any] = None,
        check_stage: Optional[str] = None,
        check_results: Optional[Any] = None,
        next_check_at: Optional[int] = None,
        increment_attempt: bool = False,
    ) -> None:
        now = int(time.time())
        encoded_arr_results = None if arr_results is None else json.dumps(arr_results)
        encoded_tracker_results = None if tracker_results is None else json.dumps(tracker_results or [])
        encoded_check_results = None if check_results is None else json.dumps(check_results)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE items
                SET status = ?, verdict = ?, reason = COALESCE(NULLIF(?, ''), reason),
                    mapped_path = COALESCE(NULLIF(?, ''), mapped_path),
                    ua_args = COALESCE(NULLIF(?, ''), ua_args),
                    ua_log = COALESCE(NULLIF(?, ''), ua_log),
                    tracker_results = COALESCE(?, tracker_results),
                    arr_results = COALESCE(?, arr_results),
                    check_stage = COALESCE(?, check_stage),
                    check_results = COALESCE(?, check_results),
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
                    encoded_tracker_results,
                    encoded_arr_results,
                    check_stage,
                    encoded_check_results,
                    next_check_at,
                    now,
                    1 if status in {"candidate", "blocked", "manual_review", "error"} else 0,
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
                    tracker_results = '[]', arr_results = '{}', check_stage = '', check_results = '{}',
                    next_check_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, item_id),
            )

    def update_check_stage(self, item_id: int, stage: str, reason: str, check_results: Optional[Any] = None) -> None:
        now = int(time.time())
        encoded_check_results = None if check_results is None else json.dumps(check_results)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE items
                SET status = 'checking', check_stage = ?, reason = ?,
                    check_results = COALESCE(?, check_results),
                    updated_at = ?
                WHERE id = ?
                """,
                (stage, reason, encoded_check_results, now, item_id),
            )

    def ignore(self, item_id: int, reason: str = "Ignored from dashboard") -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                "UPDATE items SET status = 'ignored', ignored_reason = ?, updated_at = ? WHERE id = ?",
                (reason, now, item_id),
            )


def _inventory_values(meta: Any) -> Tuple[str, str, str, str, str, int, int, int, int]:
    payload = meta if isinstance(meta, dict) else {}
    tracker = payload.get("tracker") if isinstance(payload.get("tracker"), dict) else {}
    return (
        json.dumps(payload),
        str(payload.get("group_key") or ""),
        str(payload.get("media_type") or "unknown"),
        str(tracker.get("key") or ""),
        str(tracker.get("label") or tracker.get("key") or ""),
        1 if tracker.get("primary") else 0,
        1 if payload.get("is_cross_seed") else 0,
        1 if payload.get("is_upload") else 0,
        1 if payload.get("is_support") else 0,
    )


def _candidate_trackers_for_resolution(item: Dict[str, Any]) -> List[str]:
    arr_results = _json_dict(item.get("arr_results"))
    decisions = arr_results.get("decisions")
    if isinstance(decisions, list):
        trackers = [
            str(decision.get("tracker") or "").upper()
            for decision in decisions
            if isinstance(decision, dict)
            and str(decision.get("status") or "").lower() == "candidate"
            and str(decision.get("tracker") or "").strip()
        ]
        trackers = _dedupe_trackers(trackers)
        if trackers:
            return trackers

    tracker_results = _tracker_result_groups(item.get("tracker_results"), item.get("verdict"))
    return _dedupe_trackers([str(tracker).upper() for tracker in tracker_results.get("passed", [])])


def _covered_tracker_results(value: Any, verdict: Any, covered_trackers: List[str]) -> Dict[str, List[str]]:
    groups = _tracker_result_groups(value, verdict)
    covered = set(covered_trackers)
    groups["passed"] = [tracker for tracker in groups.get("passed", []) if str(tracker).upper() not in covered]
    groups["covered"] = _dedupe_trackers(list(groups.get("covered", [])) + covered_trackers)
    return groups


def _covered_arr_results(value: Any, covered_trackers: List[str], resolved_at: int) -> Dict[str, Any]:
    payload = _json_dict(value)
    if not payload:
        return {}
    covered = set(covered_trackers)
    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        updated_decisions = []
        for decision in decisions:
            if not isinstance(decision, dict):
                updated_decisions.append(decision)
                continue
            updated = dict(decision)
            tracker = str(updated.get("tracker") or "").upper()
            if str(updated.get("status") or "").lower() == "candidate" and tracker in covered:
                updated["status"] = "covered"
                updated["reason"] = "Tracker coverage is now present in QUI."
                updated["coverage_resolved_at"] = resolved_at
            updated_decisions.append(updated)
        payload["decisions"] = updated_decisions
    payload["status"] = "covered"
    payload["reason"] = f"Covered in QUI: {', '.join(covered_trackers)}"
    payload["coverage_resolved_at"] = resolved_at
    return payload


def _covered_check_results(
    value: Any,
    tracker_results: Dict[str, List[str]],
    arr_results: Dict[str, Any],
    covered_trackers: List[str],
    reason: str,
    resolved_at: int,
) -> Dict[str, Any]:
    payload = _json_dict(value)
    payload.setdefault("version", 1)
    ua = payload.get("ua") if isinstance(payload.get("ua"), dict) else {}
    ua = {**ua, "status": "covered", "verdict": "covered", "reason": reason, "tracker_results": tracker_results}
    payload["ua"] = ua
    if arr_results:
        payload["arr"] = arr_results
    payload["coverage_resolution"] = {
        "status": "covered",
        "reason": reason,
        "resolved_trackers": covered_trackers,
        "resolved_at": resolved_at,
    }

    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    stages = diagnostics.get("stages") if isinstance(diagnostics.get("stages"), list) else []
    stages = list(stages)
    stages.append(
        {
            "stage": "coverage",
            "status": "covered",
            "reason": reason,
            "covered_trackers": covered_trackers,
            "at": resolved_at,
        }
    )
    payload["diagnostics"] = {
        "stages": stages,
        "last_error": diagnostics.get("last_error") if isinstance(diagnostics.get("last_error"), dict) else {},
    }
    return payload


def _tracker_result_groups(value: Any, verdict: Any = "") -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {bucket: [] for bucket in TRACKER_BUCKETS}
    parsed: Any
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            parsed = []
    else:
        parsed = value if value is not None else []

    if isinstance(parsed, dict):
        raw_groups = parsed.get("groups") if isinstance(parsed.get("groups"), dict) else parsed
        for bucket in TRACKER_BUCKETS:
            values = raw_groups.get(bucket, [])
            if isinstance(values, list):
                groups[bucket] = [str(item) for item in values if str(item).strip()]
        return groups

    if isinstance(parsed, list):
        if verdict == "dupe":
            bucket = "dupe"
        elif verdict == "skipped":
            bucket = "skipped"
        elif verdict in {"error", "http_error", "ua_error", "path_mapping"}:
            bucket = "error"
        else:
            bucket = "passed"
        groups[bucket] = [str(item) for item in parsed if str(item).strip()]
    return groups


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dedupe_trackers(trackers: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(str(tracker).upper() for tracker in trackers if str(tracker).strip()))
