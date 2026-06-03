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
    local_files = _local_video_names(media_result)
    queried_name = srrdb_lookup_name(str(media_result.get("torrent_root") or item_name))
    if not queried_name or not local_files:
        return _result("skipped", queried_name, local_files, [], "No local video filename is available for srrDB verification.", timestamp)

    cache_key = _cache_key(queried_name)
    cached = _cache_read(cache, cache_key, timestamp)
    if cached is not None:
        return cached

    try:
        payload = await client.details(queried_name)
    except Exception as exc:
        result = _result("skipped", queried_name, local_files, [], f"srrDB unavailable: {str(exc)[:160]}", timestamp)
        _cache_write(cache, cache_key, result, timestamp, ERROR_CACHE_SECONDS)
        return result

    archived = archived_video_filenames(payload)
    if not archived:
        result = _result("not_found", queried_name, local_files, [], "No srrDB archived video filename was found.", timestamp)
        _cache_write(cache, cache_key, result, timestamp, NOT_FOUND_CACHE_SECONDS)
        return result

    local_keys = {_filename_key(name) for name in local_files}
    missing = [name for name in archived if _filename_key(name) not in local_keys]
    if missing:
        reason = "srrDB archived filename mismatch. Proper filename should be: " + ", ".join(missing)
        result = _result("mismatch", queried_name, local_files, archived, reason, timestamp, matched=False)
        _cache_write(cache, cache_key, result, timestamp, FOUND_CACHE_SECONDS)
        return result

    result = _result("verified", queried_name, local_files, archived, "srrDB archived video filename matches local file.", timestamp, matched=True)
    _cache_write(cache, cache_key, result, timestamp, FOUND_CACHE_SECONDS)
    return result


def srrdb_lookup_name(value: str) -> str:
    name = PurePosixPath(str(value or "")).name.strip()
    name = re.sub(r"\.(?:mkv|mp4|m4v|avi|m2ts|ts|mov|wmv)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", ".", name)
    name = re.sub(r"\.{2,}", ".", name)
    return name.strip(".")


def archived_video_filenames(payload: Mapping[str, Any] | Sequence[Any]) -> List[str]:
    if not isinstance(payload, Mapping):
        return []
    entries = payload.get("archived-files")
    if not isinstance(entries, list):
        return []
    names: List[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = PurePosixPath(str(entry.get("name") or "")).name
        if PurePosixPath(name).suffix.lower() in VIDEO_EXTENSIONS:
            names.append(name)
    return list(dict.fromkeys(names))


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
    names = [str(name) for name in media_result.get("complete_names", []) if str(name).strip()]
    if names:
        return names
    files = media_result.get("video_files") if isinstance(media_result.get("video_files"), list) else []
    return [str(file_info.get("basename") or PurePosixPath(str(file_info.get("name") or "")).name) for file_info in files if isinstance(file_info, Mapping)]


def _result(
    status: str,
    queried_name: str,
    local_files: Sequence[str],
    archived_files: Sequence[str],
    reason: str,
    checked_at: int,
    *,
    matched: Optional[bool] = None,
) -> Dict[str, Any]:
    return {
        "version": 1,
        "status": status,
        "queried_name": queried_name,
        "local_video_files": list(local_files),
        "archived_video_files": list(archived_files),
        "proper_filenames": list(archived_files),
        "matched": matched,
        "reason": reason,
        "checked_at": checked_at,
    }


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
    return PurePosixPath(str(name or "")).name


def _dedupe_flags(flags: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for flag in flags:
        key = str(flag.get("key") or "")
        detail = str(flag.get("detail") or "")
        if not key:
            continue
        deduped[(key, detail)] = dict(flag)
    return list(deduped.values())
