from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.inventory import build_inventory_meta, item_inventory_meta, sort_coverage_values
from app.media_identity import extract_release_group
from app.media_policy import apply_release_group_policy
from app.reducer import TRACKER_BUCKETS


_DASHBOARD_COLUMNS = """
    i.id, i.instance_id, i.hash, i.name, i.category, i.tags, i.content_path, i.mapped_path,
    i.status, i.verdict, i.reason, i.size, i.added_on, i.completion_on, i.discovered_at,
    i.updated_at, i.last_checked_at, i.next_check_at, i.attempt_count, i.tracker_results,
    i.arr_results, i.inventory_meta, i.ignored_reason, i.baseline, i.inventory_group_key,
    i.inventory_media_type, i.inventory_tracker_key, i.inventory_tracker_label,
    i.inventory_tracker_primary, i.inventory_is_cross_seed, i.inventory_is_upload,
    i.inventory_is_support, i.check_stage, i.check_results
"""


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queued_imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL,
                    args TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    session_id TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    output TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    started_at INTEGER NOT NULL DEFAULT 0,
                    finished_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS item_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    item_name TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'Other',
                    notes TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    resolved_at INTEGER NOT NULL DEFAULT 0
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_items_inventory_status_group_tracker "
                "ON items(status, inventory_group_key, inventory_tracker_key, inventory_tracker_primary)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_status_created ON queued_imports(status, created_at ASC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_item ON queued_imports(item_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_state_updated ON item_reports(state, updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_item ON item_reports(item_id, state)")

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

    def prune_missing_inventory(self, instance_id: int, seen_hashes: Iterable[str]) -> int:
        values = [str(value) for value in dict.fromkeys(seen_hashes) if str(value)]
        with self.connect() as conn:
            if values:
                placeholders = ",".join("?" for _ in values)
                cursor = conn.execute(
                    f"""
                    DELETE FROM items
                    WHERE instance_id = ?
                      AND status = 'inventory'
                      AND hash NOT IN ({placeholders})
                    """,
                    [instance_id] + values,
                )
            else:
                cursor = conn.execute(
                    """
                    DELETE FROM items
                    WHERE instance_id = ?
                      AND status = 'inventory'
                    """,
                    (instance_id,),
                )
            return int(cursor.rowcount or 0)

    def requeue_covered_with_missing_coverage(self) -> Dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM items WHERE status = 'covered'").fetchall()
        if not rows:
            return {"items": 0, "trackers": 0}

        row_payloads = [dict(row) for row in rows]
        group_keys = [
            str(row.get("inventory_group_key") or item_inventory_meta(row).get("group_key") or "")
            for row in row_payloads
        ]
        coverage = self.coverage_for_group_keys(group_keys)
        now = int(time.time())
        updates: List[Tuple[str, str, int, int]] = []
        lost_tracker_count = 0

        for row in row_payloads:
            check_results = _json_dict(row.get("check_results"))
            resolution = check_results.get("coverage_resolution")
            if not isinstance(resolution, dict):
                continue
            resolved_trackers = _dedupe_trackers(
                [str(tracker) for tracker in resolution.get("resolved_trackers", []) if str(tracker).strip()]
            )
            if not resolved_trackers:
                continue
            group_key = str(row.get("inventory_group_key") or item_inventory_meta(row).get("group_key") or "")
            present_trackers = {str(item.get("key") or "").upper() for item in coverage.get(group_key, [])}
            lost_trackers = [tracker for tracker in resolved_trackers if tracker not in present_trackers]
            if not lost_trackers:
                continue
            reason = f"Tracker coverage disappeared from QUI; recheck required: {', '.join(lost_trackers)}"
            check_results = _lost_coverage_check_results(check_results, lost_trackers, reason, now)
            updates.append((reason, json.dumps(check_results), now, int(row["id"])))
            lost_tracker_count += len(lost_trackers)

        if updates:
            with self.connect() as conn:
                conn.executemany(
                    """
                    UPDATE items
                    SET status = 'queued',
                        verdict = '',
                        reason = ?,
                        tracker_results = '[]',
                        arr_results = '{}',
                        check_results = ?,
                        check_stage = '',
                        next_check_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    updates,
                )
        return {"items": len(updates), "trackers": lost_tracker_count}

    def list_items_filtered(
        self,
        statuses: Iterable[str],
        limit: int = 100,
        offset: int = 0,
        media: Any = "all",
        missing: Optional[Iterable[str]] = None,
        valid_for: Optional[Iterable[str]] = None,
        reasons: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
        due_errors_only: bool = False,
        q: str = "",
    ) -> List[sqlite3.Row]:
        where_sql, params = self._filtered_where(
            statuses, media, missing, valid_for, reasons, hide_any_primary, due_errors_only, q
        )
        offset = max(0, int(offset or 0))
        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM items AS i {where_sql} ORDER BY i.updated_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

    def list_dashboard_items_filtered(
        self,
        statuses: Iterable[str],
        limit: int = 100,
        offset: int = 0,
        media: Any = "all",
        missing: Optional[Iterable[str]] = None,
        valid_for: Optional[Iterable[str]] = None,
        reasons: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
        due_errors_only: bool = False,
        q: str = "",
    ) -> List[sqlite3.Row]:
        where_sql, params = self._filtered_where(
            statuses, media, missing, valid_for, reasons, hide_any_primary, due_errors_only, q
        )
        offset = max(0, int(offset or 0))
        with self.connect() as conn:
            return conn.execute(
                f"SELECT {_DASHBOARD_COLUMNS} FROM items AS i {where_sql} ORDER BY i.updated_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

    def count_items_filtered(
        self,
        statuses: Iterable[str],
        media: Any = "all",
        missing: Optional[Iterable[str]] = None,
        valid_for: Optional[Iterable[str]] = None,
        reasons: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
        due_errors_only: bool = False,
        q: str = "",
    ) -> int:
        where_sql, params = self._filtered_where(
            statuses, media, missing, valid_for, reasons, hide_any_primary, due_errors_only, q
        )
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
                  AND status = 'inventory'
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

    def list_inventory_trackers(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT inventory_tracker_key AS key,
                       COALESCE(NULLIF(inventory_tracker_label, ''), inventory_tracker_key) AS label,
                       MAX(inventory_tracker_primary) AS is_primary
                FROM items
                WHERE inventory_tracker_key <> ''
                GROUP BY inventory_tracker_key
                ORDER BY inventory_tracker_primary DESC, inventory_tracker_key ASC
                """
            ).fetchall()
        return [
            {
                "key": str(row["key"] or "").upper(),
                "label": str(row["label"] or row["key"] or "").strip(),
                "primary": bool(row["is_primary"]),
            }
            for row in rows
            if str(row["key"] or "").strip()
        ]

    def bulk_requeue_baseline_filtered(
        self,
        media: Any = "all",
        missing: Optional[Iterable[str]] = None,
        valid_for: Optional[Iterable[str]] = None,
        reasons: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
    ) -> int:
        return self.bulk_requeue_filtered(
            ["baseline"],
            media=media,
            missing=missing,
            valid_for=valid_for,
            reasons=reasons,
            hide_any_primary=hide_any_primary,
            reason="Bulk recheck requested from baseline filtered set",
        )

    def bulk_requeue_filtered(
        self,
        statuses: Iterable[str],
        media: Any = "all",
        missing: Optional[Iterable[str]] = None,
        valid_for: Optional[Iterable[str]] = None,
        reasons: Optional[Iterable[str]] = None,
        hide_any_primary: bool = False,
        reason: str = "Bulk recheck requested from filtered set",
        q: str = "",
    ) -> int:
        where_sql, params = self._filtered_where(statuses, media, missing, valid_for, reasons, hide_any_primary, q=q)
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
        media: Any = "all",
        missing: Optional[Iterable[str]] = None,
        valid_for: Optional[Iterable[str]] = None,
        reasons: Optional[Iterable[str]] = None,
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
        selected_media = _selected_media_values(media)
        if selected_media:
            placeholders = ",".join("?" for _ in selected_media)
            clauses.append(f"i.inventory_media_type IN ({placeholders})")
            params.extend(selected_media)
        selected_missing = _selected_tracker_values(missing)
        if selected_missing:
            placeholders = ",".join("?" for _ in selected_missing)
            clauses.append(
                "NOT EXISTS ("
                "SELECT 1 FROM items AS c "
                "WHERE c.inventory_group_key = i.inventory_group_key "
                "AND c.inventory_tracker_key IN (" + placeholders + ")"
                " AND c.status = 'inventory'"
                ")"
            )
            params.extend(selected_missing)
        if hide_any_primary:
            clauses.append(
                "NOT EXISTS ("
                "SELECT 1 FROM items AS c "
                "WHERE c.inventory_group_key = i.inventory_group_key "
                "AND c.inventory_tracker_primary = 1 "
                "AND c.status = 'inventory'"
                ")"
            )
        selected_valid = _selected_tracker_values(valid_for)
        if selected_valid:
            tracker_clauses: List[str] = []
            for tracker in selected_valid:
                tracker_clauses.append(_valid_for_tracker_clause())
                params.extend([tracker, tracker, tracker, tracker, tracker])
            clauses.append("(" + " OR ".join(tracker_clauses) + ")")
        reason_clauses, reason_params = _reason_filter_clauses(reasons)
        if reason_clauses:
            clauses.append("(" + " OR ".join(reason_clauses) + ")")
            params.extend(reason_params)
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

    def reapply_release_group_policy(self, tracker_policies: Dict[str, Any]) -> Dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM items WHERE status = 'candidate'").fetchall()
        if not rows:
            return {"items": 0, "blocked_items": 0, "blocked_trackers": 0}

        now = int(time.time())
        updates: List[Tuple[str, str, str, str, str, str, int, int]] = []
        blocked_items = 0
        blocked_trackers = 0

        for raw_row in rows:
            row = dict(raw_row)
            tracker_results = _tracker_result_groups(row.get("tracker_results"), row.get("verdict"))
            arr_results = _json_dict(row.get("arr_results"))
            check_results = _json_dict(row.get("check_results"))
            release_group = _release_group_for_policy(row, check_results)
            status, verdict, reason, policy_result, flags = apply_release_group_policy(
                tracker_results=tracker_results,
                arr_results=arr_results,
                release_group=release_group,
                tracker_policies=tracker_policies,
                flags=check_results.get("flags") if isinstance(check_results.get("flags"), list) else [],
                item_name=str(row.get("name") or ""),
            )
            if status not in {"candidate", "blocked"}:
                continue

            updated_tracker_results = _policy_tracker_results(tracker_results, policy_result)
            if not _policy_effect_changed(
                row,
                tracker_results,
                check_results,
                status,
                verdict,
                reason,
                policy_result,
                updated_tracker_results,
            ):
                continue
            updated_arr_results = _policy_arr_results(arr_results, policy_result, status, reason, now)
            updated_check_results = _policy_check_results(
                check_results,
                updated_tracker_results,
                updated_arr_results,
                policy_result,
                flags,
                status,
                reason,
                now,
            )
            payload = (
                status,
                verdict,
                reason,
                json.dumps(updated_tracker_results),
                json.dumps(updated_arr_results),
                json.dumps(updated_check_results),
                now,
                int(row["id"]),
            )
            updates.append(payload)
            blocked = list(policy_result.get("blocked_trackers") or [])
            if blocked:
                blocked_trackers += len(blocked)
            if status == "blocked":
                blocked_items += 1

        if updates:
            with self.connect() as conn:
                conn.executemany(
                    """
                    UPDATE items
                    SET status = ?,
                        verdict = ?,
                        reason = ?,
                        tracker_results = ?,
                        arr_results = ?,
                        check_results = ?,
                        check_stage = 'done',
                        next_check_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    updates,
                )

        return {"items": len(updates), "blocked_items": blocked_items, "blocked_trackers": blocked_trackers}

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

    def enqueue_import(self, item_id: int, item_name: str, path: str, args: str) -> int:
        now = int(time.time())
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO queued_imports(item_id, item_name, path, args, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, 'pending', ?, ?)
                """,
                (item_id, item_name, path, args, now, now),
            )
            return int(cursor.lastrowid)

    def list_imports(self, limit: int = 100, offset: int = 0) -> List[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT qi.*, i.status AS item_status, i.verdict AS item_verdict
                FROM queued_imports AS qi
                LEFT JOIN items AS i ON i.id = qi.item_id
                ORDER BY
                  CASE qi.status
                    WHEN 'running' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'error' THEN 2
                    WHEN 'complete' THEN 3
                    ELSE 4
                  END,
                  qi.created_at ASC
                LIMIT ? OFFSET ?
                """,
                (max(1, int(limit or 100)), max(0, int(offset or 0))),
            ).fetchall()

    def queued_import_counts(self) -> Dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM queued_imports GROUP BY status").fetchall()
        counts = {"pending": 0, "running": 0, "complete": 0, "error": 0}
        for row in rows:
            key = str(row["status"] or "")
            counts[key] = int(row["count"] or 0)
        counts["active"] = counts["pending"] + counts["running"]
        counts["total"] = counts["pending"] + counts["running"] + counts["complete"] + counts["error"]
        return counts

    def recover_running_imports(self) -> int:
        now = int(time.time())
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE queued_imports
                SET status = 'pending',
                    message = 'Whackamole restarted while this import was running; it will retry.',
                    session_id = '',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            return int(cursor.rowcount or 0)

    def claim_next_import(self, session_id: str) -> Optional[sqlite3.Row]:
        now = int(time.time())
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM queued_imports WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE queued_imports
                SET status = 'running', session_id = ?, started_at = ?, updated_at = ?, message = 'Running unattended import.'
                WHERE id = ? AND status = 'pending'
                """,
                (session_id, now, now, int(row["id"])),
            )
        with self.connect() as conn:
            return conn.execute("SELECT * FROM queued_imports WHERE id = ?", (int(row["id"]),)).fetchone()

    def mark_import_complete(self, import_id: int, message: str, output: str = "") -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE queued_imports
                SET status = 'complete', message = ?, output = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (message, output[-250_000:], now, now, import_id),
            )

    def mark_import_error(self, import_id: int, message: str, output: str = "") -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE queued_imports
                SET status = 'error', message = ?, output = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (message, output[-250_000:], now, now, import_id),
            )

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

    def update_check_results(self, item_id: int, check_results: Any) -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE items
                SET check_results = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(check_results), now, item_id),
            )

    def ignore(self, item_id: int, reason: str = "Ignored from dashboard") -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                "UPDATE items SET status = 'ignored', ignored_reason = ?, updated_at = ? WHERE id = ?",
                (reason, now, item_id),
            )

    def append_service_error(self, message: str, occurred_at: Optional[int] = None, limit: int = 20) -> None:
        text = str(message or "").strip()
        if not text:
            return
        timestamp = int(occurred_at or time.time())
        entries = self.service_error_history()
        if entries and entries[-1].get("message") == text:
            entries[-1]["last_seen_at"] = timestamp
            entries[-1]["count"] = int(entries[-1].get("count") or 1) + 1
        else:
            entries.append({"message": text, "first_seen_at": timestamp, "last_seen_at": timestamp, "count": 1})
        entries = entries[-max(1, int(limit)) :]
        self.set_kv("service_error_history", json.dumps(entries))
        self.set_kv("last_service_error", text)

    def service_error_history(self) -> List[Dict[str, Any]]:
        try:
            parsed = json.loads(self.get_kv("service_error_history") or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def clear_service_errors(self) -> None:
        self.set_kv("service_error_history", "[]")
        self.set_kv("last_service_error", "")

    def create_report(self, item_id: int, item_name: str, stage: str, notes: str) -> int:
        now = int(time.time())
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO item_reports(item_id, item_name, stage, notes, state, created_at, updated_at)
                VALUES(?, ?, ?, ?, 'active', ?, ?)
                """,
                (int(item_id), str(item_name or ""), str(stage or "Other"), str(notes or ""), now, now),
            )
            return int(cur.lastrowid)

    def list_reports(self, state: str = "active", item_id: Optional[int] = None, limit: int = 200) -> List[sqlite3.Row]:
        clauses = ["state = ?"]
        params: List[Any] = [state]
        if item_id is not None:
            clauses.append("item_id = ?")
            params.append(int(item_id))
        params.append(int(limit))
        with self.connect() as conn:
            return conn.execute(
                f"""
                SELECT *
                FROM item_reports
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

    def get_report(self, report_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM item_reports WHERE id = ?", (int(report_id),)).fetchone()

    def resolve_report(self, report_id: int) -> bool:
        now = int(time.time())
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE item_reports
                SET state = 'resolved', updated_at = ?, resolved_at = ?
                WHERE id = ? AND state <> 'deleted'
                """,
                (now, now, int(report_id)),
            )
            return cur.rowcount > 0

    def delete_report(self, report_id: int) -> bool:
        now = int(time.time())
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE item_reports
                SET state = 'deleted', updated_at = ?
                WHERE id = ? AND state <> 'deleted'
                """,
                (now, int(report_id)),
            )
            return cur.rowcount > 0


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


def _selected_media_values(media: Any) -> List[str]:
    raw_values = media if isinstance(media, (list, tuple, set)) else [media]
    selected = []
    for value in raw_values:
        cleaned = str(value or "").strip().lower()
        if cleaned and cleaned != "all":
            selected.append(cleaned)
    return list(dict.fromkeys(selected))


def _selected_tracker_values(values: Any) -> List[str]:
    raw_values = values if isinstance(values, (list, tuple, set)) else [values]
    return list(dict.fromkeys(str(value).strip().upper() for value in raw_values if str(value or "").strip()))


def _valid_for_tracker_clause() -> str:
    policy_path = "$.release_group_policy.candidate_trackers"
    arr_path = "$.decisions"
    return f"""
        (
            (
                COALESCE(json_array_length(json_extract(i.check_results, '{policy_path}')), 0) > 0
                AND EXISTS (
                    SELECT 1
                    FROM json_each(json_extract(i.check_results, '{policy_path}')) AS policy_tracker
                    WHERE UPPER(CAST(policy_tracker.value AS TEXT)) = ?
                )
            )
            OR (
                COALESCE(json_array_length(json_extract(i.check_results, '{policy_path}')), 0) = 0
                AND EXISTS (
                    SELECT 1
                    FROM json_each(json_extract(i.arr_results, '{arr_path}')) AS arr_decision
                    WHERE UPPER(COALESCE(json_extract(arr_decision.value, '$.tracker'), '')) = ?
                      AND LOWER(COALESCE(json_extract(arr_decision.value, '$.status'), '')) = 'candidate'
                )
            )
            OR (
                COALESCE(json_array_length(json_extract(i.check_results, '{policy_path}')), 0) = 0
                AND NOT EXISTS (
                    SELECT 1
                    FROM json_each(json_extract(i.arr_results, '{arr_path}')) AS arr_any
                    WHERE LOWER(COALESCE(json_extract(arr_any.value, '$.status'), '')) = 'candidate'
                )
                AND i.status = 'candidate'
                AND (
                    EXISTS (
                        SELECT 1
                        FROM json_each(json_extract(i.tracker_results, '$.passed')) AS passed_tracker
                        WHERE UPPER(CAST(passed_tracker.value AS TEXT)) = ?
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM json_each(json_extract(i.tracker_results, '$.groups.passed')) AS grouped_passed_tracker
                        WHERE UPPER(CAST(grouped_passed_tracker.value AS TEXT)) = ?
                    )
                    OR (
                        json_type(i.tracker_results) = 'array'
                        AND EXISTS (
                            SELECT 1
                            FROM json_each(i.tracker_results) AS legacy_tracker
                            WHERE UPPER(CAST(legacy_tracker.value AS TEXT)) = ?
                        )
                    )
                )
            )
        )
    """


def _reason_filter_clauses(reasons: Optional[Iterable[str]]) -> Tuple[List[str], List[Any]]:
    raw_values = reasons if isinstance(reasons, (list, tuple, set)) else [reasons]
    selected = [str(value).strip().lower() for value in raw_values if str(value or "").strip()]
    clauses: List[str] = []
    params: List[Any] = []
    for reason in selected:
        if reason == "media_warning":
            clauses.append(
                "("
                "LOWER(i.verdict) LIKE '%media_warning%' OR LOWER(i.reason) LIKE '%mediainfo%' "
                "OR LOWER(i.check_results) LIKE '%\"severity\": \"warning\"%'"
                ")"
            )
        elif reason == "media_error":
            clauses.append(
                "("
                "LOWER(i.verdict) LIKE '%media_error%' OR LOWER(i.reason) LIKE '%mediainfo%' "
                "OR LOWER(i.check_results) LIKE '%\"severity\": \"error\"%'"
                ")"
            )
        elif reason == "arr_equal_or_better":
            clauses.append("(LOWER(i.reason) LIKE '%equal-or-better%' OR LOWER(i.arr_results) LIKE '%equal-or-better%')")
        elif reason == "banned_release_group":
            clauses.append("(LOWER(i.verdict) LIKE '%banned_release_group%' OR LOWER(i.check_results) LIKE '%banned_release_group%')")
        elif reason == "no_video":
            clauses.append("(LOWER(i.verdict) LIKE '%no_video%' OR LOWER(i.reason) LIKE '%video files%')")
        elif reason == "path_error":
            clauses.append("(LOWER(i.verdict) LIKE '%path%' OR LOWER(i.reason) LIKE '%path%' OR LOWER(i.reason) LIKE '%mount%')")
        elif reason == "ua_error":
            clauses.append("(LOWER(i.verdict) LIKE '%ua_error%' OR LOWER(i.tracker_results) LIKE '%\"ua\"%')")
        elif reason == "manual_review":
            clauses.append("(i.status = 'manual_review' OR LOWER(i.verdict) LIKE '%manual_review%')")
        else:
            clauses.append("(LOWER(i.verdict) LIKE ? OR LOWER(i.reason) LIKE ? OR LOWER(i.check_results) LIKE ?)")
            like = f"%{reason}%"
            params.extend([like, like, like])
    return clauses, params


def _release_group_for_policy(item: Dict[str, Any], check_results: Dict[str, Any]) -> str:
    policy = check_results.get("release_group_policy") if isinstance(check_results.get("release_group_policy"), dict) else {}
    media = check_results.get("media") if isinstance(check_results.get("media"), dict) else {}
    return str(policy.get("release_group") or media.get("release_group") or extract_release_group(str(item.get("name") or "")))


def _policy_tracker_results(
    tracker_results: Dict[str, List[str]],
    policy_result: Dict[str, Any],
) -> Dict[str, List[str]]:
    updated = {bucket: list(tracker_results.get(bucket, [])) for bucket in TRACKER_BUCKETS}
    allowed = _dedupe_trackers(policy_result.get("candidate_trackers") or [])
    blocked = set(_dedupe_trackers(policy_result.get("blocked_trackers") or []))
    if allowed or blocked:
        updated["passed"] = allowed
    return updated


def _policy_arr_results(
    arr_results: Dict[str, Any],
    policy_result: Dict[str, Any],
    status: str,
    reason: str,
    updated_at: int,
) -> Dict[str, Any]:
    if not arr_results:
        return {}
    updated = dict(arr_results)
    policy_decisions = {
        str(decision.get("tracker") or "").upper(): decision
        for decision in policy_result.get("decisions", [])
        if isinstance(decision, dict) and str(decision.get("tracker") or "").strip()
    }
    decisions = arr_results.get("decisions")
    if isinstance(decisions, list):
        updated_decisions = []
        for decision in decisions:
            if not isinstance(decision, dict):
                updated_decisions.append(decision)
                continue
            tracker = str(decision.get("tracker") or "").upper()
            policy_decision = policy_decisions.get(tracker)
            if policy_decision and str(decision.get("status") or "").lower() in {"candidate", "blocked"}:
                merged = {**decision, **policy_decision, "policy_reapplied_at": updated_at}
                updated_decisions.append(merged)
            else:
                updated_decisions.append(decision)
        updated["decisions"] = updated_decisions
    updated["status"] = status
    updated["reason"] = reason
    updated["policy_reapplied_at"] = updated_at
    return updated


def _policy_check_results(
    check_results: Dict[str, Any],
    tracker_results: Dict[str, List[str]],
    arr_results: Dict[str, Any],
    policy_result: Dict[str, Any],
    flags: List[Dict[str, Any]],
    status: str,
    reason: str,
    updated_at: int,
) -> Dict[str, Any]:
    updated = dict(check_results)
    updated.setdefault("version", 1)
    ua = updated.get("ua") if isinstance(updated.get("ua"), dict) else {}
    updated["ua"] = {**ua, "tracker_results": tracker_results}
    if arr_results:
        updated["arr"] = arr_results
    updated["release_group_policy"] = policy_result
    updated["flags"] = flags

    diagnostics = updated.get("diagnostics") if isinstance(updated.get("diagnostics"), dict) else {}
    stages = diagnostics.get("stages") if isinstance(diagnostics.get("stages"), list) else []
    stages = list(stages)
    stages.append(
        {
            "stage": "policy",
            "status": status,
            "reason": reason,
            "at": updated_at,
            "candidate_trackers": list(policy_result.get("candidate_trackers") or []),
            "blocked_trackers": list(policy_result.get("blocked_trackers") or []),
            "policy_only": True,
        }
    )
    updated["diagnostics"] = {
        "stages": stages,
        "last_error": diagnostics.get("last_error") if isinstance(diagnostics.get("last_error"), dict) else {},
    }
    return updated


def _policy_effect_changed(
    row: Dict[str, Any],
    current_tracker_results: Dict[str, List[str]],
    check_results: Dict[str, Any],
    status: str,
    verdict: str,
    reason: str,
    policy_result: Dict[str, Any],
    updated_tracker_results: Dict[str, List[str]],
) -> bool:
    current_policy = (
        check_results.get("release_group_policy") if isinstance(check_results.get("release_group_policy"), dict) else {}
    )
    return (
        str(row.get("status") or "") != status
        or str(row.get("verdict") or "") != verdict
        or str(row.get("reason") or "") != reason
        or current_tracker_results != updated_tracker_results
        or current_policy != policy_result
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


def _lost_coverage_check_results(
    value: Any,
    lost_trackers: List[str],
    reason: str,
    lost_at: int,
) -> Dict[str, Any]:
    payload = _json_dict(value)
    payload.setdefault("version", 1)
    resolution = payload.get("coverage_resolution") if isinstance(payload.get("coverage_resolution"), dict) else {}
    payload["coverage_resolution"] = {
        **resolution,
        "status": "lost",
        "reason": reason,
        "lost_trackers": lost_trackers,
        "lost_at": lost_at,
    }

    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    stages = diagnostics.get("stages") if isinstance(diagnostics.get("stages"), list) else []
    stages = list(stages)
    stages.append(
        {
            "stage": "coverage",
            "status": "lost",
            "reason": reason,
            "lost_trackers": lost_trackers,
            "at": lost_at,
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
