from __future__ import annotations

import json
import os
import time
from hmac import compare_digest
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette import status

from app.clients import ProfilarrClient, QuiClient, RadarrClient, SonarrClient, UploadAssistantClient
from app.check_results import CheckResults
from app.config import (
    AppConfig,
    ConfigManager,
    SecretStore,
    default_tracker_policies,
    format_path_mappings,
    join_csv,
    parse_csv,
    parse_path_mappings,
)
from app.database import Database
from app.inventory import (
    PRIMARY_TRACKERS,
    coverage_for_item,
    item_inventory_meta,
    missing_primary_trackers,
)
from app.reducer import TRACKER_BUCKETS
from app.service import WhackamoleService

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
VIDEO_EXTENSIONS = {".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}
MAX_VIDEO_FILES = 200
DASHBOARD_VIEWS = {
    "active": ["queued", "deferred", "checking", "error"],
    "candidates": ["candidate"],
    "covered": ["covered"],
    "blocked": ["blocked"],
    "manual": ["manual_review"],
    "errors": ["error"],
    "baseline": ["baseline"],
    "inventory": ["inventory"],
    "ignored": ["ignored"],
    "all": [],
}
FILTERABLE_VIEWS = {"baseline", "candidates", "covered", "blocked", "manual"}
DASHBOARD_TABS = [
    ("active", "Active", ["queued", "deferred", "checking", "error"]),
    ("candidates", "Candidates", ["candidate"]),
    ("covered", "Covered", ["covered"]),
    ("blocked", "Blocked", ["blocked"]),
    ("manual", "Review", ["manual_review"]),
    ("errors", "Errors", ["error"]),
    ("baseline", "Baseline", ["baseline"]),
    ("inventory", "Inventory", ["inventory"]),
    ("ignored", "Ignored", ["ignored"]),
    ("all", "All", []),
]
MEDIA_FILTERS = [
    {"key": "movie", "label": "Movies"},
    {"key": "tv", "label": "TV shows"},
    {"key": "episode", "label": "Episodes"},
]
REASON_FILTERS = [
    {"key": "media_warning", "label": "MediaInfo warning", "applies_to": ["blocked", "manual"]},
    {"key": "media_error", "label": "MediaInfo error", "applies_to": ["manual", "errors"]},
    {"key": "arr_equal_or_better", "label": "Arr equal/better", "applies_to": ["blocked"]},
    {"key": "banned_release_group", "label": "Banned release group", "applies_to": ["blocked"]},
    {"key": "no_video", "label": "No video files", "applies_to": ["manual", "errors"]},
    {"key": "path_error", "label": "Path or mount error", "applies_to": ["manual", "errors"]},
    {"key": "ua_error", "label": "UA error", "applies_to": ["manual", "errors"]},
    {"key": "manual_review", "label": "Manual review", "applies_to": ["manual"]},
]


def _format_datetime(value: Optional[int]) -> str:
    if not value:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(value)))


def _format_bytes(value: Optional[int]) -> str:
    amount = float(value or 0)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TiB"


def _format_json(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True)
    except TypeError:
        return str(value)


templates.env.filters["datetime"] = _format_datetime
templates.env.filters["bytes"] = _format_bytes
templates.env.filters["json_pretty"] = _format_json


def _config_dir() -> str:
    return os.getenv("WHACKAMOLE_CONFIG_DIR", "/config")


def _row_dict(row: Any, coverage: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    item = dict(row)
    tracker_groups = _tracker_result_groups(item.get("tracker_results"), item.get("verdict"))
    arr_result = _arr_result(item.get("arr_results"))
    check_results = _check_results(item.get("check_results"))
    inventory_meta = item_inventory_meta(item)
    item_coverage = coverage_for_item(item, coverage or {})
    item["tracker_results"] = tracker_groups
    item["tracker_buckets"] = _tracker_bucket_items(tracker_groups)
    item["tracker_summary"] = _tracker_summary(tracker_groups)
    item["arr_result"] = arr_result
    item["arr_summary"] = _arr_summary(arr_result)
    item["check_results"] = check_results
    item["check_flags"] = _check_flags(check_results)
    item["stage_flow"] = _stage_flow(item, check_results, arr_result)
    item["inventory_meta"] = inventory_meta
    item["coverage"] = item_coverage
    item["missing_primary_trackers"] = missing_primary_trackers(item_coverage)
    item["display_status"] = _display_status(item)
    item["next_action"] = _next_action(item)
    item["valid_for_trackers"] = _valid_for_trackers(item, tracker_groups, arr_result, check_results)
    item["decision_notice"] = _decision_notice(item, check_results)
    item["reason_categories"] = _reason_categories(item, check_results, arr_result)
    item["coverage_status"] = _coverage_status(item_coverage, item["missing_primary_trackers"])
    return item


def _row_detail_dict(row: Any, coverage: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    item = _row_dict(row, coverage)
    item["video_files"] = _video_files_for_item(item)
    item["raw_payloads"] = _raw_payloads(item)
    return item


def _normalized_item(row: Any) -> Dict[str, Any]:
    item = dict(row)
    if "coverage" in item and "inventory_meta" in item and isinstance(item.get("tracker_results"), dict):
        return item
    return _row_dict(row)


def _json_object(value: Any) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _check_results(value: Any) -> Dict[str, Any]:
    parsed = _json_object(value)
    return CheckResults.from_any(parsed).to_dict()


def _check_flags(check_results: Dict[str, Any]) -> List[Dict[str, Any]]:
    flags = check_results.get("flags")
    if not isinstance(flags, list):
        return []
    return [flag for flag in flags if isinstance(flag, dict) and str(flag.get("key") or "")]


def _display_status(item: Dict[str, Any]) -> Dict[str, str]:
    value = str(item.get("status") or "")
    stage = str(item.get("check_stage") or "")
    next_check_at = item.get("next_check_at")
    now = int(time.time())
    stage_labels = {
        "media": "Checking MediaInfo",
        "path": "Mapping path",
        "ua": "Running UA",
        "arr": "Checking ARR",
        "policy": "Applying policy",
        "done": "Checked",
        "interrupted": "Interrupted",
    }
    if value == "checking":
        return {"label": stage_labels.get(stage, "Checking"), "group": "running", "detail": "In progress"}
    if value in {"queued", "deferred"}:
        return {"label": "Queued", "group": "queued", "detail": "Waiting for a check slot"}
    if value == "candidate":
        return {"label": "Ready", "group": "ready", "detail": "Upload candidate"}
    if value == "covered":
        return {"label": "Covered", "group": "covered", "detail": "Coverage resolved in QUI"}
    if value == "blocked":
        return {"label": "Blocked", "group": "covered", "detail": "No upload needed"}
    if value == "manual_review":
        return {"label": "Needs Review", "group": "attention", "detail": "Manual decision needed"}
    if value == "error":
        try:
            retry_waiting = bool(next_check_at and int(next_check_at) > now)
        except (TypeError, ValueError):
            retry_waiting = False
        return {
            "label": "Retry Scheduled" if retry_waiting else "Error",
            "group": "error",
            "detail": "Waiting for retry window" if retry_waiting else "Retry due or failed",
        }
    if value == "baseline":
        return {"label": "Baseline", "group": "neutral", "detail": "Inventory backlog"}
    if value == "inventory":
        return {"label": "Inventory", "group": "neutral", "detail": "Coverage signal"}
    if value == "ignored":
        return {"label": "Ignored", "group": "muted", "detail": "Hidden from active work"}
    return {"label": value.replace("_", " ").title() if value else "Unknown", "group": "neutral", "detail": ""}


def _next_action(item: Dict[str, Any]) -> str:
    status_value = str(item.get("status") or "")
    if status_value == "candidate":
        return "Review candidate"
    if status_value == "covered":
        return "Coverage resolved"
    if status_value == "blocked":
        return "Review coverage"
    if status_value == "manual_review":
        return "Inspect issue"
    if status_value == "error":
        try:
            if item.get("next_check_at") and int(item["next_check_at"]) > int(time.time()):
                return "Waiting for retry"
        except (TypeError, ValueError):
            pass
        return "Retry check"
    if status_value in {"baseline", "queued", "deferred"}:
        return "Run check"
    if status_value == "checking":
        return "In progress"
    if status_value == "inventory":
        return "Coverage only"
    if status_value == "ignored":
        return "Ignored"
    return "Open"


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


def _decision_notice(item: Dict[str, Any], check_results: Dict[str, Any]) -> str:
    flags = _check_flags(check_results)
    for severity in ("blocker", "error", "warning"):
        for flag in flags:
            if str(flag.get("severity") or "").lower() == severity:
                return str(flag.get("detail") or flag.get("message") or flag.get("label") or "").strip()
    return str(item.get("reason") or item.get("display_status", {}).get("detail") or "").strip()


def _reason_categories(item: Dict[str, Any], check_results: Dict[str, Any], arr_result: Dict[str, Any]) -> List[str]:
    categories: List[str] = []
    status_value = str(item.get("status") or "").lower()
    verdict = str(item.get("verdict") or "").lower()
    reason = str(item.get("reason") or "").lower()
    flags = _check_flags(check_results)
    flag_keys = {str(flag.get("key") or "").lower() for flag in flags}
    flag_severities = {str(flag.get("severity") or "").lower() for flag in flags}
    media = check_results.get("media") if isinstance(check_results.get("media"), dict) else {}

    if "warning" in flag_severities or "media_warning" in verdict or str(media.get("verdict") or "").lower() == "media_warning":
        categories.append("media_warning")
    if "error" in flag_severities or "media_error" in verdict or str(media.get("verdict") or "").lower() == "media_error":
        categories.append("media_error")
    if "equal-or-better" in reason or "equal-or-better" in json.dumps(arr_result).lower():
        categories.append("arr_equal_or_better")
    if "banned_release_group" in verdict or "banned_release_group" in flag_keys:
        categories.append("banned_release_group")
    if "no_video" in verdict or "video files" in reason:
        categories.append("no_video")
    if "path" in verdict or "path" in reason or "mount" in reason:
        categories.append("path_error")
    if "ua_error" in verdict:
        categories.append("ua_error")
    if status_value == "manual_review" or "manual_review" in verdict:
        categories.append("manual_review")
    return list(dict.fromkeys(categories))


def _coverage_status(coverage: List[Dict[str, Any]], missing: List[str]) -> Dict[str, List[Dict[str, str]]]:
    found_default = [
        {"key": str(item.get("key") or ""), "label": str(item.get("label") or item.get("key") or "")}
        for item in coverage
        if item.get("primary")
    ]
    found_other = [
        {"key": str(item.get("key") or ""), "label": str(item.get("label") or item.get("key") or "")}
        for item in coverage
        if not item.get("primary")
    ]
    missing_default = [{"key": tracker, "label": tracker} for tracker in missing]
    return {"found_default": found_default, "found_other": found_other, "missing_default": missing_default}


def _dedupe_trackers(trackers: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(str(tracker).upper() for tracker in trackers if str(tracker).strip()))


def _raw_payloads(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw_torrent = _json_object(item.get("raw_torrent"))
    checks = item.get("check_results") if isinstance(item.get("check_results"), dict) else {}
    media = checks.get("media") if isinstance(checks.get("media"), dict) else {}
    raw_mediainfo = media.get("raw_mediainfo_payloads") if isinstance(media, dict) else []
    diagnostics = checks.get("diagnostics") if isinstance(checks.get("diagnostics"), dict) else {}
    return {
        "ua_log": {
            "title": "UA log",
            "kind": "text",
            "available": bool(str(item.get("ua_log") or "")),
            "content": str(item.get("ua_log") or "No UA log captured yet."),
        },
        "qui": {
            "title": "Raw QUI torrent",
            "kind": "json",
            "available": bool(raw_torrent),
            "content": raw_torrent or {"message": "No raw QUI torrent payload recorded."},
        },
        "mediainfo": {
            "title": "Raw QUI MediaInfo",
            "kind": "json",
            "available": bool(raw_mediainfo),
            "content": raw_mediainfo or {"message": "Raw MediaInfo will be available after this item is rechecked."},
        },
        "arr": {
            "title": "Raw ARR result",
            "kind": "json",
            "available": bool(item.get("arr_result")),
            "content": item.get("arr_result") or {"message": "No ARR result recorded."},
        },
        "diagnostics": {
            "title": "Check diagnostics",
            "kind": "json",
            "available": bool(diagnostics),
            "content": diagnostics or {"message": "No diagnostics recorded."},
        },
    }


def _api_item_summary(row: Any) -> Dict[str, Any]:
    item = _normalized_item(row)
    return {
        "id": item["id"],
        "instance_id": item["instance_id"],
        "hash": item["hash"],
        "name": item["name"],
        "category": item["category"],
        "tags": item["tags"],
        "content_path": item["content_path"],
        "mapped_path": item["mapped_path"],
        "status": item["status"],
        "verdict": item["verdict"],
        "reason": item["reason"],
        "size": item["size"],
        "added_on": item["added_on"],
        "completion_on": item["completion_on"],
        "discovered_at": item["discovered_at"],
        "updated_at": item["updated_at"],
        "last_checked_at": item["last_checked_at"],
        "next_check_at": item["next_check_at"],
        "attempt_count": item["attempt_count"],
        "check_stage": item.get("check_stage", ""),
        "display_status": item["display_status"],
        "next_action": item["next_action"],
        "flags": item["check_flags"],
        "stage_flow": item["stage_flow"],
        "baseline": bool(item["baseline"]),
        "ignored_reason": item["ignored_reason"],
        "tracker_results": item["tracker_results"],
        "tracker_summary": item["tracker_summary"],
        "arr_summary": item["arr_summary"],
        "inventory_meta": item["inventory_meta"],
        "coverage": item["coverage"],
        "missing_primary_trackers": item["missing_primary_trackers"],
        "valid_for_trackers": item["valid_for_trackers"],
        "decision_notice": item["decision_notice"],
        "reason_categories": item["reason_categories"],
        "coverage_status": item["coverage_status"],
    }


def _api_item_detail(row: Any) -> Dict[str, Any]:
    item = _normalized_item(row)
    summary = _api_item_summary(row)
    raw_torrent = _json_object(item.get("raw_torrent"))
    ua = {
        "session_id": item["ua_session_id"],
        "args": item["ua_args"],
        "log": item["ua_log"],
        "tracker_results": item["tracker_results"],
        "tracker_summary": item["tracker_summary"],
    }
    arr = item["arr_result"]
    stored_checks = item["check_results"]
    checks = {
        "version": stored_checks.get("version") or 1,
        "media": stored_checks.get("media") or {},
        "nfo": stored_checks.get("nfo") or {},
        "ua": {**(stored_checks.get("ua") if isinstance(stored_checks.get("ua"), dict) else {}), **ua},
        "arr": stored_checks.get("arr") or arr,
        "release_group_policy": stored_checks.get("release_group_policy") or {},
        "coverage_resolution": stored_checks.get("coverage_resolution") or {},
        "flags": item["check_flags"],
        "diagnostics": stored_checks.get("diagnostics") or {"stages": [], "last_error": {}},
    }
    summary.update(
        {
            "raw_torrent": raw_torrent,
            "video_files": item.get("video_files") or _video_files_for_item(item),
            "ua": ua,
            "arr": arr,
            "checks": checks,
        }
    )
    return summary


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
        path = Path(root)
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
            files.append(_video_file_payload(child, path))
            if len(files) >= MAX_VIDEO_FILES:
                result["truncated"] = True
                break
        result["files"] = files
        if not files:
            result["message"] = "No video files found at this path."
        return result
    except OSError as exc:
        result["message"] = f"Could not inspect path: {exc}"
        return result


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


def _tracker_result_groups(value: Any, verdict: Any = "") -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {bucket: [] for bucket in TRACKER_BUCKETS}
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        parsed = []

    if isinstance(parsed, dict):
        raw_groups = parsed.get("groups") if isinstance(parsed.get("groups"), dict) else parsed
        for bucket in TRACKER_BUCKETS:
            values = raw_groups.get(bucket, [])
            if isinstance(values, list):
                groups[bucket] = [str(item) for item in values if str(item).strip()]
        return groups

    if isinstance(parsed, list):
        legacy_bucket = _legacy_tracker_bucket(str(verdict or ""))
        groups[legacy_bucket] = [str(item) for item in parsed if str(item).strip()]
    return groups


def _legacy_tracker_bucket(verdict: str) -> str:
    if verdict == "dupe":
        return "dupe"
    if verdict == "skipped":
        return "skipped"
    if verdict in {"error", "http_error", "ua_error", "path_mapping"}:
        return "error"
    return "passed"


def _tracker_bucket_items(groups: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    return {
        bucket: [
            {
                "name": tracker,
            }
            for tracker in groups.get(bucket, [])
        ]
        for bucket in TRACKER_BUCKETS
    }


def _tracker_summary(groups: Dict[str, List[str]]) -> str:
    labels = {
        "passed": "Missing/upload-worthy",
        "covered": "Covered in QUI",
        "dupe": "Dupes",
        "skipped": "Skipped",
        "error": "Errors",
    }
    parts = [
        f"{labels[bucket]}: {', '.join(groups[bucket])}"
        for bucket in TRACKER_BUCKETS
        if groups.get(bucket)
    ]
    return " | ".join(parts)


def _stage_flow(item: Dict[str, Any], check_results: Dict[str, Any], arr_result: Dict[str, Any]) -> List[Dict[str, str]]:
    status_value = str(item.get("status") or "")
    stage = str(item.get("check_stage") or "")
    final_statuses = {"candidate", "covered", "blocked", "manual_review", "error", "ignored", "inventory", "baseline"}
    media_done = bool(check_results.get("media"))
    ua_done = bool(check_results.get("ua"))
    arr_done = bool(check_results.get("arr") or arr_result)

    def state_for(key: str, done: bool) -> str:
        if key == "queue" and status_value in {"queued", "deferred"}:
            return "active"
        if key == "media" and stage == "media":
            return "active"
        if key == "ua" and stage == "ua":
            return "active"
        if key == "arr" and stage in {"arr", "policy"}:
            return "active"
        if done or status_value in final_statuses:
            return "complete" if done or key == "queue" else "pending"
        return "pending"

    final_state = "complete" if status_value in final_statuses else ("active" if stage == "done" else "pending")
    return [
        {"key": "queue", "label": "Queue", "state": state_for("queue", status_value not in {"queued", "deferred"})},
        {"key": "media", "label": "MediaInfo", "state": state_for("media", media_done)},
        {"key": "ua", "label": "UA", "state": state_for("ua", ua_done)},
        {"key": "arr", "label": "ARR", "state": state_for("arr", arr_done)},
        {"key": "final", "label": _status_label(status_value), "state": final_state},
    ]


def _status_label(value: str) -> str:
    labels = {
        "candidate": "Ready",
        "covered": "Covered",
        "blocked": "Blocked",
        "manual_review": "Review",
        "error": "Error",
        "ignored": "Ignored",
        "inventory": "Inventory",
        "baseline": "Baseline",
        "checking": "Checking",
        "queued": "Queued",
        "deferred": "Queued",
    }
    return labels.get(value, value.replace("_", " ").title() if value else "Final")


def _arr_result(value: Any) -> Dict[str, Any]:
    return _json_object(value)


def _arr_summary(result: Dict[str, Any]) -> str:
    decisions = result.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        return ""
    valid = [str(item.get("tracker")) for item in decisions if item.get("status") == "candidate"]
    covered = [str(item.get("tracker")) for item in decisions if item.get("status") == "covered"]
    blocked = [str(item.get("tracker")) for item in decisions if item.get("status") == "blocked"]
    manual = [str(item.get("tracker")) for item in decisions if item.get("status") == "manual_review"]
    parts = []
    if valid:
        parts.append(f"Valid: {', '.join(valid)}")
    if covered:
        parts.append(f"Covered: {', '.join(covered)}")
    if blocked:
        parts.append(f"Equal/better exists: {', '.join(blocked)}")
    if manual:
        parts.append(f"Manual review: {', '.join(manual)}")
    return " | ".join(parts)


def _as_int(value: Any, default: int, minimum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _as_time_value(value: str, default: str) -> str:
    try:
        hour_text, minute_text = str(value or "").split(":", 1)
        hour = max(0, min(23, int(hour_text)))
        minute = max(0, min(59, int(minute_text)))
        return f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        return default or "05:00"


def _secret_state(secrets: SecretStore) -> Dict[str, bool]:
    return {
        "whackamole_api_token": secrets.has("whackamole_api_token"),
        "qui_api_key": secrets.has("qui_api_key"),
        "ua_bearer_token": secrets.has("ua_bearer_token"),
        "sonarr_api_key": secrets.has("sonarr_api_key"),
        "radarr_api_key": secrets.has("radarr_api_key"),
        "easycross_api_key": secrets.has("easycross_api_key"),
        "profilarr_api_key": secrets.has("profilarr_api_key"),
    }


def _config_context(request: Request, message: str = "", probe_results: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    cfg = request.app.state.config_manager.load()
    secrets = request.app.state.secrets
    return {
        **_shell_context(request, section="settings"),
        "request": request,
        "cfg": cfg,
        "secrets": _secret_state(secrets),
        "path_mappings": format_path_mappings(cfg.path_mappings),
        "exclude_category_terms": join_csv(cfg.watch.exclude_category_terms),
        "exclude_tag_terms": join_csv(cfg.watch.exclude_tag_terms),
        "error_backoff_minutes": join_csv([str(item) for item in cfg.safety.error_backoff_minutes]),
        "tracker_policies": _tracker_policy_context(cfg),
        "message": message,
        "probe_results": probe_results or [],
    }


def _shell_context(request: Request, section: str = "", view: str = "", q: str = "") -> Dict[str, Any]:
    service_snapshot = request.app.state.service.snapshot()
    counts = request.app.state.db.status_counts()
    return {
        "section": section,
        "service": service_snapshot,
        "counts": counts,
        "dashboard_nav": _dashboard_nav(counts, service_snapshot, view=view, q=q),
        "search_query": q,
        "show_dashboard_search": section == "dashboard",
    }


def _dashboard_nav(counts: Dict[str, int], service: Dict[str, Any], view: str = "", q: str = "") -> List[Dict[str, Any]]:
    rows = []
    queue = service.get("queue") if isinstance(service.get("queue"), dict) else {}
    for key, label, statuses in DASHBOARD_TABS:
        if key == "active":
            total = int(queue.get("active") or 0)
        elif statuses:
            total = sum(int(counts.get(status, 0)) for status in statuses)
        else:
            total = sum(int(value or 0) for value in counts.values())
        rows.append(
            {
                "key": key,
                "label": label,
                "total": total,
                "href": _dashboard_url(key, q=q),
                "selected": key == view,
            }
        )
    return rows


def _tracker_policy_context(cfg: AppConfig) -> List[Dict[str, str]]:
    policies = cfg.tracker_policies if isinstance(cfg.tracker_policies, dict) else default_tracker_policies()
    rows = []
    for tracker in default_tracker_policies().keys():
        policy = policies.get(tracker) if isinstance(policies.get(tracker), dict) else {}
        rows.append(
            {
                "tracker": tracker,
                "banned": join_csv([str(item) for item in policy.get("banned_release_groups", [])]),
                "ranked": join_csv([str(item) for item in policy.get("ranked_release_groups", [])]),
            }
        )
    return rows


def _coverage_for_rows(db: Database, rows: Sequence[Any]) -> Dict[str, List[Dict[str, Any]]]:
    group_keys = [str(dict(row).get("inventory_group_key") or item_inventory_meta(dict(row)).get("group_key") or "") for row in rows]
    return db.coverage_for_group_keys(group_keys)


def _coverage_for_row(db: Database, row: Any) -> Dict[str, List[Dict[str, Any]]]:
    return _coverage_for_rows(db, [row])


def _filtered_rows(
    db: Database,
    statuses: Sequence[str],
    limit: int,
    offset: int = 0,
    media: Any = "all",
    missing: Optional[Iterable[str]] = None,
    valid_for: Optional[Iterable[str]] = None,
    reasons: Optional[Iterable[str]] = None,
    hide_any_primary: bool = False,
    due_errors_only: bool = False,
    q: str = "",
) -> tuple[List[Any], int, Dict[str, List[Dict[str, Any]]]]:
    rows = db.list_items_filtered(
        statuses,
        limit=limit,
        offset=offset,
        media=media,
        missing=missing,
        valid_for=valid_for,
        reasons=reasons,
        hide_any_primary=hide_any_primary,
        due_errors_only=due_errors_only,
        q=q,
    )
    total = db.count_items_filtered(
        statuses,
        media=media,
        missing=missing,
        valid_for=valid_for,
        reasons=reasons,
        hide_any_primary=hide_any_primary,
        due_errors_only=due_errors_only,
        q=q,
    )
    return rows, total, _coverage_for_rows(db, rows)


def _dashboard_url(
    view: str,
    page: int = 1,
    media: Any = "all",
    missing: Optional[Iterable[str]] = None,
    valid_for: Optional[Iterable[str]] = None,
    reasons: Optional[Iterable[str]] = None,
    hide_any_primary: bool = False,
    message: str = "",
    q: str = "",
) -> str:
    params: Dict[str, Any] = {"view": view, "page": max(1, page)}
    if q:
        params["q"] = q
    if view in FILTERABLE_VIEWS:
        selected_media = _selected_media_filter(media)
        if selected_media:
            params["media"] = selected_media
        selected_missing = [tracker for tracker in (missing or []) if tracker]
        if selected_missing:
            params["missing"] = selected_missing
        selected_valid = [tracker for tracker in (valid_for or []) if tracker]
        if selected_valid:
            params["valid_for"] = selected_valid
        selected_reasons = [reason for reason in (reasons or []) if reason]
        if selected_reasons:
            params["reason"] = selected_reasons
        if hide_any_primary:
            params["hide_any_primary"] = "true"
    if message:
        params["message"] = message
    return f"/?{urlencode(params, doseq=True)}"


def _safe_local_redirect(value: str, fallback: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def _selected_media_filter(media: Any) -> List[str]:
    raw_values = media if isinstance(media, (list, tuple, set)) else [media]
    selected = []
    for value in raw_values:
        cleaned = str(value or "").strip().lower()
        if cleaned and cleaned != "all":
            selected.append(cleaned)
    return list(dict.fromkeys(selected))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.config_manager = ConfigManager(_config_dir())
    app.state.secrets = SecretStore(_config_dir())
    app.state.db = Database(str(Path(_config_dir()) / "whackamole.db"))
    app.state.db.backfill_inventory_columns()
    app.state.service = WhackamoleService(app.state.config_manager, app.state.secrets, app.state.db)
    app.state.service.start()
    try:
        yield
    finally:
        await app.state.service.stop()


app = FastAPI(title="Whackamole", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    view: str = "active",
    media: Optional[List[str]] = Query(None),
    missing: Optional[List[str]] = Query(None),
    valid_for: Optional[List[str]] = Query(None),
    reason: Optional[List[str]] = Query(None),
    hide_any_primary: bool = False,
    page: int = Query(1, ge=1),
    q: str = "",
    message: str = "",
) -> HTMLResponse:
    selected = view if view in DASHBOARD_VIEWS else "active"
    search_query = q.strip()
    media_values = _selected_media_filter(media or [])
    missing_values = missing or []
    valid_for_values = [tracker.upper() for tracker in (valid_for or []) if tracker.strip()]
    reason_values = [value.strip().lower() for value in (reason or []) if value.strip()]
    limit = 100 if selected in {"baseline", "inventory"} else 150
    offset = (page - 1) * limit
    filter_media = media_values if selected in FILTERABLE_VIEWS else []
    filter_missing = missing_values if selected in FILTERABLE_VIEWS else []
    filter_valid_for = valid_for_values if selected in FILTERABLE_VIEWS else []
    filter_reasons = reason_values if selected in FILTERABLE_VIEWS else []
    filter_hide_any = hide_any_primary if selected in FILTERABLE_VIEWS else False
    rows, filtered_total, coverage = _filtered_rows(
        request.app.state.db,
        DASHBOARD_VIEWS[selected],
        limit=limit,
        offset=offset,
        media=filter_media,
        missing=filter_missing,
        valid_for=filter_valid_for,
        reasons=filter_reasons,
        hide_any_primary=filter_hide_any,
        due_errors_only=selected == "active",
        q=search_query,
    )
    service_snapshot = request.app.state.service.snapshot()
    context = {
        **_shell_context(request, section="dashboard", view=selected, q=search_query),
        "request": request,
        "items": [_row_dict(row, coverage) for row in rows],
        "view": selected,
        "counts": request.app.state.db.status_counts(),
        "service": service_snapshot,
        "message": message,
        "primary_trackers": PRIMARY_TRACKERS,
        "media_filter_options": MEDIA_FILTERS,
        "reason_filter_options": REASON_FILTERS,
        "filterable_views": FILTERABLE_VIEWS,
        "dashboard_filters": {
            "media": filter_media,
            "missing": [tracker.upper() for tracker in filter_missing],
            "valid_for": filter_valid_for,
            "reasons": filter_reasons,
            "hide_any_primary": filter_hide_any,
            "filtered_total": filtered_total,
            "displayed": len(rows),
            "limit": limit,
            "view": selected,
            "q": search_query,
            "label": {
                "baseline": "baseline",
                "candidates": "candidate",
                "covered": "covered",
                "blocked": "blocked",
                "manual": "manual review",
            }.get(selected, selected.replace("_", " ")),
        },
        "pagination": {
            "page": page,
            "limit": limit,
            "offset": offset,
            "total": filtered_total,
            "start": offset + 1 if filtered_total else 0,
            "end": offset + len(rows),
            "prev_url": _dashboard_url(
                selected, page - 1, filter_media, filter_missing, filter_valid_for, filter_reasons, filter_hide_any, q=search_query
            )
            if page > 1
            else "",
            "next_url": _dashboard_url(
                selected, page + 1, filter_media, filter_missing, filter_valid_for, filter_reasons, filter_hide_any, q=search_query
            )
            if offset + len(rows) < filtered_total
            else "",
        },
        "current_url": _dashboard_url(selected, page, filter_media, filter_missing, filter_valid_for, filter_reasons, filter_hide_any, q=search_query),
    }
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/items/{item_id}", response_class=HTMLResponse)
async def item_detail(request: Request, item_id: int) -> HTMLResponse:
    row = request.app.state.db.get_item(item_id)
    if row is None:
        return templates.TemplateResponse(
            request,
            "item.html",
            {**_shell_context(request, section="items"), "request": request, "item": None},
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "item.html",
        {
            **_shell_context(request, section="items"),
            "request": request,
            "item": _row_detail_dict(row, _coverage_for_row(request.app.state.db, row)),
        },
    )


@app.post("/items/{item_id}/recheck")
async def recheck_item(item_id: int, return_to: str = Form("")) -> RedirectResponse:
    app.state.db.requeue(item_id)
    return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/baseline/recheck-filtered")
async def recheck_filtered_baseline(
    media: Optional[List[str]] = Form(None),
    missing: Optional[List[str]] = Form(None),
    valid_for: Optional[List[str]] = Form(None),
    reason: Optional[List[str]] = Form(None),
    hide_any_primary: Optional[str] = Form(None),
    q: str = Form(""),
) -> RedirectResponse:
    return await recheck_filtered_items("baseline", media, missing, valid_for, reason, hide_any_primary, q)


@app.post("/items/recheck-filtered")
async def recheck_filtered_items(
    view: str = Form("baseline"),
    media: Optional[List[str]] = Form(None),
    missing: Optional[List[str]] = Form(None),
    valid_for: Optional[List[str]] = Form(None),
    reason: Optional[List[str]] = Form(None),
    hide_any_primary: Optional[str] = Form(None),
    q: str = Form(""),
) -> RedirectResponse:
    selected = view if view in FILTERABLE_VIEWS else "baseline"
    media_values = _selected_media_filter(media or [])
    missing_values = missing or []
    valid_for_values = [tracker.upper() for tracker in (valid_for or []) if tracker.strip()]
    reason_values = [value.strip().lower() for value in (reason or []) if value.strip()]
    hide_any = hide_any_primary == "true"
    search_query = q.strip()
    label = {
        "baseline": "baseline",
        "candidates": "candidate",
        "covered": "covered",
        "blocked": "blocked",
        "manual": "manual review",
    }[selected]
    reason = (
        "Bulk recheck requested from baseline filtered set"
        if selected == "baseline"
        else f"Bulk recheck requested from {label} filtered set"
    )
    queued = app.state.db.bulk_requeue_filtered(
        DASHBOARD_VIEWS[selected],
        media=media_values,
        missing=missing_values,
        valid_for=valid_for_values,
        reasons=reason_values,
        hide_any_primary=hide_any,
        reason=reason,
        q=search_query,
    )
    return RedirectResponse(
        url=_dashboard_url(
            selected,
            page=1,
            media=media_values,
            missing=missing_values,
            valid_for=valid_for_values,
            reasons=reason_values,
            hide_any_primary=hide_any,
            message=f"Queued {queued} item{'s' if queued != 1 else ''}.",
            q=search_query,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/items/{item_id}/ignore")
async def ignore_item(item_id: int) -> RedirectResponse:
    app.state.db.ignore(item_id)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/maintenance/pause")
async def pause_maintenance(return_to: str = Form("/")) -> RedirectResponse:
    app.state.service.manual_pause()
    return RedirectResponse(url=_safe_local_redirect(return_to, "/"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/maintenance/resume")
async def resume_maintenance(return_to: str = Form("/")) -> RedirectResponse:
    app.state.service.manual_resume()
    return RedirectResponse(url=_safe_local_redirect(return_to, "/"), status_code=status.HTTP_303_SEE_OTHER)


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "config.html", _config_context(request))


@app.post("/config", response_class=HTMLResponse)
async def save_config(
    request: Request,
    qui_url: str = Form(""),
    qui_instance_id: str = Form("1"),
    qui_page_limit: str = Form("200"),
    qui_api_key: str = Form(""),
    clear_qui_api_key: Optional[str] = Form(None),
    ua_url: str = Form(""),
    ua_tmp_path: str = Form("/ua-tmp"),
    ua_timeout: str = Form("3600"),
    ua_bearer_token: str = Form(""),
    clear_ua_bearer_token: Optional[str] = Form(None),
    path_mappings: str = Form(""),
    exclude_category_terms: str = Form(""),
    exclude_tag_terms: str = Form(""),
    process_existing_on_first_run: Optional[str] = Form(None),
    poll_interval_seconds: str = Form("60"),
    max_queue_size: str = Form("250"),
    max_concurrent_ua_jobs: str = Form("1"),
    min_seconds_between_ua_jobs: str = Form("120"),
    arr_search_timeout_seconds: str = Form("300"),
    recheck_cooldown_hours: str = Form("24"),
    max_error_retries: str = Form("3"),
    error_backoff_minutes: str = Form("15, 60, 360"),
    maintenance_enabled: Optional[str] = Form(None),
    maintenance_timezone: str = Form("Europe/London"),
    maintenance_start_time: str = Form("05:00"),
    maintenance_lead_minutes: str = Form("30"),
    sonarr_url: str = Form(""),
    sonarr_api_key: str = Form(""),
    clear_sonarr_api_key: Optional[str] = Form(None),
    radarr_url: str = Form(""),
    radarr_api_key: str = Form(""),
    clear_radarr_api_key: Optional[str] = Form(None),
    easycross_url: str = Form(""),
    easycross_api_key: str = Form(""),
    clear_easycross_api_key: Optional[str] = Form(None),
    profilarr_url: str = Form(""),
    profilarr_api_key: str = Form(""),
    clear_profilarr_api_key: Optional[str] = Form(None),
    whackamole_api_token: str = Form(""),
    clear_whackamole_api_token: Optional[str] = Form(None),
    policy_dp_banned: Optional[str] = Form(None),
    policy_dp_ranked: Optional[str] = Form(None),
    policy_ulcx_banned: Optional[str] = Form(None),
    policy_ulcx_ranked: Optional[str] = Form(None),
    policy_ihd_banned: Optional[str] = Form(None),
    policy_ihd_ranked: Optional[str] = Form(None),
) -> HTMLResponse:
    manager: ConfigManager = request.app.state.config_manager
    secrets: SecretStore = request.app.state.secrets
    cfg: AppConfig = manager.load()

    cfg.qui.url = qui_url.strip().rstrip("/")
    cfg.qui.instance_id = _as_int(qui_instance_id, cfg.qui.instance_id, minimum=1)
    cfg.qui.page_limit = _as_int(qui_page_limit, cfg.qui.page_limit, minimum=1)
    cfg.upload_assistant.url = ua_url.strip().rstrip("/")
    cfg.upload_assistant.tmp_path = ua_tmp_path.strip() or "/ua-tmp"
    cfg.upload_assistant.request_timeout_seconds = _as_int(ua_timeout, cfg.upload_assistant.request_timeout_seconds, minimum=60)
    cfg.path_mappings = parse_path_mappings(path_mappings)

    cfg.watch.exclude_category_terms = parse_csv(exclude_category_terms)
    cfg.watch.exclude_tag_terms = parse_csv(exclude_tag_terms)
    cfg.watch.process_existing_on_first_run = process_existing_on_first_run == "on"

    cfg.safety.poll_interval_seconds = _as_int(poll_interval_seconds, cfg.safety.poll_interval_seconds, minimum=15)
    cfg.safety.max_queue_size = _as_int(max_queue_size, cfg.safety.max_queue_size, minimum=1)
    cfg.safety.max_concurrent_ua_jobs = _as_int(max_concurrent_ua_jobs, cfg.safety.max_concurrent_ua_jobs, minimum=1)
    cfg.safety.min_seconds_between_ua_jobs = _as_int(
        min_seconds_between_ua_jobs,
        cfg.safety.min_seconds_between_ua_jobs,
        minimum=0,
    )
    cfg.safety.arr_search_timeout_seconds = _as_int(
        arr_search_timeout_seconds,
        cfg.safety.arr_search_timeout_seconds,
        minimum=5,
    )
    cfg.safety.recheck_cooldown_hours = _as_int(recheck_cooldown_hours, cfg.safety.recheck_cooldown_hours, minimum=1)
    cfg.safety.max_error_retries = _as_int(max_error_retries, cfg.safety.max_error_retries, minimum=0)
    cfg.safety.error_backoff_minutes = [
        _as_int(item, 15, minimum=1)
        for item in parse_csv(error_backoff_minutes)
    ] or [15, 60, 360]
    cfg.maintenance.enabled = maintenance_enabled == "on"
    cfg.maintenance.timezone = maintenance_timezone.strip() or "Europe/London"
    cfg.maintenance.start_time = _as_time_value(maintenance_start_time, cfg.maintenance.start_time)
    cfg.maintenance.lead_minutes = _as_int(maintenance_lead_minutes, cfg.maintenance.lead_minutes, minimum=0)
    cfg.maintenance.resume_signal = "qui_down_up"

    cfg.sonarr.url = sonarr_url.strip().rstrip("/")
    cfg.radarr.url = radarr_url.strip().rstrip("/")
    cfg.easycross.url = easycross_url.strip().rstrip("/")
    cfg.profilarr.url = profilarr_url.strip().rstrip("/")
    policy_inputs = {
        "DP": (policy_dp_banned, policy_dp_ranked),
        "ULCX": (policy_ulcx_banned, policy_ulcx_ranked),
        "IHD": (policy_ihd_banned, policy_ihd_ranked),
    }
    existing_policies = cfg.tracker_policies if isinstance(cfg.tracker_policies, dict) else default_tracker_policies()
    cfg.tracker_policies = default_tracker_policies()
    for tracker, (banned, ranked) in policy_inputs.items():
        existing = existing_policies.get(tracker) if isinstance(existing_policies.get(tracker), dict) else {}
        cfg.tracker_policies[tracker] = {
            "banned_release_groups": parse_csv(banned) if banned is not None else list(existing.get("banned_release_groups", [])),
            "ranked_release_groups": parse_csv(ranked) if ranked is not None else list(existing.get("ranked_release_groups", [])),
        }

    _update_secret(secrets, "qui_api_key", qui_api_key, clear_qui_api_key)
    _update_secret(secrets, "ua_bearer_token", ua_bearer_token, clear_ua_bearer_token)
    _update_secret(secrets, "sonarr_api_key", sonarr_api_key, clear_sonarr_api_key)
    _update_secret(secrets, "radarr_api_key", radarr_api_key, clear_radarr_api_key)
    _update_secret(secrets, "easycross_api_key", easycross_api_key, clear_easycross_api_key)
    _update_secret(secrets, "profilarr_api_key", profilarr_api_key, clear_profilarr_api_key)
    _update_secret(secrets, "whackamole_api_token", whackamole_api_token, clear_whackamole_api_token)

    manager.save(cfg)
    return templates.TemplateResponse(request, "config.html", _config_context(request, message="Settings saved."))


@app.post("/config/probe", response_class=HTMLResponse)
async def probe_config(request: Request) -> HTMLResponse:
    cfg = request.app.state.config_manager.load()
    secrets = request.app.state.secrets
    results: List[Dict[str, str]] = []

    if cfg.qui.url:
        try:
            client = QuiClient(cfg, secrets.get("qui_api_key"))
            await client.health()
            instances = await client.list_instances() if secrets.has("qui_api_key") else []
            detail = f"Connected. {len(instances)} instance(s) visible." if instances else "Setup endpoint reachable."
            results.append({"name": "QUI", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "QUI", "state": "error", "detail": _short_error(exc)})

    if cfg.upload_assistant.url:
        try:
            client = UploadAssistantClient(cfg, secrets.get("ua_bearer_token"))
            await client.health()
            roots = await client.browse_roots() if secrets.has("ua_bearer_token") else {}
            detail = "Connected."
            if isinstance(roots, dict) and roots:
                detail = f"Connected. Browse roots: {', '.join(str(k) for k in roots.keys())}."
            results.append({"name": "Upload Assistant", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "Upload Assistant", "state": "error", "detail": _short_error(exc)})

    if cfg.sonarr.url:
        try:
            client = SonarrClient(cfg.sonarr.url, secrets.get("sonarr_api_key"), cfg.safety.arr_search_timeout_seconds)
            status_payload = await client.system_status()
            indexers = await client.list_indexers() if secrets.has("sonarr_api_key") else []
            torrent_count = sum(1 for indexer in indexers if str(indexer.get("protocol", "")).lower() == "torrent")
            detail = f"Connected to {status_payload.get('appName', 'Sonarr')}. {torrent_count} torrent indexer(s)."
            results.append({"name": "Sonarr", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "Sonarr", "state": "error", "detail": _short_error(exc)})

    if cfg.radarr.url:
        try:
            client = RadarrClient(cfg.radarr.url, secrets.get("radarr_api_key"), cfg.safety.arr_search_timeout_seconds)
            status_payload = await client.system_status()
            indexers = await client.list_indexers() if secrets.has("radarr_api_key") else []
            torrent_count = sum(1 for indexer in indexers if str(indexer.get("protocol", "")).lower() == "torrent")
            detail = f"Connected to {status_payload.get('appName', 'Radarr')}. {torrent_count} torrent indexer(s)."
            results.append({"name": "Radarr", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "Radarr", "state": "error", "detail": _short_error(exc)})

    if cfg.profilarr.url:
        try:
            client = ProfilarrClient(cfg.profilarr.url, secrets.get("profilarr_api_key"), cfg.safety.arr_search_timeout_seconds)
            await client.health()
            status_payload = await client.status() if secrets.has("profilarr_api_key") else {}
            databases = status_payload.get("databases") if isinstance(status_payload.get("databases"), list) else []
            if databases:
                counts = databases[0].get("counts") if isinstance(databases[0], dict) else {}
                detail = (
                    f"Connected to Profilarr {status_payload.get('version', '')}. "
                    f"{counts.get('customFormats', 0)} custom format(s), "
                    f"{counts.get('regularExpressions', 0)} regex pattern(s)."
                )
            else:
                detail = "Connected. Save the API key to read database status."
            results.append({"name": "Profilarr", "state": "ok", "detail": detail})
        except Exception as exc:
            results.append({"name": "Profilarr", "state": "error", "detail": _short_error(exc)})

    if not results:
        results.append({"name": "Configuration", "state": "idle", "detail": "Add URLs and saved keys before probing."})

    return templates.TemplateResponse(request, "config.html", _config_context(request, probe_results=results))


@app.get("/api/status")
async def api_status(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "service": request.app.state.service.snapshot(),
            "counts": request.app.state.db.status_counts(),
            "configured": _secret_state(request.app.state.secrets),
        }
    )


@app.post("/service-errors/clear")
async def clear_service_errors(return_to: str = Form("/")) -> RedirectResponse:
    app.state.db.clear_service_errors()
    return RedirectResponse(url=_safe_local_redirect(return_to, "/"), status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/items")
async def api_items(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_details: bool = Query(False),
    media: Optional[List[str]] = Query(None),
    missing: Optional[List[str]] = Query(None),
    valid_for: Optional[List[str]] = Query(None),
    reason: Optional[List[str]] = Query(None),
    hide_any_primary: bool = Query(False),
    q: str = Query(""),
) -> JSONResponse:
    _require_api_auth(request)
    statuses = _parse_status_filter(status_filter)
    media_values = _selected_media_filter(media or [])
    missing_values = missing or []
    valid_for_values = [tracker.upper() for tracker in (valid_for or []) if tracker.strip()]
    reason_values = [value.strip().lower() for value in (reason or []) if value.strip()]
    search_query = q.strip()
    rows, total, coverage = _filtered_rows(
        request.app.state.db,
        statuses,
        limit=limit,
        offset=offset,
        media=media_values,
        missing=missing_values,
        valid_for=valid_for_values,
        reasons=reason_values,
        hide_any_primary=hide_any_primary,
        q=search_query,
    )
    serializer = _api_item_detail if include_details else _api_item_summary
    return JSONResponse(
        {
            "items": [serializer(_row_dict(row, coverage)) for row in rows],
            "count": len(rows),
            "total": total,
            "limit": limit,
            "offset": offset,
            "status": statuses,
            "include_details": include_details,
            "media": media_values,
            "missing": [tracker.upper() for tracker in missing_values],
            "valid_for": valid_for_values,
            "reason": reason_values,
            "hide_any_primary": hide_any_primary,
            "q": search_query,
        }
    )


@app.get("/api/items/{item_id}")
async def api_item_detail(request: Request, item_id: int) -> JSONResponse:
    _require_api_auth(request)
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return JSONResponse(_api_item_detail(_row_detail_dict(row, _coverage_for_row(request.app.state.db, row))))


@app.get("/api/items/{item_id}/log")
async def api_item_log(request: Request, item_id: int) -> PlainTextResponse:
    _require_api_auth(request)
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    return PlainTextResponse(str(row["ua_log"] or ""), media_type="text/plain")


def _update_secret(secrets: SecretStore, name: str, value: str, clear: Optional[str]) -> None:
    if clear == "on":
        secrets.clear(name)
    elif value.strip():
        secrets.set(name, value.strip())


def _short_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return str(exc)[:240]


def _require_api_auth(request: Request) -> None:
    expected = request.app.state.secrets.get("whackamole_api_token")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Whackamole API token is not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or not compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _parse_status_filter(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
