from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.arr_compare import parse_release_traits


VIDEO_EXTENSIONS = {".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}


def empty_check_results() -> Dict[str, Any]:
    return {
        "version": 1,
        "nfo": {},
        "ua": {},
        "arr": {},
        "release_group_policy": {},
        "flags": [],
    }


def merge_check_results(existing: Any, **updates: Any) -> Dict[str, Any]:
    payload = existing if isinstance(existing, dict) else {}
    result = empty_check_results()
    for key, value in payload.items():
        if key in result:
            result[key] = value
        else:
            result[key] = value
    for key, value in updates.items():
        result[key] = value
    return result


def nfo_file_candidates(files: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for index, file_info in enumerate(files):
        name = str(file_info.get("name") or "")
        if PurePosixPath(name).suffix.lower() == ".nfo":
            item = dict(file_info)
            item["index"] = int(file_info.get("index", index) or index)
            candidates.append(item)
    return candidates


def video_file_payloads(files: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    videos: List[Dict[str, Any]] = []
    for index, file_info in enumerate(files):
        name = str(file_info.get("name") or "")
        if PurePosixPath(name).suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        videos.append(
            {
                "index": int(file_info.get("index", index) or index),
                "name": name,
                "basename": PurePosixPath(name).name,
                "size": int(file_info.get("size") or 0),
            }
        )
    return videos


def build_nfo_manual_result(verdict: str, reason: str, files: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "version": 1,
        "status": "manual_review",
        "verdict": verdict,
        "reason": reason,
        "nfo_file": None,
        "release_title": "",
        "release_group": "",
        "complete_names": [],
        "video_files": video_file_payloads(files),
        "flags": [],
        "excerpt": "",
    }


def analyze_nfo(
    *,
    item_name: str,
    files: Sequence[Mapping[str, Any]],
    nfo_file: Mapping[str, Any],
    nfo_text: str,
) -> Dict[str, Any]:
    video_files = video_file_payloads(files)
    release_title = parse_nfo_release_title(nfo_text)
    complete_names = parse_nfo_complete_names(nfo_text)
    release_group = extract_release_group(release_title or item_name)
    nfo_payload = {
        "index": int(nfo_file.get("index") or 0),
        "name": str(nfo_file.get("name") or ""),
        "size": int(nfo_file.get("size") or 0),
    }
    base_result = {
        "version": 1,
        "status": "passed",
        "verdict": "nfo_passed",
        "reason": "NFO title matches the torrent release.",
        "nfo_file": nfo_payload,
        "release_title": release_title,
        "release_group": release_group,
        "complete_names": complete_names,
        "video_files": video_files,
        "flags": [],
        "excerpt": _excerpt(nfo_text),
    }
    if not release_title:
        return {
            **base_result,
            "status": "manual_review",
            "verdict": "nfo_unreadable",
            "reason": "Whackamole could not find a release title in the NFO.",
        }

    expected_root = _torrent_root_name(files) or item_name
    if _release_key(release_title) != _release_key(expected_root):
        return {
            **base_result,
            "status": "manual_review",
            "verdict": "nfo_mismatch",
            "reason": f"NFO release title does not match torrent root: {release_title} != {expected_root}",
            "torrent_root": expected_root,
        }

    trait_mismatch = _trait_mismatch(release_title, expected_root)
    if trait_mismatch:
        return {
            **base_result,
            "status": "manual_review",
            "verdict": "nfo_mismatch",
            "reason": trait_mismatch,
            "torrent_root": expected_root,
        }

    flags = renamed_file_flags(complete_names, video_files)
    return {
        **base_result,
        "torrent_root": expected_root,
        "flags": flags,
    }


def parse_nfo_release_title(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if _clean_heading(line) != "release":
            continue
        for candidate in lines[index + 1 :]:
            stripped = candidate.strip()
            if not stripped or _is_box_line(stripped):
                continue
            return stripped
    return ""


def parse_nfo_complete_names(text: str) -> List[str]:
    names: List[str] = []
    for line in text.splitlines():
        match = re.match(r"\s*Complete name\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        if match:
            names.append(PurePosixPath(match.group(1).strip()).name)
    return list(dict.fromkeys(names))


def decode_nfo_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_release_group(value: str) -> str:
    name = PurePosixPath(str(value or "")).name
    name = re.sub(r"\.(?:mkv|mp4|avi|m2ts|ts|mov|wmv|nfo)$", "", name, flags=re.IGNORECASE)
    match = re.search(r"-([A-Za-z0-9][A-Za-z0-9._]{1,})$", name)
    return match.group(1) if match else ""


def apply_release_group_policy(
    *,
    tracker_results: Mapping[str, Sequence[str]],
    arr_results: Mapping[str, Any],
    release_group: str,
    tracker_policies: Mapping[str, Mapping[str, Sequence[str]]],
    flags: Sequence[Mapping[str, Any]],
    item_name: str,
) -> Tuple[str, str, str, Dict[str, Any], List[Dict[str, Any]]]:
    existing_flags = [dict(flag) for flag in flags]
    candidate_trackers = _candidate_trackers(tracker_results, arr_results)
    decisions = []
    allowed: List[str] = []
    blocked: List[str] = []
    normalized_group = _policy_key(release_group)

    for tracker in candidate_trackers:
        policy = tracker_policies.get(tracker) or {}
        banned = [str(item) for item in policy.get("banned_release_groups", []) if str(item).strip()]
        ranked = [str(item) for item in policy.get("ranked_release_groups", []) if str(item).strip()]
        banned_match = _match_policy_group(release_group, banned)
        rank = _rank_policy_group(release_group, ranked)
        if normalized_group and banned_match:
            blocked.append(tracker)
            decisions.append(
                {
                    "tracker": tracker,
                    "status": "blocked",
                    "reason": f"{release_group} is banned on {tracker}.",
                    "banned_match": banned_match,
                    "rank": rank,
                }
            )
        else:
            allowed.append(tracker)
            decisions.append(
                {
                    "tracker": tracker,
                    "status": "candidate",
                    "reason": "Release group policy allows this tracker.",
                    "banned_match": "",
                    "rank": rank,
                }
            )

    if blocked:
        existing_flags.append(
            {
                "key": "banned_release_group",
                "label": "Banned release group",
                "severity": "blocker",
                "detail": f"{release_group or 'Unknown group'} is banned on: {', '.join(blocked)}",
            }
        )

    existing_flags.extend(_possible_renamed_flags(arr_results, release_group, item_name))
    policy_result = {
        "version": 1,
        "release_group": release_group,
        "candidate_trackers": allowed,
        "blocked_trackers": blocked,
        "decisions": decisions,
    }
    if candidate_trackers and not allowed:
        return (
            "blocked",
            "banned_release_group",
            f"{release_group or 'This release group'} is banned on every otherwise valid tracker.",
            policy_result,
            _dedupe_flags(existing_flags),
        )
    if allowed:
        return (
            "candidate",
            "candidate",
            f"Valid upload candidate on: {', '.join(allowed)}",
            policy_result,
            _dedupe_flags(existing_flags),
        )
    status = str(arr_results.get("status") or "")
    reason = str(arr_results.get("reason") or "")
    return status, _verdict_for_status(status), reason, policy_result, _dedupe_flags(existing_flags)


def renamed_file_flags(expected_names: Sequence[str], video_files: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    expected = {PurePosixPath(name).name for name in expected_names if str(name).strip()}
    actual = {PurePosixPath(str(item.get("basename") or item.get("name") or "")).name for item in video_files}
    if not expected or expected.intersection(actual):
        return []
    return [
        {
            "key": "renamed_files",
            "label": "Renamed files",
            "severity": "warning",
            "detail": "The video filenames do not match the NFO Complete name entries.",
        }
    ]


def _candidate_trackers(tracker_results: Mapping[str, Sequence[str]], arr_results: Mapping[str, Any]) -> List[str]:
    decisions = arr_results.get("decisions") if isinstance(arr_results, Mapping) else None
    if isinstance(decisions, list):
        return [
            str(decision.get("tracker"))
            for decision in decisions
            if isinstance(decision, Mapping) and decision.get("status") == "candidate" and str(decision.get("tracker"))
        ]
    return [str(tracker) for tracker in tracker_results.get("passed", []) if str(tracker)]


def _possible_renamed_flags(arr_results: Mapping[str, Any], release_group: str, item_name: str) -> List[Dict[str, Any]]:
    if not release_group:
        return []
    local_key = _release_key(item_name)
    for decision in arr_results.get("decisions", []) if isinstance(arr_results, Mapping) else []:
        if not isinstance(decision, Mapping):
            continue
        best = decision.get("best_release")
        if not isinstance(best, Mapping):
            continue
        title = str(best.get("title") or "")
        if _policy_key(extract_release_group(title)) == _policy_key(release_group) and _release_key(title) != local_key:
            return [
                {
                    "key": "possible_renamed_release",
                    "label": "Possible renamed release",
                    "severity": "warning",
                    "detail": "Arr found a same-group release with a different release title.",
                }
            ]
    return []


def _trait_mismatch(nfo_title: str, torrent_root: str) -> str:
    nfo_traits = parse_release_traits(nfo_title)
    torrent_traits = parse_release_traits(torrent_root)
    checks = [
        ("resolution", nfo_traits.resolution, torrent_traits.resolution),
        ("source", nfo_traits.source if nfo_traits.source != "other" else "", torrent_traits.source if torrent_traits.source != "other" else ""),
        ("audio format", nfo_traits.audio_format, torrent_traits.audio_format),
        ("codec", nfo_traits.codec, torrent_traits.codec),
    ]
    if nfo_traits.audio_channels and torrent_traits.audio_channels:
        checks.append(("audio channels", str(nfo_traits.audio_channels), str(torrent_traits.audio_channels)))
    if nfo_traits.hdr_rank or torrent_traits.hdr_rank:
        checks.append(("HDR", str(nfo_traits.hdr_rank), str(torrent_traits.hdr_rank)))
    if nfo_traits.season is not None and torrent_traits.season is not None:
        checks.append(("season", str(nfo_traits.season), str(torrent_traits.season)))
    if nfo_traits.episode is not None and torrent_traits.episode is not None:
        checks.append(("episode", str(nfo_traits.episode), str(torrent_traits.episode)))
    if nfo_traits.season_pack != torrent_traits.season_pack and nfo_traits.season is not None and torrent_traits.season is not None:
        return "NFO season-pack/episode identity does not match the torrent title."
    for label, left, right in checks:
        if left and right and left != right:
            return f"NFO {label} does not match the torrent title."
    return ""


def _torrent_root_name(files: Sequence[Mapping[str, Any]]) -> str:
    roots = []
    for file_info in files:
        name = str(file_info.get("name") or "")
        parts = PurePosixPath(name).parts
        if len(parts) > 1:
            roots.append(parts[0])
    if roots and len(set(roots)) == 1:
        return roots[0]
    return ""


def _release_key(value: str) -> str:
    name = PurePosixPath(str(value or "")).name
    name = re.sub(r"\.(?:mkv|mp4|avi|m2ts|ts|mov|wmv|nfo)$", "", name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _policy_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _match_policy_group(release_group: str, values: Sequence[str]) -> str:
    wanted = _policy_key(release_group)
    if not wanted:
        return ""
    for value in values:
        if _policy_key(value) == wanted:
            return str(value)
    return ""


def _rank_policy_group(release_group: str, ranked: Sequence[str]) -> Optional[int]:
    wanted = _policy_key(release_group)
    for index, value in enumerate(ranked, start=1):
        if _policy_key(value) == wanted:
            return index
    return None


def _verdict_for_status(status: str) -> str:
    if status == "candidate":
        return "candidate"
    if status == "blocked":
        return "not_upgrade"
    if status == "manual_review":
        return "manual_review"
    return status or "unknown"


def _clean_heading(line: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", line.lower())


def _is_box_line(line: str) -> bool:
    return bool(re.fullmatch(r"[+\-|=\s]+", line)) or _clean_heading(line) in {"", "release"}


def _excerpt(text: str, limit: int = 4000) -> str:
    text = text.strip()
    return text[:limit]


def _dedupe_flags(flags: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for flag in flags:
        key = str(flag.get("key") or "")
        detail = str(flag.get("detail") or "")
        dedupe_key = (key, detail)
        if not key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(dict(flag))
    return result
