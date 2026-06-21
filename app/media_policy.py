from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.check_results import empty_check_results, merge_check_results
from app.media_identity import analyze_media_payloads, extract_release_group, media_display_fields_from_files


VIDEO_EXTENSIONS = {".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}


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


def build_media_manual_result(verdict: str, reason: str, files: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    status = "error" if verdict in {"mediainfo_unavailable", "mediainfo_missing", "no_video_files"} else "manual_review"
    return {
        "version": 1,
        "source": "mediainfo",
        "status": status,
        "media_status": "error",
        "verdict": verdict,
        "reason": reason,
        "release_title": "",
        "release_group": "",
        "confirmed_tags": [],
        "custom_formats": [],
        "title_tags": [],
        "media_tags": [],
        "title_tag_matches": [],
        "issues": [
            {
                "severity": "ERROR",
                "key": verdict,
                "message": reason,
                "file": "",
                "tags": [],
            }
        ],
        "video_files": video_file_payloads(files),
        "mediainfo_files": [],
        "flags": [
            {
                "key": verdict,
                "label": "MediaInfo Error",
                "severity": "blocker",
                "detail": reason,
            }
        ],
    }


def analyze_mediainfo(
    *,
    item_name: str,
    files: Sequence[Mapping[str, Any]],
    mediainfo_payloads: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    video_files = video_file_payloads(files)
    expected_root = _torrent_root_name(files) or item_name
    media_result = analyze_media_payloads(
        release_title=expected_root or item_name,
        media_files=video_files,
        mediainfo_payloads=mediainfo_payloads,
    )
    media_files = list(media_result.get("mediainfo_files") or [])
    release_group = str(media_result.get("release_group") or extract_release_group(expected_root or item_name))
    status = str(media_result.get("status") or "passed")
    issue_keys = {str(issue.get("key") or "") for issue in media_result.get("issues", []) if isinstance(issue, Mapping)}
    if not video_files:
        verdict = "no_video_files"
        status = "error"
    elif not mediainfo_payloads:
        verdict = "mediainfo_missing"
        status = "error"
    elif status != "passed":
        verdict = "media_error"
    else:
        verdict = "mediainfo_passed"
    reason = str(media_result.get("reason") or "QUI MediaInfo matches the torrent release traits.")
    if status == "passed" and media_result.get("media_status") == "confirmed":
        reason = "QUI MediaInfo matches the torrent release traits."
    base_result = {
        **media_result,
        "version": 1,
        "source": "mediainfo",
        "status": status,
        "verdict": verdict,
        "reason": reason,
        "release_title": expected_root,
        "release_group": release_group,
        "complete_names": [item["basename"] for item in video_files],
        "video_files": video_files,
        "mediainfo_files": media_files,
        "flags": list(media_result.get("flags") or []),
        "torrent_root": expected_root,
    }
    mismatch_keys = {"resolution_mismatch", "video_codec_mismatch", "audio_codec_mismatch", "audio_channels_mismatch"}
    if issue_keys.intersection(mismatch_keys):
        for issue in media_result.get("issues", []):
            if isinstance(issue, Mapping) and str(issue.get("key") or "") in mismatch_keys:
                base_result["reason"] = str(issue.get("message") or base_result["reason"])
                break
    return base_result


def merge_mediainfo_provider_results(
    primary: Mapping[str, Any],
    supplemental: Mapping[str, Any],
    *,
    supplemental_label: str = "Local MediaInfo",
) -> Dict[str, Any]:
    result = dict(primary)
    supplemental_files = [
        item for item in supplemental.get("mediainfo_files", []) if isinstance(item, Mapping)
    ] if isinstance(supplemental, Mapping) else []
    if not supplemental_files:
        return result

    result["mediainfo_providers"] = {
        "primary": str(primary.get("provider") or "qui"),
        "supplemental": str(supplemental.get("provider") or "local"),
    }
    result["supplemental_mediainfo_files"] = [dict(item) for item in supplemental_files]

    primary_files = [
        item for item in primary.get("mediainfo_files", [])
        if isinstance(item, Mapping)
    ] if isinstance(primary.get("mediainfo_files"), list) else []
    remaining_issues: List[Dict[str, Any]] = []
    resolved_issues: List[Dict[str, Any]] = []
    for issue in primary.get("issues", []) if isinstance(primary.get("issues"), list) else []:
        if not isinstance(issue, Mapping):
            continue
        if _issue_confirmed_by_media_sources(issue, primary_files, supplemental_files):
            resolved = dict(issue)
            resolved["resolved_by"] = supplemental_label
            resolved_issues.append(resolved)
            continue
        remaining_issues.append(dict(issue))

    remaining_issues.extend(_provider_disagreement_issues(primary, supplemental, supplemental_label))
    if resolved_issues:
        result["resolved_mediainfo_issues"] = resolved_issues
    result["issues"] = remaining_issues
    _refresh_media_result_status(result, primary)
    result.update(
        media_display_fields_from_files(
            str(result.get("release_title") or primary.get("release_title") or supplemental.get("release_title") or ""),
            [
                *primary_files,
                *supplemental_files,
            ],
            suppressed_labels=_provider_disagreement_labels(primary, supplemental),
        )
    )
    return result


def _issue_confirmed_by_media_sources(
    issue: Mapping[str, Any],
    primary_files: Sequence[Mapping[str, Any]],
    supplemental_files: Sequence[Mapping[str, Any]],
) -> bool:
    key = str(issue.get("key") or "")
    issue_file = str(issue.get("file") or "")
    primary_matches = _matching_mediainfo_files(issue_file, primary_files)
    supplemental_matches = _matching_mediainfo_files(issue_file, supplemental_files)
    if not supplemental_matches:
        return False

    source_matches = [*primary_matches, *supplemental_matches]
    hdr_formats = _merged_hdr_formats(source_matches)
    if key == "dolby_vision_missing":
        return "Dolby Vision" in hdr_formats
    if key == "hdr10_missing":
        return bool(hdr_formats.intersection({"HDR10", "HDR10+"}))
    if key == "hdr10plus_missing":
        return "HDR10+" in hdr_formats
    if key == "dv_hdr10_fallback_missing":
        return "Dolby Vision" in hdr_formats and bool(hdr_formats.intersection({"HDR10", "HDR10+"}))
    if key == "audio_object_missing":
        objects = _merged_audio_objects(supplemental_matches)
        message = str(issue.get("message") or "")
        return any(audio_object in objects and audio_object in message for audio_object in objects)
    return False


def _matching_mediainfo_files(issue_file: str, files: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    if not issue_file:
        return list(files)
    issue_name = PurePosixPath(issue_file).name
    return [
        item
        for item in files
        if str(item.get("name") or "") == issue_file
        or PurePosixPath(str(item.get("name") or "")).name == issue_name
    ]


def _merged_hdr_formats(files: Sequence[Mapping[str, Any]]) -> set[str]:
    formats: set[str] = set()
    for item in files:
        traits = item.get("traits") if isinstance(item.get("traits"), Mapping) else {}
        formats.update(str(value) for value in traits.get("hdr_formats", []) if str(value or ""))
    return formats


def _merged_audio_objects(files: Sequence[Mapping[str, Any]]) -> set[str]:
    objects: set[str] = set()
    for item in files:
        traits = item.get("traits") if isinstance(item.get("traits"), Mapping) else {}
        objects.update(str(value) for value in traits.get("audio_objects", []) if str(value or ""))
    return objects


def _provider_disagreement_issues(
    primary: Mapping[str, Any],
    supplemental: Mapping[str, Any],
    supplemental_label: str,
) -> List[Dict[str, Any]]:
    primary_files = {
        int(item.get("index") or 0): item
        for item in primary.get("mediainfo_files", [])
        if isinstance(item, Mapping)
    } if isinstance(primary.get("mediainfo_files"), list) else {}
    supplemental_files = {
        int(item.get("index") or 0): item
        for item in supplemental.get("mediainfo_files", [])
        if isinstance(item, Mapping)
    } if isinstance(supplemental.get("mediainfo_files"), list) else {}
    issues: List[Dict[str, Any]] = []
    for index, primary_file in primary_files.items():
        supplemental_file = supplemental_files.get(index)
        if not supplemental_file:
            continue
        primary_traits = primary_file.get("traits") if isinstance(primary_file.get("traits"), Mapping) else {}
        supplemental_traits = supplemental_file.get("traits") if isinstance(supplemental_file.get("traits"), Mapping) else {}
        disagreements = []
        for field, label in (
            ("resolution", "resolution"),
            ("codec", "video codec"),
            ("audio_channels", "audio channels"),
        ):
            left = primary_traits.get(field)
            right = supplemental_traits.get(field)
            if left and right and left != right:
                disagreements.append(f"{label}: QUI={left}, {supplemental_label}={right}")
        left_audio = str(primary_traits.get("audio_format") or "")
        right_audio = str(supplemental_traits.get("audio_format") or "")
        if left_audio and right_audio and _audio_provider_family(left_audio) != _audio_provider_family(right_audio):
            disagreements.append(f"audio format: QUI={left_audio}, {supplemental_label}={right_audio}")
        if disagreements:
            issues.append(
                {
                    "severity": "ERROR",
                    "key": "mediainfo_provider_disagreement",
                    "message": "MediaInfo providers disagree on " + "; ".join(disagreements) + ".",
                    "file": str(primary_file.get("name") or supplemental_file.get("name") or ""),
                    "index": index,
                    "tags": [],
                }
            )
    return issues


def _provider_disagreement_labels(primary: Mapping[str, Any], supplemental: Mapping[str, Any]) -> List[str]:
    primary_files = {
        int(item.get("index") or 0): item
        for item in primary.get("mediainfo_files", [])
        if isinstance(item, Mapping)
    } if isinstance(primary.get("mediainfo_files"), list) else {}
    supplemental_files = {
        int(item.get("index") or 0): item
        for item in supplemental.get("mediainfo_files", [])
        if isinstance(item, Mapping)
    } if isinstance(supplemental.get("mediainfo_files"), list) else {}
    labels: List[str] = []
    for index, primary_file in primary_files.items():
        supplemental_file = supplemental_files.get(index)
        if not supplemental_file:
            continue
        primary_traits = primary_file.get("traits") if isinstance(primary_file.get("traits"), Mapping) else {}
        supplemental_traits = supplemental_file.get("traits") if isinstance(supplemental_file.get("traits"), Mapping) else {}
        for field in ("resolution", "codec", "audio_channels"):
            left = primary_traits.get(field)
            right = supplemental_traits.get(field)
            if left and right and left != right:
                labels.extend([_provider_display_label(field, left), _provider_display_label(field, right)])
        left_audio = str(primary_traits.get("audio_format") or "")
        right_audio = str(supplemental_traits.get("audio_format") or "")
        if left_audio and right_audio and _audio_provider_family(left_audio) != _audio_provider_family(right_audio):
            labels.extend([left_audio, right_audio])
    return _dedupe_labels(labels)


def _provider_display_label(field: str, value: Any) -> str:
    if field == "audio_channels":
        try:
            return f"{float(value):.1f}"
        except (TypeError, ValueError):
            return str(value or "")
    return str(value or "")


def _dedupe_labels(values: Sequence[str]) -> List[str]:
    seen = set()
    labels: List[str] = []
    for value in values:
        label = str(value or "")
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def _audio_provider_family(value: str) -> str:
    text = str(value or "").replace(" Atmos", "").strip()
    if text == "HE-AAC":
        return "AAC"
    return text


def _refresh_media_result_status(result: Dict[str, Any], primary: Mapping[str, Any]) -> None:
    severities = {str(issue.get("severity") or "") for issue in result.get("issues", []) if isinstance(issue, Mapping)}
    result["status"] = "manual_review" if "ERROR" in severities else "passed"
    result["media_status"] = "error" if "ERROR" in severities else ("warning" if "WARNING" in severities else "confirmed")
    result["verdict"] = "media_error" if result["status"] != "passed" else (
        "media_warning" if "WARNING" in severities else "mediainfo_passed"
    )
    if result["status"] == "passed":
        result["reason"] = "MediaInfo providers match the torrent release traits."
    elif result.get("issues"):
        first = result["issues"][0]
        result["reason"] = str(first.get("message") or primary.get("reason") or "MediaInfo identity check failed.")
    result["flags"] = [
        {
            "key": str(issue.get("key") or "media_issue"),
            "label": "MediaInfo Error" if str(issue.get("severity") or "") == "ERROR" else "MediaInfo Warning",
            "severity": "blocker" if str(issue.get("severity") or "") == "ERROR" else "warning",
            "detail": str(issue.get("message") or ""),
        }
        for issue in result.get("issues", [])
        if isinstance(issue, Mapping) and str(issue.get("severity") or "") in {"ERROR", "WARNING"}
    ]


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
    if candidate_trackers and not normalized_group:
        existing_flags.append(
            {
                "key": "missing_release_group",
                "label": "Missing release group",
                "severity": "warning",
                "detail": "No release group could be confidently parsed; review before upload.",
            }
        )
        policy_result = {
            "version": 1,
            "release_group": release_group,
            "candidate_trackers": [],
            "blocked_trackers": [],
            "decisions": [
                {
                    "tracker": tracker,
                    "status": "manual_review",
                    "reason": "No release group could be confidently parsed.",
                    "banned_match": "",
                    "rank": None,
                }
                for tracker in candidate_trackers
            ],
        }
        return (
            "manual_review",
            "manual_review",
            "No release group could be confidently parsed; review before upload.",
            policy_result,
            _dedupe_flags(existing_flags),
        )

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


def _candidate_trackers(tracker_results: Mapping[str, Sequence[str]], arr_results: Mapping[str, Any]) -> List[str]:
    decisions = arr_results.get("decisions") if isinstance(arr_results, Mapping) else None
    if isinstance(decisions, list):
        return [
            str(decision.get("tracker"))
            for decision in decisions
            if isinstance(decision, Mapping) and decision.get("status") == "candidate" and str(decision.get("tracker"))
        ]
    return [str(tracker) for tracker in tracker_results.get("passed", []) if str(tracker)]


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
    if status == "skipped":
        return "no_remaining_valid_targets"
    return status or "unknown"


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
