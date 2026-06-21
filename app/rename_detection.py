from __future__ import annotations

import math
import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from app.media_identity import extract_release_group, parse_release_traits
from app.srrdb import srrdb_lookup_name


VIDEO_EXTENSIONS = {".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}
HIGH_REVIEW_KEY = "renamed_release_warning"
PLACEHOLDER_TORRENT_NAMES = {"unpack"}

_TECHNICAL_TOKEN_RE = re.compile(
    r"^(?:"
    r"\d{3,4}[pi]|2160p|1080p|1080i|720p|480p|uhd|bluray|bdrip|brrip|remux|web|web-dl|webrip|hdtv|"
    r"amzn|nf|hulu|dsnp|atv|max|hmax|all4|bbc|itv|ddp?|eac3|aac|dts|truehd|atmos|"
    r"h\.?26[45]|x26[45]|hevc|avc|proper|repack|internal|hybrid|dv|dovi|hdr10(?:plus|p)?|hdr"
    r")$",
    re.IGNORECASE,
)
_EXTRA_PATH_RE = re.compile(r"(^|[/. _-])(?:sample|extras?|featurettes?|trailer|behind[ ._-]?the[ ._-]?scenes)([/. _-]|$)", re.IGNORECASE)


def analyze_rename_detection(
    *,
    item_name: str,
    mapped_path: str = "",
    media_result: Mapping[str, Any] | None = None,
    arr_results: Mapping[str, Any] | None = None,
    srrdb_result: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    media = media_result if isinstance(media_result, Mapping) else {}
    arr = arr_results if isinstance(arr_results, Mapping) else {}
    srrdb = srrdb_result if isinstance(srrdb_result, Mapping) else {}
    video_files = _video_files(media)
    primary_files = [file_info for file_info in video_files if not _is_extra_path(str(file_info.get("name") or ""))]
    root_name = str(media.get("torrent_root") or "").strip() or _root_from_files(primary_files) or str(item_name or "").strip()
    mapped_root = PurePosixPath(str(mapped_path or "")).name
    evidence: List[Dict[str, Any]] = []

    evidence.extend(_srrdb_evidence(srrdb))
    evidence.extend(_placeholder_name_evidence(item_name, root_name, mapped_root, primary_files))
    evidence.extend(_folder_evidence(root_name, mapped_root, primary_files))
    evidence.extend(_file_evidence(root_name, primary_files))
    evidence.extend(_sibling_evidence(root_name, primary_files))
    evidence.extend(_arr_evidence(root_name or item_name, arr))
    evidence = _dedupe_evidence(evidence)

    if str(srrdb.get("status") or "").lower() == "verified":
        severe_types = {
            "mixed_release_groups",
            "mixed_technical_tail",
            "placeholder_torrent_name_mismatch",
            "random_video_basename",
        }
        evidence = [
            item
            for item in evidence
            if item.get("kind") == "srrdb_verified"
            or (item.get("confidence") == "high" and item.get("kind") in severe_types)
        ]

    high = [item for item in evidence if item.get("confidence") == "high"]
    medium = [item for item in evidence if item.get("confidence") == "medium"]
    if high:
        status = "manual_review"
        confidence = "high"
        reason = str(high[0].get("reason") or "Rename Check found high-confidence renamed release evidence.")
    elif medium:
        status = "warning"
        confidence = "medium"
        reason = str(medium[0].get("reason") or "Rename Check found possible renamed release evidence.")
    else:
        status = "pass"
        confidence = "low"
        reason = "Rename Check did not find suspicious name mismatches."

    return {
        "version": 1,
        "status": status,
        "confidence": confidence,
        "reason": reason,
        "torrent_name": str(item_name or ""),
        "root_name": root_name,
        "mapped_root": mapped_root,
        "file_count": len(primary_files),
        "evidence": evidence,
    }


def rename_detection_flag(result: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "key": HIGH_REVIEW_KEY,
        "label": "Rename Check",
        "severity": "warning",
        "detail": str(result.get("reason") or "Rename Check found high-confidence renamed release evidence."),
    }


def _srrdb_evidence(srrdb: Mapping[str, Any]) -> List[Dict[str, Any]]:
    status = str(srrdb.get("status") or "").lower()
    if status == "mismatch":
        local_entries = _srrdb_entries(srrdb, "local_video_entries", "local_video_files")
        archived_entries = _srrdb_entries(srrdb, "archived_video_entries", "proper_filenames")
        return [
            _evidence(
                kind="srrdb_mismatch",
                scope="srrdb",
                confidence="high",
                source="srrDB",
                value=", ".join(str(item) for item in srrdb.get("local_video_files") or []),
                expected=", ".join(str(item) for item in srrdb.get("proper_filenames") or []),
                reason=str(srrdb.get("reason") or "srrDB archived filenames differ from local files."),
                queried_name=str(srrdb.get("queried_name") or ""),
                local_video_entries=local_entries,
                archived_video_entries=archived_entries,
                comparison_pairs=_srrdb_pairs(srrdb, local_entries, archived_entries),
            )
        ]
    if status == "verified":
        local_entries = _srrdb_entries(srrdb, "local_video_entries", "local_video_files")
        archived_entries = _srrdb_entries(srrdb, "archived_video_entries", "proper_filenames")
        return [
            _evidence(
                kind="srrdb_verified",
                scope="srrdb",
                confidence="low",
                source="srrDB",
                value=", ".join(str(item) for item in srrdb.get("local_video_files") or []),
                expected=", ".join(str(item) for item in srrdb.get("proper_filenames") or []),
                reason="srrDB archived filename matches local files.",
                queried_name=str(srrdb.get("queried_name") or ""),
                local_video_entries=local_entries,
                archived_video_entries=archived_entries,
                comparison_pairs=_srrdb_pairs(srrdb, local_entries, archived_entries),
            )
        ]
    return []


def _folder_evidence(root_name: str, mapped_root: str, files: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    if len(files) > 1 and root_name:
        normalized = srrdb_lookup_name(root_name)
        if normalized and normalized != root_name:
            evidence.append(
                _evidence(
                    kind="folder_scene_normalization",
                    scope="folder",
                    confidence="low",
                    source="torrent_root",
                    value=root_name,
                    expected=normalized,
                    reason=f"Folder name would be normalised to {normalized}.",
                )
            )
    if mapped_root and root_name and _release_key(mapped_root) != _release_key(root_name):
        evidence.append(
            _evidence(
                kind="mapped_root_mismatch",
                scope="folder",
                confidence="medium",
                source="mapped_path",
                value=mapped_root,
                expected=root_name,
                reason="Mapped folder name differs from the torrent root name.",
            )
        )
    return evidence


def _placeholder_name_evidence(
    item_name: str,
    root_name: str,
    mapped_root: str,
    files: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    placeholders = [
        ("item_name", str(item_name or "").strip()),
        ("torrent_root", str(root_name or "").strip()),
    ]
    placeholder = next(
        ((source, value) for source, value in placeholders if _placeholder_key(value) in PLACEHOLDER_TORRENT_NAMES),
        None,
    )
    if not placeholder:
        return []

    source_name, placeholder_value = placeholder
    structured = _first_structured_content_name(mapped_root, root_name, files, placeholder_value)
    if not structured:
        return []

    return [
        _evidence(
            kind="placeholder_torrent_name_mismatch",
            scope="folder",
            confidence="high",
            source="torrent_root",
            value=placeholder_value,
            expected=structured,
            reason="Torrent/root name is a placeholder and does not identify the mapped release content.",
            source_name=source_name,
            content_name=structured,
        )
    ]


def _file_evidence(root_name: str, files: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    root_traits = parse_release_traits(root_name)
    root_group_name = root_traits.release_group or extract_release_group(root_name)
    root_group = _policy_key(root_group_name)
    for file_info in files:
        name = PurePosixPath(str(file_info.get("name") or "")).name
        stem = _stem(name)
        if not stem:
            continue
        if _has_empty_human_token(stem):
            evidence.append(
                _evidence(
                    kind="empty_title_token",
                    scope="file",
                    confidence="high",
                    source="video_file",
                    value=name,
                    reason=f"{name} contains an empty title token in the human-readable title area.",
                    filename=name,
                    files=[name],
                )
            )
        if root_traits.is_comparable and _looks_random_basename(stem):
            evidence.append(
                _evidence(
                    kind="random_video_basename",
                    scope="file",
                    confidence="high",
                    source="video_file",
                    value=name,
                    expected=root_name,
                    reason=f"{name} looks like a random renamed video basename inside a structured release folder.",
                    filename=name,
                    files=[name],
                )
            )
        file_group = _policy_key(extract_release_group(name))
        if root_group and file_group and file_group != root_group:
            evidence.append(
                _evidence(
                    kind="file_group_mismatch",
                    scope="file",
                    confidence="high",
                    source="video_file",
                    value=name,
                    expected=root_group_name,
                    reason=f"{name} uses a different release group than the folder/root.",
                    filename=name,
                    file_group=extract_release_group(name),
                    root_group=root_group_name,
                    files=[name],
                )
            )
    return evidence


def _sibling_evidence(root_name: str, files: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    if len(files) < 2:
        return []
    evidence: List[Dict[str, Any]] = []
    groups = {}
    signatures = {}
    for file_info in files:
        name = PurePosixPath(str(file_info.get("name") or "")).name
        traits = parse_release_traits(name)
        group = _policy_key(traits.release_group)
        if group:
            groups.setdefault(group, []).append(name)
        signature = _technical_signature(traits)
        if signature:
            signatures.setdefault(signature, []).append(name)

    if len(groups) > 1:
        evidence.append(
            _evidence(
                kind="mixed_release_groups",
                scope="siblings",
                confidence="high",
                source="video_files",
                value=", ".join(sorted(groups)),
                expected=extract_release_group(root_name),
                reason="Video files in the same folder use mixed release groups.",
                groups=groups,
            )
        )
    if len(signatures) > 1 and _has_majority_outlier(signatures):
        evidence.append(
            _evidence(
                kind="mixed_technical_tail",
                scope="siblings",
                confidence="high",
                source="video_files",
                value="; ".join(f"{len(names)}x {key}" for key, names in sorted(signatures.items())),
                reason="Video files in the same folder have inconsistent technical release tails.",
                signatures=signatures,
            )
        )
    return evidence


def _srrdb_entries(srrdb: Mapping[str, Any], entries_key: str, fallback_key: str) -> List[Dict[str, Any]]:
    entries = srrdb.get(entries_key)
    if isinstance(entries, list):
        return [
            {"name": str(entry.get("name") or ""), "size": int(entry.get("size") or 0)}
            for entry in entries
            if isinstance(entry, Mapping) and str(entry.get("name") or "")
        ]
    return [{"name": str(name), "size": 0} for name in srrdb.get(fallback_key, []) if str(name)]


def _srrdb_pairs(
    srrdb: Mapping[str, Any],
    local_entries: Sequence[Mapping[str, Any]],
    archived_entries: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    pairs = srrdb.get("comparison_pairs")
    if isinstance(pairs, list):
        return [dict(pair) for pair in pairs if isinstance(pair, Mapping)]
    if len(local_entries) == len(archived_entries):
        return [
            {
                "local_name": str(local.get("name") or ""),
                "archived_name": str(archived.get("name") or ""),
                "local_size": int(local.get("size") or 0),
                "archived_size": int(archived.get("size") or 0),
                "status": "legacy_pair",
            }
            for local, archived in zip(local_entries, archived_entries)
        ]
    return []


def _arr_evidence(local_title: str, arr: Mapping[str, Any]) -> List[Dict[str, Any]]:
    local_traits = parse_release_traits(local_title)
    local_group = _policy_key(local_traits.release_group)
    if not local_group:
        return []
    local_key = _release_key(local_title)
    for decision in arr.get("decisions", []) if isinstance(arr.get("decisions"), list) else []:
        if not isinstance(decision, Mapping):
            continue
        best = decision.get("best_release")
        if not isinstance(best, Mapping):
            continue
        title = str(best.get("title") or "")
        remote_traits = parse_release_traits(title, str(best.get("quality") or ""))
        if _policy_key(remote_traits.release_group) != local_group:
            continue
        if _release_key(title) == local_key:
            continue
        if _same_arr_scope(local_traits, remote_traits):
            tracker = str(decision.get("tracker") or best.get("tracker") or "").strip()
            reason = "Arr found a same-group release in the same scope with a different release title."
            if tracker:
                reason = f"Arr found a same-group release on {tracker} in the same scope with a different release title."
            return [
                _evidence(
                    kind="same_group_arr_title_mismatch",
                    scope="arr_title",
                    confidence="high",
                    source="Discovarr",
                    value=local_title,
                    expected=title,
                    reason=reason,
                    tracker=tracker,
                    local_title=local_title,
                    remote_title=title,
                    release_group=local_traits.release_group,
                    local_key=local_key,
                    remote_key=_release_key(title),
                    local_scope=_scope_payload(local_traits),
                    remote_scope=_scope_payload(remote_traits),
                )
            ]
    return []


def _video_files(media: Mapping[str, Any]) -> List[Dict[str, Any]]:
    files = media.get("video_files") if isinstance(media.get("video_files"), list) else []
    return [dict(item) for item in files if isinstance(item, Mapping)]


def _root_from_files(files: Sequence[Mapping[str, Any]]) -> str:
    roots = []
    for file_info in files:
        parts = PurePosixPath(str(file_info.get("name") or "")).parts
        if len(parts) > 1:
            roots.append(parts[0])
    if roots and len(set(roots)) == 1:
        return roots[0]
    return ""


def _is_extra_path(value: str) -> bool:
    return bool(_EXTRA_PATH_RE.search(str(value or "")))


def _first_structured_content_name(
    mapped_root: str,
    root_name: str,
    files: Sequence[Mapping[str, Any]],
    placeholder_value: str,
) -> str:
    candidates = [mapped_root, root_name]
    candidates.extend(_stem(PurePosixPath(str(file_info.get("name") or "")).name) for file_info in files)
    placeholder_key = _release_key(placeholder_value)
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        if _release_key(text) == placeholder_key:
            continue
        if parse_release_traits(text).is_comparable:
            return text
    return ""


def _stem(value: str) -> str:
    name = PurePosixPath(str(value or "")).name
    suffix = PurePosixPath(name).suffix.lower()
    return name[: -len(suffix)] if suffix in VIDEO_EXTENSIONS else name


def _tokens_with_empty(stem: str) -> List[str]:
    return re.split(r"[._\s-]", str(stem or ""))


def _human_tokens(stem: str) -> List[str]:
    tokens = _tokens_with_empty(stem)
    for index, token in enumerate(tokens):
        if _TECHNICAL_TOKEN_RE.match(token) or re.match(r"^S\d{1,2}$", token, flags=re.IGNORECASE):
            if index > 0:
                return tokens[:index]
    return tokens


def _has_empty_human_token(stem: str) -> bool:
    tokens = _human_tokens(stem)
    return "" in tokens[1:-1]


def _looks_random_basename(stem: str) -> bool:
    text = str(stem or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{8,24}", text):
        return False
    if not (re.search(r"[a-z]", text) and re.search(r"[A-Z]", text) and re.search(r"\d", text)):
        return False
    counts = {char: text.count(char) for char in set(text)}
    entropy = -sum((count / len(text)) * math.log2(count / len(text)) for count in counts.values())
    return entropy >= 3.0


def _technical_signature(traits: Any) -> str:
    values = [
        traits.resolution,
        traits.source_tag or traits.rip_type,
        traits.source_provider,
        traits.audio_format,
        _format_channels(traits.audio_channels),
        traits.codec,
        traits.release_group,
    ]
    cleaned = [str(value) for value in values if str(value or "").strip()]
    if len(cleaned) < 3:
        return ""
    return "|".join(cleaned)


def _has_majority_outlier(groups: Mapping[str, Sequence[str]]) -> bool:
    counts = sorted((len(values) for values in groups.values()), reverse=True)
    return bool(counts and counts[0] >= 2 and counts[-1] == 1)


def _same_arr_scope(local: Any, remote: Any) -> bool:
    if local.season != remote.season:
        return False
    if local.episode != remote.episode:
        if not (local.season_pack and remote.season_pack):
            return False
    for field in ("resolution", "source_tag", "source_provider", "rip_type"):
        left = str(getattr(local, field, "") or "")
        right = str(getattr(remote, field, "") or "")
        if left and right and left.lower() != right.lower():
            return False
    return True


def _scope_payload(traits: Any) -> Dict[str, Any]:
    return {
        "season": traits.season,
        "episode": traits.episode,
        "season_pack": bool(traits.season_pack),
        "resolution": traits.resolution,
        "source_tag": traits.source_tag,
        "source_provider": traits.source_provider,
        "rip_type": traits.rip_type,
    }


def _release_key(value: str) -> str:
    stem = _stem(str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", stem.lower())


def _placeholder_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _policy_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _format_channels(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return ""
    return f"{amount:.1f}" if amount else ""


def _evidence(
    *,
    kind: str,
    scope: str,
    confidence: str,
    source: str,
    value: str = "",
    expected: str = "",
    reason: str,
    **extra: Any,
) -> Dict[str, Any]:
    payload = {
        "kind": kind,
        "scope": scope,
        "confidence": confidence,
        "source": source,
        "value": value,
        "expected": expected,
        "reason": reason,
    }
    payload.update({key: value for key, value in extra.items() if value not in ("", None, [], {})})
    return payload


def _dedupe_evidence(evidence: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for item in evidence:
        key = (str(item.get("kind") or ""), str(item.get("scope") or ""), str(item.get("value") or ""))
        if key[0]:
            deduped[key] = dict(item)
    return list(deduped.values())
