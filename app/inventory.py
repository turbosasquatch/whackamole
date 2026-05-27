from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


PRIMARY_TRACKERS = ("DP", "ULCX", "IHD")

TRACKER_LABELS = {
    "DP": "DP",
    "ULCX": "ULCX",
    "IHD": "IHD",
    "DC": "DC",
    "TL": "TL",
    "IPT": "IPT",
    "SP": "Seedpool",
}

TRACKER_ALIASES = {
    "DP": ("darkpeers", "darkpeer", "dp"),
    "ULCX": ("upload.cx", "uploadcx", "ulcx"),
    "IHD": ("infinityhd", "ihd"),
    "DC": ("digitalcore",),
    "TL": ("torrentleech", "tleechreload"),
    "IPT": ("iptorrents",),
    "SP": ("seedpool",),
}

_PRIMARY_ORDER = {tracker: index for index, tracker in enumerate(PRIMARY_TRACKERS)}


def build_inventory_meta(torrent: Mapping[str, Any]) -> Dict[str, Any]:
    tracker = detect_tracker(torrent)
    media_type = detect_media_type(torrent)
    category = str(torrent.get("category") or "")
    tags = _tags(torrent.get("tags"))
    paths = _path_values(torrent)
    is_cross_seed = _has_term(category, "cross") or any("cross" in tag for tag in tags) or _paths_contain(paths, "cross-seeds")
    is_upload = category.lower() == "uploads" or any("upload" in tag for tag in tags) or _paths_contain(paths, "uploads")

    return {
        "version": 1,
        "group_key": release_group_key(str(torrent.get("name") or torrent.get("content_path") or "")),
        "media_type": media_type,
        "is_episode": media_type == "episode",
        "is_cross_seed": is_cross_seed,
        "is_upload": is_upload,
        "is_support": is_cross_seed or is_upload,
        "tracker": tracker or {},
    }


def item_inventory_meta(item: Mapping[str, Any]) -> Dict[str, Any]:
    stored = _jsonish_dict(item.get("inventory_meta"))
    raw = _jsonish_dict(item.get("raw_torrent"))
    torrent = {
        **raw,
        "name": item.get("name") or raw.get("name"),
        "category": item.get("category") or raw.get("category"),
        "tags": item.get("tags") or raw.get("tags"),
        "content_path": item.get("content_path") or raw.get("content_path"),
    }
    derived = build_inventory_meta(torrent)
    if not stored:
        return derived
    merged = {**derived, **stored}
    if not isinstance(merged.get("tracker"), dict) or not merged.get("tracker"):
        merged["tracker"] = derived["tracker"]
    if not merged.get("group_key"):
        merged["group_key"] = derived["group_key"]
    return merged


def coverage_index(rows: Iterable[Mapping[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        meta = item_inventory_meta(row)
        group_key = str(meta.get("group_key") or "")
        tracker = meta.get("tracker") if isinstance(meta.get("tracker"), dict) else {}
        tracker_key = str(tracker.get("key") or "")
        if not group_key or not tracker_key:
            continue
        grouped.setdefault(group_key, {})[tracker_key] = {
            "key": tracker_key,
            "label": str(tracker.get("label") or tracker_key),
            "primary": tracker_key in PRIMARY_TRACKERS,
        }

    return {group: _sort_coverage(list(values.values())) for group, values in grouped.items()}


def coverage_for_item(item: Mapping[str, Any], index: Mapping[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    meta = item_inventory_meta(item)
    return list(index.get(str(meta.get("group_key") or ""), []))


def missing_primary_trackers(coverage: Sequence[Mapping[str, Any]]) -> List[str]:
    present = {str(item.get("key") or "") for item in coverage if item.get("primary")}
    return [tracker for tracker in PRIMARY_TRACKERS if tracker not in present]


def filter_inventory_rows(
    rows: Sequence[Mapping[str, Any]],
    index: Mapping[str, List[Dict[str, Any]]],
    media: str = "all",
    missing: Optional[Iterable[str]] = None,
    hide_any_primary: bool = False,
) -> List[Mapping[str, Any]]:
    selected_missing = {tracker.upper() for tracker in (missing or []) if tracker.upper() in PRIMARY_TRACKERS}
    media = (media or "all").lower()
    filtered = []
    for row in rows:
        meta = item_inventory_meta(row)
        if media not in {"", "all"} and str(meta.get("media_type") or "") != media:
            continue
        coverage = coverage_for_item(row, index)
        present = {str(item.get("key") or "") for item in coverage}
        if selected_missing and selected_missing.intersection(present):
            continue
        if hide_any_primary and any(item.get("primary") for item in coverage):
            continue
        filtered.append(row)
    return filtered


def is_inventory_support(meta: Mapping[str, Any]) -> bool:
    return bool(meta.get("is_support"))


def detect_media_type(torrent: Mapping[str, Any]) -> str:
    name = str(torrent.get("name") or "")
    category = str(torrent.get("category") or "").lower()
    tags = " ".join(_tags(torrent.get("tags")))
    path = " ".join(_path_values(torrent)).lower()
    haystack = f"{category} {tags} {path} {name.lower()}"

    if re.search(r"\bS\d{1,2}E\d{1,3}\b", name, flags=re.IGNORECASE):
        return "episode"
    if "movie" in haystack:
        return "movie"
    if "tv" in haystack or re.search(r"\bS\d{1,2}\b", name, flags=re.IGNORECASE):
        return "tv"
    return "unknown"


def release_group_key(value: str) -> str:
    name = PurePosixPath(str(value or "")).name or str(value or "")
    name = re.sub(r"--[a-f0-9]{6,}$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\.(?:mkv|mp4|avi|m2ts|ts|mov|wmv|rar|zip)$", "", name, flags=re.IGNORECASE)
    name = name.replace("&", " and ")
    name = re.sub(r"[^a-zA-Z0-9]+", " ", name.lower())
    return " ".join(name.split())


def detect_tracker(torrent: Mapping[str, Any]) -> Dict[str, Any]:
    fields = [
        str(torrent.get("category") or ""),
        str(torrent.get("tags") or ""),
        str(torrent.get("save_path") or torrent.get("savePath") or ""),
        str(torrent.get("content_path") or torrent.get("contentPath") or ""),
        str(torrent.get("comment") or ""),
        str(torrent.get("tracker") or ""),
    ]
    segments = _path_segments(fields)

    for segment in segments:
        canonical = _canonical_tracker(segment, exact_short=True)
        if canonical:
            return _tracker_payload(canonical)

    compact_fields = " ".join(_compact(field) for field in fields)
    for canonical, aliases in TRACKER_ALIASES.items():
        for alias in aliases:
            compact_alias = _compact(alias)
            if len(compact_alias) >= 4 and compact_alias in compact_fields:
                return _tracker_payload(canonical)

    folder = _support_folder(fields)
    if folder:
        canonical = _canonical_tracker(folder, exact_short=True)
        if canonical:
            return _tracker_payload(canonical)
        label = _clean_label(folder)
        if label:
            return {
                "key": f"OTHER:{_compact(label).upper()}",
                "label": label,
                "primary": False,
            }

    return {}


def _canonical_tracker(value: str, exact_short: bool = False) -> Optional[str]:
    compact = _compact(value)
    if not compact:
        return None
    for canonical, aliases in TRACKER_ALIASES.items():
        alias_compacts = {_compact(alias) for alias in aliases}
        if compact == _compact(canonical) or compact in alias_compacts:
            return canonical
        if not exact_short and any(len(alias) >= 4 and alias in compact for alias in alias_compacts):
            return canonical
    return None


def _tracker_payload(canonical: str) -> Dict[str, Any]:
    return {
        "key": canonical,
        "label": TRACKER_LABELS.get(canonical, canonical),
        "primary": canonical in PRIMARY_TRACKERS,
    }


def _sort_coverage(values: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        values,
        key=lambda item: (
            0 if item.get("primary") else 1,
            _PRIMARY_ORDER.get(str(item.get("key")), 99),
            str(item.get("label") or ""),
        ),
    )


def _path_values(torrent: Mapping[str, Any]) -> List[str]:
    return [
        str(torrent.get("save_path") or torrent.get("savePath") or ""),
        str(torrent.get("content_path") or torrent.get("contentPath") or ""),
    ]


def _paths_contain(paths: Sequence[str], value: str) -> bool:
    wanted = value.lower()
    return any(wanted in path.lower().replace("\\", "/") for path in paths)


def _path_segments(values: Iterable[str]) -> List[str]:
    segments: List[str] = []
    for value in values:
        segments.extend(part for part in re.split(r"[\\/]+", value) if part)
    return segments


def _support_folder(values: Iterable[str]) -> str:
    for value in values:
        parts = [part for part in re.split(r"[\\/]+", value) if part]
        lowered = [part.lower() for part in parts]
        for marker in ("cross-seeds", "uploads"):
            if marker in lowered:
                index = lowered.index(marker)
                if index + 1 < len(parts):
                    return re.sub(r"--[a-f0-9]{6,}$", "", parts[index + 1], flags=re.IGNORECASE)
    return ""


def _clean_label(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", " ", value).strip(" ._-")
    return value[:32]


def _has_term(value: str, term: str) -> bool:
    return term.lower() in value.lower()


def _tags(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return [part.strip().lower() for part in str(value or "").split(",") if part.strip()]


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _jsonish_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
