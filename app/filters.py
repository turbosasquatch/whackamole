from __future__ import annotations

from typing import Any, Dict, List

from app.config import WatchConfig


def is_completed_torrent(torrent: Dict[str, Any]) -> bool:
    if float(torrent.get("progress") or 0) >= 1:
        return True
    if int(torrent.get("amount_left") or 0) == 0 and int(torrent.get("completion_on") or 0) > 0:
        return True
    return str(torrent.get("state", "")).lower() in {"uploading", "stalledup", "forcedup"}


def is_watchable_torrent(torrent: Dict[str, Any], watch: WatchConfig) -> bool:
    if not is_completed_torrent(torrent):
        return False

    category = str(torrent.get("category") or "").lower()
    tags = _tags(torrent.get("tags"))

    for term in watch.exclude_category_terms:
        if term and term.lower() in category:
            return False

    for term in watch.exclude_tag_terms:
        lowered = term.lower()
        if lowered and any(lowered in tag for tag in tags):
            return False

    return bool(torrent.get("hash") and torrent.get("content_path"))


def _tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).lower() for item in value]
    return [part.strip().lower() for part in str(value or "").split(",") if part.strip()]

