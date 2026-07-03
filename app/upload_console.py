from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from app.media_policy import VIDEO_EXTENSIONS
from app.path_security import validate_media_path
from app.source_providers import extract_provider_abbreviation, extract_provider_from_release_title, provider_abbreviation_for_label
from app.srrdb import srrdb_lookup_name

MAX_VIDEO_FILES = 200
SOURCE_PROVIDER_FIELD_RE = re.compile(r"(?:site|network|source|service|provider|streaming)", re.IGNORECASE)


def _effective_status(item: Mapping[str, Any]) -> str:
    value = str(item.get("status") or "")
    if value == "rejected":
        return value
    checks = item.get("check_results") if isinstance(item.get("check_results"), dict) else {}
    decision = checks.get("decision") if isinstance(checks.get("decision"), dict) else {}
    decision_status = str(decision.get("status") or "")
    if decision_status in {"candidate", "manual_review", "blocked", "skipped", "retry", "error"}:
        return decision_status
    return value


def _can_upload(item: Mapping[str, Any]) -> bool:
    return _effective_status(item) in {"candidate", "manual_review"}


def _dedupe_trackers(trackers: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(str(tracker).upper() for tracker in trackers if str(tracker).strip()))


def _valid_for_trackers(
    item: Dict[str, Any],
    tracker_groups: Dict[str, List[str]],
    arr_result: Dict[str, Any],
    check_results: Dict[str, Any],
) -> List[str]:
    policy = check_results.get("release_group_policy") if isinstance(check_results.get("release_group_policy"), dict) else {}
    policy_candidates = policy.get("candidate_trackers") if isinstance(policy.get("candidate_trackers"), list) else []
    trackers = [str(tracker).upper() for tracker in policy_candidates if str(tracker).strip()]
    if trackers:
        return _dedupe_trackers(trackers)

    decisions = arr_result.get("decisions") if isinstance(arr_result.get("decisions"), list) else []
    trackers = [
        str(decision.get("tracker") or "").upper()
        for decision in decisions
        if isinstance(decision, dict)
        and str(decision.get("status") or "").lower() == "candidate"
        and str(decision.get("tracker") or "").strip()
    ]
    if trackers:
        return _dedupe_trackers(trackers)

    if str(item.get("status") or "") == "candidate":
        return _dedupe_trackers([str(tracker).upper() for tracker in tracker_groups.get("passed", [])])
    return []


def _folder_name_check(item: Mapping[str, Any], video_files: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    root = str(item.get("mapped_path") or item.get("content_path") or "").strip()
    if not root:
        return {
            "state": "Not Applicable",
            "group": "neutral",
            "notes": "No mapped path is recorded.",
            "blocked": False,
            "root_name": "",
            "normalized": "",
        }

    files = video_files.get("files") if isinstance(video_files, Mapping) and isinstance(video_files.get("files"), list) else []
    if len(files) == 1 and str(files[0].get("path") or "").strip():
        return {
            "state": "Pass",
            "group": "pass",
            "notes": "Single video file will be uploaded.",
            "blocked": False,
            "root_name": PurePosixPath(str(files[0].get("path") or "")).name,
            "normalized": "",
        }

    root_name = PurePosixPath(root).name.strip()
    if not root_name:
        return {
            "state": "Not Applicable",
            "group": "neutral",
            "notes": "No folder name is available.",
            "blocked": False,
            "root_name": "",
            "normalized": "",
        }
    if PurePosixPath(root_name).suffix.lower() in VIDEO_EXTENSIONS:
        return {
            "state": "Pass",
            "group": "pass",
            "notes": "Selected path is a video file.",
            "blocked": False,
            "root_name": root_name,
            "normalized": "",
        }

    normalized = srrdb_lookup_name(root_name)
    if normalized and normalized != root_name:
        warning = f"Folder name would be normalised to {normalized}."
        return {
            "state": "Warning",
            "group": "warning",
            "notes": warning,
            "blocked": False,
            "root_name": root_name,
            "normalized": normalized,
        }
    return {
        "state": "Pass",
        "group": "pass",
        "notes": root_name,
        "blocked": False,
        "root_name": root_name,
        "normalized": normalized,
    }


def _video_file_payload(path: Path, base: Path) -> Dict[str, Any]:
    try:
        relative = str(path.relative_to(base))
    except ValueError:
        relative = path.name
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "name": path.name,
        "relative_path": relative,
        "path": str(path),
        "size": size,
    }


def _video_files_for_item(item: Dict[str, Any]) -> Dict[str, Any]:
    root = str(item.get("mapped_path") or item.get("content_path") or "")
    result = {
        "path": root,
        "files": [],
        "truncated": False,
        "message": "",
    }
    if not root:
        result["message"] = "No path recorded."
        return result

    try:
        path = validate_media_path(root)
        if not path.exists():
            result["message"] = "Path is not visible inside the Whackamole container."
            return result
        if path.is_file():
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                result["files"] = [_video_file_payload(path, path.parent)]
            else:
                result["message"] = "Path is a file, but not a known video extension."
            return result
        if not path.is_dir():
            result["message"] = "Path is not a regular file or directory."
            return result

        files = []
        for child in sorted(path.rglob("*")):
            if not child.is_file() or child.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                validate_media_path(str(child.resolve(strict=False)), (path.resolve(strict=False),))
            except ValueError:
                continue
            files.append(_video_file_payload(child, path))
            if len(files) >= MAX_VIDEO_FILES:
                result["truncated"] = True
                break
        result["files"] = files
        if not files:
            result["message"] = "No video files found at this path."
        return result
    except ValueError as exc:
        result["message"] = str(exc)
        return result
    except OSError as exc:
        result["message"] = f"Could not inspect path: {exc}"
        return result


def _collect_source_provider_fields(value: Any, fields: List[str]) -> None:
    if isinstance(value, Mapping):
        field_name = str(value.get("name") or value.get("@name") or "")
        if field_name and SOURCE_PROVIDER_FIELD_RE.search(field_name) and "value" in value:
            fields.append(f"{field_name}: {value.get('value')}")
        for key, nested in value.items():
            key_text = str(key or "")
            if SOURCE_PROVIDER_FIELD_RE.search(key_text) and isinstance(nested, (str, int, float)):
                fields.append(f"{key_text}: {nested}")
            elif isinstance(nested, (Mapping, list, tuple)):
                _collect_source_provider_fields(nested, fields)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _collect_source_provider_fields(nested, fields)


def _source_provider_from_mediainfo(media: Mapping[str, Any]) -> str:
    provider = str(media.get("dashboard_source_provider") or "").strip()
    if provider:
        return provider
    fields: List[str] = []
    files = media.get("mediainfo_files") if isinstance(media.get("mediainfo_files"), list) else []
    for file_info in files:
        if not isinstance(file_info, Mapping):
            continue
        traits = file_info.get("traits") if isinstance(file_info.get("traits"), Mapping) else {}
        provider = provider_abbreviation_for_label(str(traits.get("source_provider") or ""))
        if provider:
            return provider
        _collect_source_provider_fields(file_info, fields)
    payloads = media.get("raw_mediainfo_payloads") if isinstance(media.get("raw_mediainfo_payloads"), list) else []
    for payload in payloads:
        _collect_source_provider_fields(payload, fields)
    return extract_provider_abbreviation(*fields)


def _source_provider_for_item(item: Dict[str, Any]) -> str:
    title_provider = extract_provider_from_release_title(str(item.get("name") or ""))
    if title_provider:
        return title_provider
    nfo = item.get("nfo_info") if isinstance(item.get("nfo_info"), dict) else {}
    provider = str(nfo.get("provider_abbreviation") or "").strip()
    if provider:
        return provider
    checks = item.get("check_results") if isinstance(item.get("check_results"), dict) else {}
    media = checks.get("media") if isinstance(checks.get("media"), dict) else {}
    return _source_provider_from_mediainfo(media)


def _is_web_release(item: Dict[str, Any]) -> bool:
    traits = item.get("discovarr_local_traits") if isinstance(item.get("discovarr_local_traits"), dict) else {}
    if str(traits.get("source") or "").lower() == "web":
        return True
    if str(traits.get("rip_type") or "").lower() in {"web", "web-dl", "webrip"}:
        return True
    if str(traits.get("source_tag") or "").lower() in {"web", "web-dl", "webrip"}:
        return True
    values = " ".join(
        str(value or "")
        for value in (
            item.get("name"),
            traits.get("rip_type"),
            traits.get("source_tag"),
            traits.get("source"),
            traits.get("source_label"),
            traits.get("type"),
        )
    )
    return bool(re.search(r"\b(?:WEB[-_. ]?DL|WEBDL|WEBRIP|WEB[-_. ]?RIP)\b", values, re.IGNORECASE))


def _release_title_has_provider(title: str, provider: str) -> bool:
    if not title or not provider:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(provider)}(?![A-Za-z0-9])", title, re.IGNORECASE))


def _with_unattended_arg(args: str) -> str:
    value = str(args or "").strip()
    if re.search(r"(^|\s)--unattended(\s|$)", value):
        return value
    return f"{value} --unattended".strip()


def _upload_payload_args(payload: Any, console: Mapping[str, Any]) -> str:
    raw_args = payload.get("args") if isinstance(payload, dict) else None
    args = str(raw_args or "").strip()
    if args:
        return args
    return str(console.get("args") or "").strip()


def _upload_console_args(item: Dict[str, Any]) -> str:
    parts: List[str] = []
    trackers = [str(tracker).strip().lower() for tracker in item.get("valid_for_trackers") or [] if str(tracker).strip()]
    if trackers:
        parts.append(f"--trackers {','.join(dict.fromkeys(trackers))}")
    provider = _source_provider_for_item(item)
    if provider and _is_web_release(item) and not _release_title_has_provider(str(item.get("name") or ""), provider):
        parts.append(f"--service {provider}")
    return " ".join(parts)


def _upload_console_path(item: Dict[str, Any]) -> Dict[str, Any]:
    video_files = item.get("video_files") if isinstance(item.get("video_files"), dict) else {}
    files = video_files.get("files") if isinstance(video_files.get("files"), list) else []
    root = str(item.get("mapped_path") or item.get("content_path") or "").strip()
    warnings: List[str] = []
    folder_check = _folder_name_check(item, video_files)
    if len(files) == 1 and str(files[0].get("path") or "").strip():
        selected = str(files[0]["path"])
        label = str(files[0].get("relative_path") or files[0].get("name") or selected)
        kind = "file"
    else:
        selected = root
        label = root
        kind = "folder" if len(files) != 1 else "path"
        if kind == "folder" and folder_check.get("group") == "warning":
            warnings.append(str(folder_check.get("notes") or "Folder name needs review before uploading."))
    if not selected:
        warnings.append("No mapped Upload Assistant path is recorded for this item.")
    else:
        try:
            if not Path(selected).exists():
                warnings.append("Path is not visible inside the Whackamole container; Upload Assistant may still see it if mappings differ.")
        except OSError as exc:
            warnings.append(f"Could not inspect path visibility: {str(exc)[:160]}")
    return {"path": selected, "label": label, "kind": kind, "warnings": warnings, "blocked": False}


def _upload_console_context(item: Dict[str, Any]) -> Dict[str, Any]:
    path_info = _upload_console_path(item)
    args = _upload_console_args(item)
    warnings = list(path_info.get("warnings") or [])
    blocked = not bool(item.get("can_upload", _can_upload(item)))
    if blocked:
        warnings.append("This item is not uploadable in its current status.")
    if _is_web_release(item) and not _source_provider_for_item(item):
        warnings.append("Source Missing: detected WEB-DL/WEBRip but no streaming service provider is known yet.")
    if any(str(flag.get("key") or "").lower() == "possible_renamed_release" for flag in item.get("check_flags") or [] if isinstance(flag, dict)):
        warnings.append("Possible renamed release: review the tracker title before uploading.")
    return {
        "path": path_info["path"],
        "path_label": path_info["label"],
        "path_kind": path_info["kind"],
        "args": args,
        "warnings": list(dict.fromkeys(warnings)),
        "blocked": blocked,
    }


def resolve_path_and_args(
    item: Mapping[str, Any],
    tracker_groups: Dict[str, List[str]],
    arr_result: Dict[str, Any],
    check_results: Dict[str, Any],
) -> Tuple[str, str]:
    """Resolve the Upload Assistant path and unattended args for an item mid-pipeline.

    Mirrors what _upload_console_context computes for the manual queue/execute
    endpoints, but works only from data already available during the check
    pipeline (no DB coverage reads, no nfo_info) so it can be called directly
    from the service layer. As a result, --service provider detection falls
    back to release-title and check_results["media"] parsing only (no NFO
    content), which is an acceptable accuracy tradeoff for automatic queuing.
    """
    working = dict(item)
    working["check_results"] = check_results
    working["video_files"] = _video_files_for_item(working)
    working["valid_for_trackers"] = _valid_for_trackers(working, tracker_groups, arr_result, check_results)
    path_info = _upload_console_path(working)
    args = _upload_console_args(working)
    return path_info["path"], _with_unattended_arg(args)
