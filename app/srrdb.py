from __future__ import annotations

import json
import re
import time
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple


VIDEO_EXTENSIONS = {".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}
FOUND_CACHE_SECONDS = 30 * 24 * 60 * 60
NOT_FOUND_CACHE_SECONDS = 7 * 24 * 60 * 60
ERROR_CACHE_SECONDS = 60 * 60


class SrrdbDetailsClient(Protocol):
    async def details(self, release_name: str) -> Mapping[str, Any] | Sequence[Any]:
        ...


class SrrdbCache(Protocol):
    def get_kv(self, key: str) -> Optional[str]:
        ...

    def set_kv(self, key: str, value: str) -> None:
        ...


async def verify_srrdb_release(
    *,
    item_name: str,
    media_result: Mapping[str, Any],
    client: SrrdbDetailsClient,
    cache: SrrdbCache,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    timestamp = int(now or time.time())
    local_entries = _local_video_entries(media_result)
    local_files = [entry["name"] for entry in local_entries]
    queried_name = srrdb_lookup_name(str(media_result.get("torrent_root") or item_name))
    if not queried_name or not local_files:
        return _result(
            "skipped",
            queried_name,
            local_files,
            [],
            "No local video filename is available for srrDB verification.",
            timestamp,
            local_entries=local_entries,
        )

    cache_key = _cache_key(queried_name)
    cached = _cache_read(cache, cache_key, timestamp)
    if cached is not None:
        return cached

    try:
        payload = await client.details(queried_name)
    except Exception as exc:
        result = _result(
            "skipped",
            queried_name,
            local_files,
            [],
            f"srrDB unavailable: {str(exc)[:160]}",
            timestamp,
            local_entries=local_entries,
        )
        _cache_write(cache, cache_key, result, timestamp, ERROR_CACHE_SECONDS)
        return result

    archived_entries = archived_video_entries(payload)
    archived = [entry["name"] for entry in archived_entries]
    if not archived:
        result = _result(
            "not_found",
            queried_name,
            local_files,
            [],
            "No srrDB archived video filename was found.",
            timestamp,
            local_entries=local_entries,
            archived_entries=archived_entries,
        )
        _cache_write(cache, cache_key, result, timestamp, NOT_FOUND_CACHE_SECONDS)
        return result

    local_keys = {_filename_key(name) for name in local_files}
    missing = [name for name in archived if _filename_key(name) not in local_keys]
    if missing:
        reason = "srrDB archived filename mismatch. Proper filename should be: " + ", ".join(missing)
        result = _result(
            "mismatch",
            queried_name,
            local_files,
            archived,
            reason,
            timestamp,
            matched=False,
            local_entries=local_entries,
            archived_entries=archived_entries,
        )
        _cache_write(cache, cache_key, result, timestamp, FOUND_CACHE_SECONDS)
        return result

    local_by_key = {_filename_key(entry["name"]): entry for entry in local_entries}
    size_mismatches = [
        archived_entry["name"]
        for archived_entry in archived_entries
        if int(archived_entry.get("size") or 0) > 0
        and int(local_by_key.get(_filename_key(archived_entry["name"]), {}).get("size") or 0) > 0
        and int(archived_entry.get("size") or 0) != int(local_by_key.get(_filename_key(archived_entry["name"]), {}).get("size") or 0)
    ]
    if size_mismatches:
        reason = "srrDB archived file size mismatch. File may have been modified: " + ", ".join(size_mismatches)
        result = _result(
            "mismatch",
            queried_name,
            local_files,
            archived,
            reason,
            timestamp,
            matched=False,
            local_entries=local_entries,
            archived_entries=archived_entries,
        )
        _cache_write(cache, cache_key, result, timestamp, FOUND_CACHE_SECONDS)
        return result

    result = _result(
        "verified",
        queried_name,
        local_files,
        archived,
        "srrDB archived video filename matches local file.",
        timestamp,
        matched=True,
        local_entries=local_entries,
        archived_entries=archived_entries,
    )
    _cache_write(cache, cache_key, result, timestamp, FOUND_CACHE_SECONDS)
    return result


def srrdb_lookup_name(value: str) -> str:
    name = PurePosixPath(str(value or "")).name.strip()
    name = re.sub(r"\.(?:mkv|mp4|m4v|avi|m2ts|ts|mov|wmv)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", ".", name)
    name = re.sub(r"\.{2,}", ".", name)
    return name.strip(".")


def archived_video_filenames(payload: Mapping[str, Any] | Sequence[Any]) -> List[str]:
    return [entry["name"] for entry in archived_video_entries(payload)]


def archived_video_entries(payload: Mapping[str, Any] | Sequence[Any]) -> List[Dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    entries = payload.get("archived-files")
    if not isinstance(entries, list):
        return []
    videos: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = PurePosixPath(str(entry.get("name") or "")).name
        if PurePosixPath(name).suffix.lower() not in VIDEO_EXTENSIONS or name in seen:
            continue
        seen.add(name)
        videos.append({"name": name, "size": int(entry.get("size") or 0)})
    return videos


def apply_srrdb_result(
    *,
    status: str,
    verdict: str,
    reason: str,
    flags: Sequence[Mapping[str, Any]],
    srrdb_result: Mapping[str, Any],
) -> Tuple[str, str, str, List[Dict[str, Any]]]:
    next_flags = [dict(flag) for flag in flags]
    result_status = str(srrdb_result.get("status") or "")
    if result_status == "mismatch":
        detail = str(srrdb_result.get("reason") or "srrDB archived filename does not match the local video filename.")
        next_flags.append(
            {
                "key": "srrdb_filename_mismatch",
                "label": "srrDB filename mismatch",
                "severity": "warning",
                "detail": detail,
            }
        )
        return "manual_review", "srrdb_filename_mismatch", detail, _dedupe_flags(next_flags)
    if result_status == "verified":
        next_flags.append(
            {
                "key": "srrdb_verified",
                "label": "srrDB verified",
                "severity": "info",
                "detail": str(srrdb_result.get("reason") or "srrDB archived filename matches."),
            }
        )
    return status, verdict, reason, _dedupe_flags(next_flags)


def _local_video_names(media_result: Mapping[str, Any]) -> List[str]:
    return [entry["name"] for entry in _local_video_entries(media_result)]


def _local_video_entries(media_result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    names = [str(name) for name in media_result.get("complete_names", []) if str(name).strip()]
    files = media_result.get("video_files") if isinstance(media_result.get("video_files"), list) else []
    by_name = {
        str(file_info.get("basename") or PurePosixPath(str(file_info.get("name") or "")).name): int(file_info.get("size") or 0)
        for file_info in files
        if isinstance(file_info, Mapping)
    }
    if names:
        return [{"name": name, "size": int(by_name.get(PurePosixPath(name).name) or 0)} for name in names]
    return [
        {"name": str(file_info.get("basename") or PurePosixPath(str(file_info.get("name") or "")).name), "size": int(file_info.get("size") or 0)}
        for file_info in files
        if isinstance(file_info, Mapping)
    ]


def _result(
    status: str,
    queried_name: str,
    local_files: Sequence[str],
    archived_files: Sequence[str],
    reason: str,
    checked_at: int,
    *,
    matched: Optional[bool] = None,
    local_entries: Optional[Sequence[Mapping[str, Any]]] = None,
    archived_entries: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    local_payload = _entry_payloads(local_entries) if local_entries is not None else _entries_from_names(local_files)
    archived_payload = _entry_payloads(archived_entries) if archived_entries is not None else _entries_from_names(archived_files)
    return {
        "version": 1,
        "status": status,
        "queried_name": queried_name,
        "local_video_files": list(local_files),
        "archived_video_files": list(archived_files),
        "proper_filenames": list(archived_files),
        "local_video_entries": local_payload,
        "archived_video_entries": archived_payload,
        "comparison_pairs": _comparison_pairs(local_payload, archived_payload),
        "matched": matched,
        "reason": reason,
        "checked_at": checked_at,
    }


def _entry_payloads(entries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"name": str(entry.get("name") or ""), "size": int(entry.get("size") or 0)}
        for entry in entries
        if isinstance(entry, Mapping) and str(entry.get("name") or "")
    ]


def _entries_from_names(names: Sequence[str]) -> List[Dict[str, Any]]:
    return [{"name": str(name), "size": 0} for name in names if str(name)]


def _comparison_pairs(local_entries: Sequence[Mapping[str, Any]], archived_entries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    local_by_key = {_filename_key(str(entry.get("name") or "")): entry for entry in local_entries if str(entry.get("name") or "")}
    archived_by_key = {_filename_key(str(entry.get("name") or "")): entry for entry in archived_entries if str(entry.get("name") or "")}
    pairs: List[Dict[str, Any]] = []
    used_archived: set[str] = set()
    for local_index, local in enumerate(local_entries):
        local_name = str(local.get("name") or "")
        local_key = _filename_key(local_name)
        archived = archived_by_key.get(local_key)
        pair_status = "matched"
        if archived is None and len(local_entries) == len(archived_entries):
            archived = archived_entries[local_index]
            pair_status = "filename_mismatch"
        if archived is None:
            pairs.append(
                {
                    "local_name": local_name,
                    "archived_name": "",
                    "local_size": int(local.get("size") or 0),
                    "archived_size": 0,
                    "status": "missing_archived_pair",
                }
            )
            continue
        archived_name = str(archived.get("name") or "")
        used_archived.add(_filename_key(archived_name))
        local_size = int(local.get("size") or 0)
        archived_size = int(archived.get("size") or 0)
        if pair_status == "matched" and local_size and archived_size and local_size != archived_size:
            pair_status = "size_mismatch"
        pairs.append(
            {
                "local_name": local_name,
                "archived_name": archived_name,
                "local_size": local_size,
                "archived_size": archived_size,
                "status": pair_status,
            }
        )
    for archived in archived_entries:
        archived_name = str(archived.get("name") or "")
        archived_key = _filename_key(archived_name)
        if archived_key and archived_key not in used_archived and archived_key not in local_by_key:
            pairs.append(
                {
                    "local_name": "",
                    "archived_name": archived_name,
                    "local_size": 0,
                    "archived_size": int(archived.get("size") or 0),
                    "status": "extra_archived",
                }
            )
    return pairs


def _cache_key(queried_name: str) -> str:
    return "srrdb_details:" + re.sub(r"[^a-z0-9._-]+", "_", queried_name.lower())


def _cache_read(cache: SrrdbCache, key: str, now: int) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(cache.get_kv(key) or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    expires_at = int(payload.get("expires_at") or 0)
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else None
    if expires_at <= now or result is None:
        return None
    return dict(result)


def _cache_write(cache: SrrdbCache, key: str, result: Mapping[str, Any], now: int, ttl: int) -> None:
    cache.set_kv(key, json.dumps({"expires_at": now + ttl, "result": dict(result)}))


def _filename_key(name: str) -> str:
    return PurePosixPath(str(name or "")).name.casefold()


def _dedupe_flags(flags: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for flag in flags:
        key = str(flag.get("key") or "")
        detail = str(flag.get("detail") or "")
        if not key:
            continue
        deduped[(key, detail)] = dict(flag)
    return list(deduped.values())
