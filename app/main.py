from __future__ import annotations

import json
import os
import re
import time
from hmac import compare_digest
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import httpx
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette import status

from app.clients import ProfilarrClient, QuiClient, RadarrClient, SonarrClient, UploadAssistantClient
from app.check_results import CheckResults, merge_check_results
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
from app.database import REPORT_STATES, Database
from app.inventory import (
    PRIMARY_TRACKERS,
    coverage_for_item,
    item_inventory_meta,
    missing_primary_trackers,
)
from app.media_identity import ensure_media_display_fields
from app.reducer import TRACKER_BUCKETS
from app.rules import rule_catalogue, ruleset_changelog
from app.service import WhackamoleService
from app.source_providers import extract_provider_abbreviation, extract_provider_from_release_title, provider_abbreviation_for_label
from app.srrdb import srrdb_lookup_name
from app.ua_execution import UaExecutionCoordinator, UploadConsoleManager

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
VIDEO_EXTENSIONS = {".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".ts", ".webm", ".wmv"}
NFO_EXTENSIONS = {".nfo"}
MAX_VIDEO_FILES = 200
MAX_NFO_BYTES = 262144
SOURCE_PROVIDER_FIELD_RE = re.compile(r"(?:site|network|source|service|provider|streaming)", re.IGNORECASE)
DASHBOARD_VIEWS = {
    "active": ["queued", "deferred", "checking", "retry"],
    "candidates": ["candidate"],
    "covered": ["covered"],
    "rejected": ["rejected"],
    "blocked": ["blocked"],
    "skipped": ["skipped"],
    "manual": ["manual_review"],
    "errors": ["error"],
    "baseline": ["baseline"],
    "inventory": ["inventory"],
    "ignored": ["ignored"],
    "all": [],
}
FILTERABLE_VIEWS = {"baseline", "candidates", "covered", "rejected", "blocked", "skipped", "manual"}
DASHBOARD_TABS = [
    ("active", "Active", ["queued", "deferred", "checking", "retry"]),
    ("candidates", "Candidates", ["candidate"]),
    ("covered", "Covered", ["covered"]),
    ("rejected", "Rejected", ["rejected"]),
    ("blocked", "Blocked", ["blocked"]),
    ("skipped", "Skipped", ["skipped"]),
    ("manual", "Review", ["manual_review"]),
    ("errors", "Errors", ["error"]),
    ("baseline", "Baseline", ["baseline"]),
    ("inventory", "Inventory", ["inventory"]),
    ("ignored", "Ignored", ["ignored"]),
    ("all", "All", []),
]
IMPORT_PAGE_SIZE = 50
IMPORT_VIEW_STATUSES = {
    "queue": ["pending", "running"],
    "error": ["error"],
    "complete": ["complete"],
    "cancelled": ["cancelled"],
}
IMPORT_TABS = [
    {"key": "queue", "label": "Queue", "show_count": True},
    {"key": "error", "label": "Error", "show_count": True},
    {"key": "complete", "label": "Complete", "show_count": False},
    {"key": "cancelled", "label": "Cancelled", "show_count": False},
]
MEDIA_FILTERS = [
    {"key": "movie", "label": "Movies"},
    {"key": "tv", "label": "TV shows"},
    {"key": "episode", "label": "Episodes"},
]
REASON_FILTERS = [
    {"key": "media_warning", "label": "MediaInfo warning", "applies_to": ["blocked", "manual"]},
    {"key": "media_error", "label": "MediaInfo error", "applies_to": ["manual", "errors"]},
    {"key": "arr_equal_or_better", "label": "Arr equal/better", "applies_to": ["skipped"]},
    {"key": "banned_release_group", "label": "Banned release group", "applies_to": ["blocked"]},
    {"key": "srrdb_filename_mismatch", "label": "srrDB filename mismatch", "applies_to": ["manual"]},
    {"key": "renamed_release_warning", "label": "Rename Check", "applies_to": ["manual", "rejected"]},
    {"key": "no_video", "label": "No video files", "applies_to": ["manual", "errors"]},
    {"key": "path_error", "label": "Path or mount error", "applies_to": ["manual", "errors"]},
    {"key": "ua_error", "label": "UA error", "applies_to": ["manual", "errors"]},
    {"key": "manual_review", "label": "Manual review", "applies_to": ["manual"]},
]
REPORTING_STAGES = [
    "MediaInfo",
    "Release Group",
    "srrDB",
    "Rename Check",
    "Tracker Moderation",
    "Source Detection",
    "Discovarr",
    "Upload Assistant",
    "Tracker Validation",
    "Cross Check",
    "Inventory/QUI Sync",
    "Queue Import",
    "UI",
    "Other",
]
REPORT_TABS = [
    {"key": "active", "label": "Active"},
    {"key": "attempted", "label": "Attempted"},
    {"key": "resolved", "label": "Resolved"},
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
    check_results = _check_results_with_media_display(check_results, str(item.get("name") or ""))
    inventory_meta = item_inventory_meta(item)
    item_coverage = coverage_for_item(item, coverage or {})
    item["tracker_results"] = tracker_groups
    item["tracker_buckets"] = _tracker_bucket_items(tracker_groups)
    item["tracker_summary"] = _tracker_summary(tracker_groups)
    item["arr_result"] = arr_result
    item["arr_summary"] = _arr_summary(arr_result)
    item["check_results"] = check_results
    item["check_flags"] = _check_flags(check_results)
    item["inventory_meta"] = inventory_meta
    item["coverage"] = item_coverage
    item["missing_primary_trackers"] = missing_primary_trackers(item_coverage)
    item["valid_for_trackers"] = _valid_for_trackers(item, tracker_groups, arr_result, check_results)
    item["folder_name_check"] = _folder_name_check(item)
    item["effective_status"] = _effective_status(item)
    item["stage_flow"] = _stage_flow(item, check_results, arr_result)
    item["display_status"] = _display_status(item)
    item["next_action"] = _next_action(item)
    item["can_upload"] = _can_upload(item)
    item["decision_notice"] = _decision_notice(item, check_results)
    item["decision_label"] = _decision_label(item)
    item["reason_categories"] = _reason_categories(item, check_results, arr_result)
    item["cross_check"] = _cross_check_status(item_coverage, item["valid_for_trackers"])
    item["coverage_status"] = _coverage_status(item_coverage, item["missing_primary_trackers"], item["valid_for_trackers"])
    item["tracker_coverage"] = _tracker_coverage(item_coverage, item["missing_primary_trackers"], item["valid_for_trackers"])
    item["source_label"] = _source_label(item, tracker_groups)
    item["overview_checks"] = _overview_checks(item, check_results, arr_result)
    item["alert_tags"] = _alert_tags(item, check_results, arr_result)
    item["discovarr_local_traits"] = _discovarr_local_traits(item, check_results, arr_result)
    item["arr_release_views"] = _arr_release_views(arr_result, item["discovarr_local_traits"])
    return item


def _check_results_with_media_display(check_results: Dict[str, Any], item_name: str = "") -> Dict[str, Any]:
    media = check_results.get("media") if isinstance(check_results.get("media"), dict) else {}
    if not media:
        return check_results
    result = dict(check_results)
    result["media"] = ensure_media_display_fields(media, item_name)
    return result


def _dashboard_row_dict(row: Any, coverage: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    item = dict(row)
    tracker_groups = _tracker_result_groups(item.get("tracker_results"), item.get("verdict"))
    arr_result = _arr_result(item.get("arr_results"))
    check_results = _dashboard_check_results(item.get("check_results"))
    inventory_meta = item_inventory_meta(item)
    item_coverage = coverage_for_item(item, coverage or {})
    item["tracker_results"] = tracker_groups
    item["check_results"] = check_results
    item["check_flags"] = _check_flags(check_results)
    item["arr_result"] = arr_result
    item["inventory_meta"] = inventory_meta
    item["coverage"] = item_coverage
    item["missing_primary_trackers"] = missing_primary_trackers(item_coverage)
    item["valid_for_trackers"] = _valid_for_trackers(item, tracker_groups, arr_result, check_results)
    item["folder_name_check"] = _folder_name_check(item)
    item["effective_status"] = _effective_status(item)
    item["display_status"] = _display_status(item)
    item["next_action"] = _next_action(item)
    item["can_upload"] = _can_upload(item)
    item["decision_notice"] = _decision_notice(item, check_results)
    item["decision_label"] = _decision_label(item)
    item["cross_check"] = _cross_check_status(item_coverage, item["valid_for_trackers"])
    item["coverage_status"] = _coverage_status(item_coverage, item["missing_primary_trackers"], item["valid_for_trackers"])
    item["tracker_coverage"] = _tracker_coverage(item_coverage, item["missing_primary_trackers"], item["valid_for_trackers"])
    item["source_label"] = _source_label(item, tracker_groups)
    item["overview_checks"] = _overview_checks(item, check_results, arr_result)
    item["alert_tags"] = _alert_tags(item, check_results, arr_result)
    return item


def _row_detail_dict(row: Any, coverage: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    item = _row_dict(row, coverage)
    item["nfo_info"] = _nfo_info_for_item(item)
    item["discovarr_local_traits"] = _discovarr_local_traits(item, item["check_results"], item["arr_result"], item["nfo_info"])
    item["arr_release_views"] = _arr_release_views(item["arr_result"], item["discovarr_local_traits"])
    item["source_label"] = _source_label(item, item["tracker_results"])
    item["video_files"] = _video_files_for_item(item)
    item["folder_name_check"] = _folder_name_check(item, item["video_files"])
    item["effective_status"] = _effective_status(item)
    item["stage_flow"] = _stage_flow(item, item["check_results"], item["arr_result"])
    item["display_status"] = _display_status(item)
    item["next_action"] = _next_action(item)
    item["can_upload"] = _can_upload(item)
    item["decision_notice"] = _decision_notice(item, item["check_results"])
    item["decision_label"] = _decision_label(item)
    item["overview_checks"] = _overview_checks(item, item["check_results"], item["arr_result"])
    item["alert_tags"] = _alert_tags(item, item["check_results"], item["arr_result"])
    item["raw_payloads"] = _raw_payloads(item)
    item["upload_console"] = _upload_console_context(item)
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


def _json_array(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


def _check_results(value: Any) -> Dict[str, Any]:
    parsed = _json_object(value)
    return CheckResults.from_any(parsed).to_dict()


def _dashboard_check_results(value: Any) -> Dict[str, Any]:
    parsed = _json_object(value)
    media_payload = parsed.get("media") if isinstance(parsed.get("media"), dict) else {}
    if media_payload:
        media_payload = dict(media_payload)
        for key in ("raw_mediainfo_payloads", "raw_local_mediainfo_payloads", "supplemental_mediainfo_files"):
            media_payload.pop(key, None)
        parsed["media"] = media_payload
    check_results = CheckResults.from_any(parsed).to_dict()
    media = check_results.get("media") if isinstance(check_results.get("media"), dict) else {}
    if not media:
        return check_results
    media = dict(media)
    provider = _dashboard_source_provider_from_mediainfo(media)
    if provider:
        media["dashboard_source_provider"] = provider
    slim_files = []
    for file_info in media.get("mediainfo_files") if isinstance(media.get("mediainfo_files"), list) else []:
        if not isinstance(file_info, dict):
            continue
        slim_file = {"traits": file_info.get("traits") if isinstance(file_info.get("traits"), dict) else {}}
        name = str(file_info.get("name") or "")
        if name:
            slim_file["name"] = name
        slim_files.append(slim_file)
    if slim_files:
        media["mediainfo_files"] = slim_files
    check_results["media"] = media
    return check_results


def _dashboard_source_provider_from_mediainfo(media: Mapping[str, Any]) -> str:
    files = media.get("mediainfo_files") if isinstance(media.get("mediainfo_files"), list) else []
    for file_info in files:
        if not isinstance(file_info, Mapping):
            continue
        traits = file_info.get("traits") if isinstance(file_info.get("traits"), Mapping) else {}
        provider = provider_abbreviation_for_label(str(traits.get("source_provider") or ""))
        if provider:
            return provider
    local_traits = media.get("local_traits") if isinstance(media.get("local_traits"), Mapping) else {}
    provider = provider_abbreviation_for_label(str(local_traits.get("source_provider") or ""))
    return provider


def _check_flags(check_results: Dict[str, Any]) -> List[Dict[str, Any]]:
    flags = check_results.get("flags")
    if not isinstance(flags, list):
        return []
    return [flag for flag in flags if isinstance(flag, dict) and str(flag.get("key") or "")]


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


def _effective_status_for_row(row: Any) -> str:
    item = dict(row)
    value = str(item.get("status") or "")
    if value == "rejected":
        return value
    checks = _json_object(item.get("check_results"))
    decision = checks.get("decision") if isinstance(checks.get("decision"), dict) else {}
    decision_status = str(decision.get("status") or "")
    if decision_status in {"candidate", "manual_review", "blocked", "skipped", "retry", "error"}:
        return decision_status
    return value


def _display_status(item: Dict[str, Any]) -> Dict[str, str]:
    value = _effective_status(item)
    stage = str(item.get("check_stage") or "")
    stage_labels = {
        "media": "Checking MediaInfo",
        "path": "Mapping path",
        "ua": "Running UA",
        "arr": "Checking ARR",
        "policy": "Applying policy",
        "srrdb": "Checking srrDB",
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
    if value == "rejected":
        return {"label": "Rejected", "group": "error", "detail": "Moderator rejection recorded"}
    if value == "blocked":
        return {"label": "Blocked", "group": "covered", "detail": "No upload needed"}
    if value == "skipped":
        return {"label": "Skipped", "group": "neutral", "detail": "No upload action remains"}
    if value == "manual_review":
        return {"label": "Needs Review", "group": "attention", "detail": "Manual decision needed"}
    if value == "retry":
        return {"label": "Retry Scheduled", "group": "queued", "detail": "Waiting for retry window"}
    if value == "error":
        return {"label": "Error", "group": "error", "detail": "Investigation needed"}
    if value == "baseline":
        return {"label": "Baseline", "group": "neutral", "detail": "Inventory backlog"}
    if value == "inventory":
        return {"label": "Inventory", "group": "neutral", "detail": "Coverage signal"}
    if value == "ignored":
        return {"label": "Ignored", "group": "muted", "detail": "Hidden from active work"}
    return {"label": value.replace("_", " ").title() if value else "Unknown", "group": "neutral", "detail": ""}


def _next_action(item: Dict[str, Any]) -> str:
    status_value = _effective_status(item)
    if status_value == "candidate":
        return "Review candidate"
    if status_value == "covered":
        return "Coverage resolved"
    if status_value == "rejected":
        return "Review rejection"
    if status_value == "blocked":
        return "Review coverage"
    if status_value == "skipped":
        return "No action"
    if status_value == "manual_review":
        return "Inspect issue"
    if status_value == "retry":
        return "Waiting for retry"
    if status_value == "error":
        return "Investigate"
    if status_value in {"baseline", "queued", "deferred"}:
        return "Run check"
    if status_value == "checking":
        return "In progress"
    if status_value == "inventory":
        return "Coverage only"
    if status_value == "ignored":
        return "Ignored"
    return "Open"


def _can_upload(item: Mapping[str, Any]) -> bool:
    return _effective_status(item) in {"candidate", "manual_review"}


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
    if str(item.get("status") or "") == "rejected":
        return str(item.get("reason") or "Moderator rejection recorded.").strip()
    flags = _check_flags(check_results)
    for severity in ("blocker", "error", "warning"):
        for flag in flags:
            if str(flag.get("severity") or "").lower() == severity:
                return str(flag.get("detail") or flag.get("message") or flag.get("label") or "").strip()
    return str(item.get("reason") or item.get("display_status", {}).get("detail") or "").strip()


def _decision_label(item: Mapping[str, Any]) -> str:
    if str(item.get("status") or "") == "candidate" and _effective_status(item) == "manual_review":
        return "Review"
    return str(item.get("verdict") or item.get("next_action") or "Open")


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
    if ("banned_release_group" in verdict or "banned_release_group" in flag_keys) and not _policy_allows_any_tracker(check_results):
        categories.append("banned_release_group")
    if "srrdb_filename_mismatch" in verdict or "srrdb_filename_mismatch" in flag_keys:
        categories.append("srrdb_filename_mismatch")
    if "renamed_release_warning" in verdict or "renamed_release_warning" in flag_keys:
        categories.append("renamed_release_warning")
    if "no_video" in verdict or "video files" in reason:
        categories.append("no_video")
    if "path" in verdict or "path" in reason or "mount" in reason:
        categories.append("path_error")
    if "ua_error" in verdict or "http_error" in verdict or "ua_interrupted" in verdict:
        categories.append("ua_error")
    if status_value == "manual_review" or "manual_review" in verdict:
        categories.append("manual_review")
    if status_value == "rejected":
        categories.append("rejected")
    if status_value == "skipped":
        categories.append("skipped")
    return list(dict.fromkeys(categories))


def _high_quality_trackers() -> List[str]:
    try:
        cfg = app.state.config_manager.load()
        values = cfg.safety.high_quality_trackers
    except Exception:
        values = []
    return _dedupe_trackers([str(tracker).upper() for tracker in values if str(tracker).strip()])


def _coverage_status(
    coverage: List[Dict[str, Any]],
    missing: List[str],
    valid_for: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    valid = set(_dedupe_trackers(valid_for or []))
    found_default = [
        {"key": str(item.get("key") or ""), "label": str(item.get("label") or item.get("key") or "")}
        for item in coverage
        if item.get("primary") and str(item.get("key") or "").upper() not in valid
    ]
    found_other = [
        {"key": str(item.get("key") or ""), "label": str(item.get("label") or item.get("key") or "")}
        for item in coverage
        if not item.get("primary") and str(item.get("key") or "").upper() not in valid
    ]
    missing_default = [{"key": tracker, "label": tracker} for tracker in missing if str(tracker).upper() not in valid]
    valid_items = [{"key": tracker, "label": tracker} for tracker in _dedupe_trackers(valid_for or [])]
    return {
        "valid_for": valid_items,
        "found_default": found_default,
        "found_other": found_other,
        "missing_default": missing_default,
    }


def _tracker_coverage(
    coverage: List[Dict[str, Any]],
    missing: List[str],
    valid_for: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    seen: set[str] = set()

    def add(key: str, label: str, state: str) -> None:
        norm = str(key or label or "").upper()
        if not norm or norm in seen:
            return
        seen.add(norm)
        rows.append({"key": norm, "label": label or norm, "state": state})

    for tracker in _dedupe_trackers(valid_for or []):
        add(tracker, tracker, "valid")
    for item in coverage:
        key = str(item.get("key") or "").upper()
        label = str(item.get("label") or key)
        add(key, label, "covered-default" if item.get("primary") else "covered-other")
    for tracker in missing:
        add(str(tracker).upper(), str(tracker).upper(), "not-valid")
    return rows


def _cross_check_status(coverage: List[Dict[str, Any]], valid_for: List[str]) -> Dict[str, Any]:
    selected = set(_high_quality_trackers())
    if not selected:
        return {"state": "not_applicable", "label": "Not Validated", "trackers": [], "selected": []}
    coverage_keys = {str(item.get("key") or "").upper() for item in coverage}
    matched = sorted(coverage_keys.intersection(selected))
    if matched:
        return {"state": "pass", "label": "Validated On High Quality Tracker", "trackers": matched, "selected": sorted(selected)}
    return {"state": "warning", "label": "Not Validated", "trackers": [], "selected": sorted(selected)}


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


def _alert_tags(item: Dict[str, Any], check_results: Dict[str, Any], arr_result: Dict[str, Any]) -> List[Dict[str, str]]:
    checks = item.get("overview_checks")
    if not isinstance(checks, list):
        checks = _overview_checks(item, check_results, arr_result)
    tags: List[Dict[str, str]] = []
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            continue
        group = str(check.get("group") or "").lower()
        if group not in {"error", "warning"}:
            continue
        label = _summary_flag_label(str(check.get("label") or ""))
        if not label or label in seen:
            continue
        seen.add(label)
        tags.append(
            {
                "key": _summary_flag_key(label),
                "label": label,
                "severity": "critical" if group == "error" else "warning",
            }
        )
    return tags


def _summary_flag_label(label: str) -> str:
    return {
        "Media Info": "Media Info",
        "Source Detection": "Source",
        "Path Mapping": "Path",
        "Folder Name": "Rename",
        "Rename Check": "Rename",
        "Upload Assistant": "UA",
        "Discovarr": "Discovarr",
        "Release Group": "Group",
        "srrDB": "srrDB",
        "Cross Check": "Cross Check",
    }.get(str(label or "").strip(), "")


def _summary_flag_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")


def _policy_allows_any_tracker(check_results: Dict[str, Any]) -> bool:
    policy = check_results.get("release_group_policy") if isinstance(check_results.get("release_group_policy"), dict) else {}
    candidates = policy.get("candidate_trackers") if isinstance(policy.get("candidate_trackers"), list) else []
    return any(str(tracker).strip() for tracker in candidates)


def _source_label(item: Dict[str, Any], tracker_groups: Dict[str, List[str]]) -> str:
    values = " ".join(
        str(item.get(key) or "")
        for key in ("category", "tags", "content_path", "mapped_path", "inventory_tracker_label", "inventory_tracker_key")
    ).lower()
    if "usenet" in values:
        return "Usenet"
    tracker = item.get("inventory_meta", {}).get("tracker") if isinstance(item.get("inventory_meta"), dict) else {}
    if isinstance(tracker, dict) and str(tracker.get("label") or tracker.get("key") or "").strip():
        return str(tracker.get("label") or tracker.get("key"))
    for bucket in ("passed", "covered", "dupe", "skipped", "error"):
        trackers = tracker_groups.get(bucket) or []
        if trackers:
            return ", ".join(str(tracker) for tracker in trackers[:3])
    return "Tracker"


def _overview_checks(item: Dict[str, Any], check_results: Dict[str, Any], arr_result: Dict[str, Any]) -> List[Dict[str, str]]:
    media = check_results.get("media") if isinstance(check_results.get("media"), dict) else {}
    ua = check_results.get("ua") if isinstance(check_results.get("ua"), dict) else {}
    srrdb = check_results.get("srrdb") if isinstance(check_results.get("srrdb"), dict) else {}
    rename_detection = _rename_check(item, check_results)
    policy = check_results.get("release_group_policy") if isinstance(check_results.get("release_group_policy"), dict) else {}
    cross = item.get("cross_check") if isinstance(item.get("cross_check"), dict) else _cross_check_status(item.get("coverage") or [], item.get("valid_for_trackers") or [])
    rows = [
        _summary_row("Media Info", *_media_summary_state(media), str(media.get("reason") or ""), "mediainfo", "Raw MediaInfo", "json"),
        _summary_row("Source Detection", *_source_summary_state(item), str((item.get("nfo_info") or {}).get("message") or ""), "nfo", "NFO View", "text"),
        _summary_row("Path Mapping", *_path_summary_state(item, check_results), str(item.get("mapped_path") or item.get("content_path") or ""), "diagnostics", "Path Mapping View", "json"),
        _summary_row(
            "Rename Check",
            *_rename_summary_state(rename_detection),
            str(rename_detection.get("reason") or ""),
            "rename_detection",
            "Rename Check View",
            "json",
        ),
        _summary_row("Upload Assistant", *_ua_summary_state(item, item.get("tracker_results") or {}), str(ua.get("reason") or item.get("tracker_summary") or ""), "ua_log", "UA Log View", "text"),
        _summary_row("Discovarr", *_arr_summary_state(arr_result), str(arr_result.get("reason") or item.get("arr_summary") or ""), "arr", "Raw Arr Result View", "json"),
        _summary_row("Release Group", *_policy_summary_state(policy), str(policy.get("reason") or ""), "diagnostics", "Release Group View", "json"),
        _summary_row("srrDB", *_srrdb_summary_state(srrdb), str(srrdb.get("reason") or ""), "srrdb", "Raw srrDB View", "json"),
        _summary_row("Cross Check", *_cross_summary_state(cross), ", ".join(cross.get("trackers") or cross.get("selected") or []), "diagnostics", "Cross Check Validation", "json"),
    ]
    return rows


def _rename_check(item: Dict[str, Any], check_results: Dict[str, Any]) -> Dict[str, Any]:
    rename = check_results.get("rename_detection") if isinstance(check_results.get("rename_detection"), dict) else {}
    if rename:
        return dict(rename)
    folder_check = item.get("folder_name_check") if isinstance(item.get("folder_name_check"), dict) else _folder_name_check(item)
    if str(folder_check.get("group") or "") == "warning":
        return {
            "version": 1,
            "status": "pass",
            "confidence": "low",
            "reason": str(folder_check.get("notes") or "Folder name needs review before upload."),
            "evidence": [
                {
                    "kind": "folder_scene_normalization",
                    "scope": "folder",
                    "confidence": "low",
                    "source": "legacy",
                    "value": str(folder_check.get("root_name") or ""),
                    "expected": str(folder_check.get("normalized") or ""),
                    "reason": str(folder_check.get("notes") or ""),
                }
            ],
        }
    for flag in _check_flags(check_results):
        key = str(flag.get("key") or "")
        if key in {"folder_name_warning", "possible_renamed_release", "renamed_release_warning"}:
            status = "warning"
            confidence = "medium"
            if key == "folder_name_warning":
                status = "pass"
                confidence = "low"
            elif key == "renamed_release_warning":
                status = "manual_review"
                confidence = "high"
            return {
                "version": 1,
                "status": status,
                "confidence": confidence,
                "reason": str(flag.get("detail") or flag.get("label") or "Rename Check needs review."),
                "evidence": [
                    {
                        "kind": key,
                        "scope": "legacy",
                        "confidence": confidence,
                        "source": "legacy",
                        "reason": str(flag.get("detail") or flag.get("label") or ""),
                    }
                ],
            }
    return {
        "version": 1,
        "status": "pass",
        "confidence": "low",
        "reason": "Rename Check did not find suspicious name mismatches.",
        "evidence": [],
    }


def _summary_row(label: str, state: str, group: str, notes: str, raw_key: str, raw_title: str, raw_kind: str) -> Dict[str, str]:
    return {
        "label": label,
        "state": state,
        "group": group,
        "notes": notes or "-",
        "raw_key": raw_key,
        "raw_title": raw_title,
        "raw_kind": raw_kind,
    }


def _check_summary(label: str, status_value: str, notes: str) -> Dict[str, str]:
    value = status_value.lower()
    if any(token in value for token in ("error", "fail", "manual", "mismatch")):
        state = "Fail"
        group = "error"
    elif "warning" in value:
        state = "Warning"
        group = "warning"
    elif value:
        state = "Pass"
        group = "pass"
    else:
        state = "Not run"
        group = "neutral"
    return {"label": label, "state": state, "group": group, "notes": notes or "-"}


def _media_summary_state(media: Dict[str, Any]) -> tuple[str, str]:
    value = str(media.get("media_status") or media.get("verdict") or media.get("status") or "").lower()
    issues = media.get("issues") if isinstance(media.get("issues"), list) else []
    severities = {str(issue.get("severity") or "").lower() for issue in issues if isinstance(issue, dict)}
    if "error" in value or "fail" in value or "error" in severities or "failure" in severities:
        return "Fail", "error"
    if "warning" in value or "warning" in severities:
        return "Warning", "warning"
    return ("Pass", "pass") if value or issues == [] else ("Not Applicable", "neutral")


def _rename_summary_state(rename_detection: Dict[str, Any]) -> tuple[str, str]:
    status_value = str(rename_detection.get("status") or "").lower()
    confidence = str(rename_detection.get("confidence") or "").lower()
    if status_value == "manual_review" or confidence == "high":
        return "Warning", "warning"
    if status_value == "warning" or confidence == "medium":
        return "Warning", "warning"
    if status_value == "pass":
        return "Pass", "pass"
    return "Not Applicable", "neutral"


def _path_summary_state(item: Dict[str, Any], check_results: Dict[str, Any]) -> tuple[str, str]:
    status_value = str(item.get("status") or "").lower()
    verdict = str(item.get("verdict") or "").lower()
    reason = str(item.get("reason") or "").lower()
    diagnostics = check_results.get("diagnostics") if isinstance(check_results.get("diagnostics"), dict) else {}
    stages = diagnostics.get("stages") if isinstance(diagnostics.get("stages"), list) else []
    stage_names = {str(stage.get("stage") or "").lower() for stage in stages if isinstance(stage, dict)}
    if "path" in verdict or "path" in reason or "mount" in reason:
        return ("Warning", "warning") if status_value == "manual_review" else ("Fail", "error")
    if "path" in stage_names or str(item.get("mapped_path") or "").strip():
        return "Pass", "pass"
    return "Not Applicable", "neutral"


def _policy_summary_state(policy: Dict[str, Any]) -> tuple[str, str]:
    decisions = policy.get("decisions") if isinstance(policy.get("decisions"), list) else []
    if not decisions:
        return "Not Applicable", "neutral"
    blocked = [decision for decision in decisions if isinstance(decision, dict) and str(decision.get("status") or "").lower() != "candidate"]
    if not blocked:
        return "Pass", "pass"
    if len(blocked) == len(decisions):
        return "Fail", "error"
    return "Warning", "warning"


def _bucket_summary_state(groups: Dict[str, Any]) -> tuple[str, str]:
    passed = groups.get("passed") or groups.get("covered") or []
    errors = groups.get("error") or []
    skipped = groups.get("skipped") or []
    dupes = groups.get("dupe") or []
    if passed and not errors:
        return "Pass", "pass"
    if passed and errors:
        return "Warning", "warning"
    if dupes or skipped or errors:
        return "Fail", "error"
    return "Not Applicable", "neutral"


def _ua_summary_state(item: Dict[str, Any], groups: Dict[str, Any]) -> tuple[str, str]:
    passed = groups.get("passed") or groups.get("covered") or []
    if passed:
        return _bucket_summary_state(groups)
    status_value = str(item.get("status") or "").lower()
    verdict = str(item.get("verdict") or "").lower()
    reason = str(item.get("reason") or "").lower()
    if status_value == "error" or "ua_error" in verdict or "http_error" in verdict:
        return "Fail", "error"
    if status_value in {"blocked", "manual_review"} and (
        "no_tracker" in verdict
        or "dupe" in verdict
        or "ua" in verdict
        or "tracker" in reason
        or "upload assistant" in reason
    ):
        return "Fail", "error"
    return _bucket_summary_state(groups)


def _arr_summary_state(arr_result: Dict[str, Any]) -> tuple[str, str]:
    status_value = str(arr_result.get("status") or "").lower()
    reason = str(arr_result.get("reason") or "").lower()
    if "unavailable" in status_value or "unavailable" in reason or "no matching" in reason:
        return "Fail", "error"
    decisions = arr_result.get("decisions") if isinstance(arr_result.get("decisions"), list) else []
    same_lane = sum(int(decision.get("same_lane_count") or 0) for decision in decisions if isinstance(decision, dict))
    candidates = [decision for decision in decisions if isinstance(decision, dict) and str(decision.get("status") or "").lower() == "candidate"]
    blocked = [decision for decision in decisions if isinstance(decision, dict) and str(decision.get("status") or "").lower() == "blocked"]
    if not decisions:
        return "Not Applicable", "neutral"
    if candidates:
        return "Pass", "pass"
    if blocked:
        return "Fail", "error"
    return ("Warning", "warning") if same_lane else ("Not Applicable", "neutral")


def _srrdb_summary_state(srrdb: Dict[str, Any]) -> tuple[str, str]:
    status_value = str(srrdb.get("status") or "").lower()
    if status_value in {"verified", "found", "match"}:
        return "Pass", "pass"
    if status_value == "mismatch":
        return "Warning", "warning"
    return "Not Applicable", "neutral"


def _source_summary_state(item: Dict[str, Any]) -> tuple[str, str]:
    if not _is_web_release(item):
        return "Not Required", "neutral"
    if _source_provider_for_item(item):
        return "Pass", "pass"
    return "Warning", "warning"


def _cross_summary_state(cross: Dict[str, Any]) -> tuple[str, str]:
    state = str(cross.get("state") or "").lower()
    if state == "pass":
        return "Pass", "pass"
    if state == "warning":
        return "Warning", "warning"
    return "Not Applicable", "neutral"


def _discovarr_local_traits(
    item: Dict[str, Any],
    check_results: Dict[str, Any],
    arr_result: Dict[str, Any],
    nfo_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    media = check_results.get("media") if isinstance(check_results.get("media"), dict) else {}
    arr_traits = arr_result.get("local_traits") if isinstance(arr_result.get("local_traits"), dict) else {}
    media_traits = media.get("local_traits") if isinstance(media.get("local_traits"), dict) else {}
    file_traits = _first_mediainfo_file_traits(media)
    traits: Dict[str, Any] = {**media_traits, **arr_traits}
    for key in (
        "audio_format",
        "audio_format_rank",
        "audio_channels",
        "audio_objects",
        "codec",
        "bit_depth",
        "chroma",
        "languages",
        "subtitle_tags",
    ):
        if _empty_trait_value(traits.get(key)) and not _empty_trait_value(file_traits.get(key)):
            traits[key] = file_traits[key]
    nfo = nfo_info if isinstance(nfo_info, dict) else {}
    provider = (
        extract_provider_from_release_title(str(item.get("name") or ""))
        or str(nfo.get("provider_abbreviation") or "")
        or _source_provider_from_mediainfo(media)
    )
    if provider:
        traits["source_provider_abbreviation"] = provider
    traits["attribute_tags"] = _discovarr_attribute_tags(traits)
    return traits


def _first_mediainfo_file_traits(media: Mapping[str, Any]) -> Dict[str, Any]:
    files = media.get("mediainfo_files") if isinstance(media.get("mediainfo_files"), list) else []
    for file_info in files:
        if not isinstance(file_info, Mapping):
            continue
        traits = file_info.get("traits")
        if isinstance(traits, Mapping):
            return dict(traits)
    return {}


def _empty_trait_value(value: Any) -> bool:
    if value is None:
        return True
    if value == "":
        return True
    if value == 0 or value == 0.0:
        return True
    if isinstance(value, (list, tuple, dict, set)) and not value:
        return True
    return False


def _discovarr_attribute_tags(traits: Dict[str, Any]) -> List[Dict[str, str]]:
    tags: List[Dict[str, str]] = []
    for key, label in (
        ("resolution", "Resolution"),
        ("source_tag", "Type"),
        ("hdr_label", "HDR"),
        ("audio_format", "Audio"),
        ("codec", "Codec"),
    ):
        value = traits.get(key)
        if value:
            tags.append({"label": label, "value": str(value), "group": "neutral"})
    if traits.get("audio_channels"):
        tags.append({"label": "Channels", "value": f"{float(traits['audio_channels']):.1f}", "group": "neutral"})
    rip_type = str(traits.get("rip_type") or "").lower()
    source = str(traits.get("source") or "").lower()
    is_web = source == "web" or rip_type in {"web-dl", "webrip", "web"}
    if is_web:
        provider = str(traits.get("source_provider_abbreviation") or "").strip()
        tags.append({"label": "Source", "value": provider or "Source Missing", "group": "warning" if not provider else "source"})
    return tags


def _arr_release_views(arr_result: Dict[str, Any], local_traits: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
    local = local_traits or (arr_result.get("local_traits") if isinstance(arr_result.get("local_traits"), dict) else {})
    views: Dict[str, List[Dict[str, Any]]] = {}
    for decision in arr_result.get("decisions", []) if isinstance(arr_result.get("decisions"), list) else []:
        if not isinstance(decision, dict):
            continue
        rows = []
        for release in decision.get("results", []) if isinstance(decision.get("results"), list) else []:
            if not isinstance(release, dict):
                continue
            remote = release.get("traits") if isinstance(release.get("traits"), dict) else {}
            rows.append(
                {
                    **release,
                    "lane_tags": _lane_tags(local, remote),
                    "ranking_tags": _ranking_tags(local, remote),
                }
            )
        views[str(decision.get("tracker") or "")] = rows
    return views


def _lane_tags(local: Dict[str, Any], remote: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        _compare_tag("Resolution", _resolution_height_label(local.get("resolution")), _resolution_height_label(remote.get("resolution"))),
        _compare_tag("Source", str(local.get("source_label") or local.get("source") or ""), str(remote.get("source_label") or remote.get("source") or "")),
        _compare_tag(
            "Version",
            ", ".join(str(item) for item in local.get("movie_versions", []) or []) or "Standard",
            ", ".join(str(item) for item in remote.get("movie_versions", []) or []) or "Standard",
        ),
    ]


def _ranking_tags(local: Dict[str, Any], remote: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        _same_rank_tag("Scan", str(local.get("scan_type") or ""), str(remote.get("scan_type") or "")),
        _hdr_rank_tag(local, remote),
        _rank_tag("Audio", _int_value(remote.get("audio_format_rank")), _int_value(local.get("audio_format_rank")), str(remote.get("audio_format") or "-")),
        _rank_tag("Channels", _float_value(remote.get("audio_channels")), _float_value(local.get("audio_channels")), str(remote.get("audio_channels") or "-")),
        _same_rank_tag("Codec", str(local.get("codec") or ""), str(remote.get("codec") or "")),
    ]


def _compare_tag(label: str, local: str, remote: str) -> Dict[str, str]:
    ok = bool(local and remote and local == remote)
    detail = remote if ok else f"{remote or '-'} != {local or '-'}"
    return {"label": label, "detail": detail, "group": "match" if ok else "mismatch"}


def _same_rank_tag(label: str, local: str, remote: str) -> Dict[str, str]:
    ok = bool(local and remote and local == remote)
    detail = remote if ok else f"{remote or '-'} != {local or '-'}"
    return {"label": label, "detail": detail, "group": "same" if ok else "worse"}


def _rank_tag(label: str, remote_rank: float, local_rank: float, detail: str) -> Dict[str, str]:
    if remote_rank == local_rank:
        group = "same"
    elif remote_rank < local_rank:
        group = "better"
    else:
        group = "worse"
    return {"label": label, "detail": detail, "group": group}


def _hdr_rank_tag(local: Dict[str, Any], remote: Dict[str, Any]) -> Dict[str, str]:
    local_rank = _int_value(local.get("hdr_rank"))
    remote_rank = _int_value(remote.get("hdr_rank"))
    local_formats = {str(value) for value in local.get("hdr_formats", []) or []}
    remote_formats = {str(value) for value in remote.get("hdr_formats", []) or []}
    if remote_rank == local_rank or ("HDR10+" in local_formats and "HDR10+" in remote_formats):
        group = "same"
    elif (
        "Dolby Vision" in local_formats
        and "HDR10" in local_formats
        and ("HDR10" in remote_formats or remote_rank == 1)
    ):
        group = "same"
    elif remote_rank == 0 and not remote_formats:
        group = "same"
    elif remote_rank < local_rank:
        group = "better"
    else:
        group = "worse"
    return {"label": "HDR", "detail": str(remote.get("hdr_label") or "SDR"), "group": group}


def _resolution_height_label(value: Any) -> str:
    text = str(value or "")
    for prefix in ("2160", "1080", "720", "576", "480"):
        if prefix in text:
            return prefix
    return text


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _dedupe_trackers(trackers: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(str(tracker).upper() for tracker in trackers if str(tracker).strip()))


def _raw_payloads(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw_torrent = _json_object(item.get("raw_torrent"))
    checks = item.get("check_results") if isinstance(item.get("check_results"), dict) else {}
    media = checks.get("media") if isinstance(checks.get("media"), dict) else {}
    srrdb = checks.get("srrdb") if isinstance(checks.get("srrdb"), dict) else {}
    rename_detection = _rename_check(item, checks)
    nfo_info = item.get("nfo_info") if isinstance(item.get("nfo_info"), dict) else checks.get("nfo") if isinstance(checks.get("nfo"), dict) else {}
    raw_mediainfo = _json_array(item.get("media_raw_mediainfo_payloads")) or (media.get("raw_mediainfo_payloads") if isinstance(media, dict) else [])
    raw_local_mediainfo = _json_array(item.get("media_raw_local_mediainfo_payloads")) or (
        media.get("raw_local_mediainfo_payloads") if isinstance(media, dict) else []
    )
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
        "local-mediainfo": {
            "title": "Raw Local MediaInfo",
            "kind": "json",
            "available": bool(raw_local_mediainfo),
            "content": raw_local_mediainfo or {"message": "Raw local MediaInfo will be available after this item is rechecked."},
        },
        "nfo": {
            "title": "NFO",
            "kind": "text",
            "available": bool(nfo_info.get("content")),
            "content": str(nfo_info.get("content") or nfo_info.get("message") or "No NFO captured yet."),
        },
        "arr": {
            "title": "Raw ARR result",
            "kind": "json",
            "available": bool(item.get("arr_result")),
            "content": item.get("arr_result") or {"message": "No ARR result recorded."},
        },
        "srrdb": {
            "title": "srrDB result",
            "kind": "json",
            "available": bool(srrdb),
            "content": srrdb or {"message": "No srrDB verification recorded."},
        },
        "rename_detection": {
            "title": "Rename Check",
            "kind": "json",
            "available": bool(rename_detection),
            "content": rename_detection or {"message": "No Rename Check evidence recorded."},
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
        "effective_status": item.get("effective_status") or _effective_status(item),
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
        "can_upload": bool(item.get("can_upload")),
        "flags": item["check_flags"],
        "alert_tags": item.get("alert_tags") or [],
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
        "decision_label": item.get("decision_label") or _decision_label(item),
        "decision": item.get("check_results", {}).get("decision", {}) if isinstance(item.get("check_results"), dict) else {},
        "reason_categories": item["reason_categories"],
        "coverage_status": item["coverage_status"],
        "tracker_coverage": item.get("tracker_coverage") or [],
        "cross_check": item.get("cross_check") or {},
        "folder_name_check": item.get("folder_name_check") or _folder_name_check(item),
        "rename_detection": item.get("check_results", {}).get("rename_detection", {}) if isinstance(item.get("check_results"), dict) else {},
        "source_label": item["source_label"],
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
        "srrdb": stored_checks.get("srrdb") or {},
        "rename_detection": stored_checks.get("rename_detection") or {},
        "release_group_policy": stored_checks.get("release_group_policy") or {},
        "coverage_resolution": stored_checks.get("coverage_resolution") or {},
        "decision": stored_checks.get("decision") or {},
        "rules": stored_checks.get("rules") or [],
        "ruleset_version": stored_checks.get("ruleset_version") or 0,
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


def _report_payload(row: Any) -> Dict[str, Any]:
    item = dict(row)
    return {
        "id": int(item["id"]),
        "item_id": int(item["item_id"]),
        "item_name": item["item_name"],
        "stage": item["stage"],
        "notes": item["notes"],
        "state": item["state"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "resolved_at": item["resolved_at"],
    }


def _sanitize_report_state(value: str, default: str = "active") -> str:
    text = str(value or "").strip().lower()
    return text if text in REPORT_STATES else default


def _sanitize_report_stage(value: str) -> str:
    text = str(value or "").strip()
    return text if text in REPORTING_STAGES else "Other"


def _report_group_key(report: Mapping[str, Any]) -> str:
    stage = re.sub(r"\s+", " ", str(report.get("stage") or "Other")).strip().lower()
    notes = re.sub(r"\s+", " ", str(report.get("notes") or "")).strip().lower()
    return f"{stage}\n{notes}"


def _report_groups(reports: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for report in reports:
        key = _report_group_key(report)
        group = grouped.setdefault(
            key,
            {
                "key": key,
                "stage": report.get("stage") or "Other",
                "notes": report.get("notes") or "",
                "reports": [],
                "items": {},
                "oldest_at": int(report.get("created_at") or 0),
                "newest_at": int(report.get("updated_at") or 0),
            },
        )
        group["reports"].append(report)
        group["items"][int(report["item_id"])] = {
            "id": int(report["item_id"]),
            "name": report.get("item_name") or f"Item {report['item_id']}",
        }
        group["oldest_at"] = min(int(group["oldest_at"] or 0) or int(report.get("created_at") or 0), int(report.get("created_at") or 0))
        group["newest_at"] = max(int(group["newest_at"] or 0), int(report.get("updated_at") or 0))

    rows = []
    for group in grouped.values():
        reports_list = list(group["reports"])
        rows.append(
            {
                **group,
                "count": len(reports_list),
                "report_ids": [int(report["id"]) for report in reports_list],
                "items": sorted(group["items"].values(), key=lambda item: item["name"].lower()),
            }
        )
    return sorted(rows, key=lambda group: (-int(group["count"]), -int(group["newest_at"] or 0), str(group["stage"]).lower()))


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


def _upload_console_args(item: Dict[str, Any]) -> str:
    parts: List[str] = []
    trackers = [str(tracker).strip().lower() for tracker in item.get("valid_for_trackers") or [] if str(tracker).strip()]
    if trackers:
        parts.append(f"--trackers {','.join(dict.fromkeys(trackers))}")
    provider = _source_provider_for_item(item)
    if provider and _is_web_release(item) and not _release_title_has_provider(str(item.get("name") or ""), provider):
        parts.append(f"--service {provider}")
    return " ".join(parts)


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


def _nfo_info_for_item(item: Dict[str, Any]) -> Dict[str, Any]:
    checks = item.get("check_results") if isinstance(item.get("check_results"), dict) else {}
    stored = checks.get("nfo") if isinstance(checks.get("nfo"), dict) else {}
    if stored.get("content"):
        return _nfo_payload(str(stored.get("content") or ""), str(stored.get("path") or ""), str(stored.get("source") or "stored"))
    local = _local_nfo_info_for_item(item)
    if local.get("content"):
        return local
    return stored or local


def _local_nfo_info_for_item(item: Dict[str, Any]) -> Dict[str, Any]:
    root = str(item.get("mapped_path") or item.get("content_path") or "")
    if not root:
        return {"available": False, "message": "No path recorded."}
    try:
        path = Path(root)
        candidates: List[Path] = []
        if path.is_file():
            if path.suffix.lower() in NFO_EXTENSIONS:
                candidates.append(path)
            candidates.extend(sorted(path.parent.glob("*.nfo")))
        elif path.is_dir():
            candidates.extend(sorted(path.rglob("*.nfo")))
        else:
            return {"available": False, "message": "Path is not visible inside the Whackamole container."}
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                content = candidate.read_bytes()[:MAX_NFO_BYTES].decode("utf-8", errors="replace")
            except OSError:
                continue
            return _nfo_payload(content, str(candidate), "local")
        return {"available": False, "message": "No NFO found at this path."}
    except OSError as exc:
        return {"available": False, "message": f"Could not inspect NFO path: {exc}"}


async def _grab_nfo_for_row(row: Any, request: Request) -> Dict[str, Any]:
    item = _row_dict(row, _coverage_for_row(request.app.state.db, row))
    local = _local_nfo_info_for_item(item)
    if local.get("content"):
        return local

    cfg = request.app.state.config_manager.load()
    try:
        qui = QuiClient(cfg, request.app.state.secrets.get("qui_api_key"))
        files = await qui.list_torrent_files(str(item.get("hash") or ""))
        for file_info in files:
            name = str(file_info.get("name") or "")
            if Path(name).suffix.lower() not in NFO_EXTENSIONS:
                continue
            content = (await qui.download_torrent_file(str(item.get("hash") or ""), int(file_info.get("index") or 0), MAX_NFO_BYTES)).decode(
                "utf-8",
                errors="replace",
            )
            return _nfo_payload(content, name, "qui")
    except Exception as exc:
        return {"available": False, "message": f"Could not grab NFO: {str(exc)[:180]}", "source": "error"}
    return local if local.get("message") else {"available": False, "message": "No NFO found in QUI files.", "source": "qui"}


def _nfo_payload(content: str, path: str, source: str) -> Dict[str, Any]:
    provider = extract_provider_abbreviation(content)
    return {
        "available": bool(content),
        "source": source,
        "path": path,
        "content": content,
        "provider_abbreviation": provider,
        "message": f"NFO found at {path}." if content else "No NFO content found.",
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
    status_value = _effective_status(item)
    stage = str(item.get("check_stage") or "")
    final_statuses = {"candidate", "covered", "rejected", "blocked", "skipped", "manual_review", "retry", "error", "ignored", "inventory", "baseline"}
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
        "rejected": "Rejected",
        "blocked": "Blocked",
        "skipped": "Skipped",
        "manual_review": "Review",
        "retry": "Retry",
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
    policy_blocked = [
        str(item.get("tracker"))
        for item in decisions
        if item.get("status") == "blocked"
        and ("banned_match" in item or "banned" in str(item.get("reason") or "").lower())
    ]
    blocked = [
        str(item.get("tracker"))
        for item in decisions
        if item.get("status") == "blocked" and str(item.get("tracker")) not in policy_blocked
    ]
    manual = [str(item.get("tracker")) for item in decisions if item.get("status") == "manual_review"]
    parts = []
    if valid:
        parts.append(f"Valid: {', '.join(valid)}")
    if covered:
        parts.append(f"Covered: {', '.join(covered)}")
    if policy_blocked:
        parts.append(f"Policy blocked: {', '.join(policy_blocked)}")
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
    tracker_options = _tracker_setting_options(request.app.state.db, cfg)
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
        "tracker_options": tracker_options,
        "high_quality_trackers": set(_dedupe_trackers(cfg.safety.high_quality_trackers)),
        "message": message,
        "probe_results": probe_results or [],
    }


def _rules_context(
    request: Request,
    *,
    message: str = "",
    replay_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        **_shell_context(request, section="settings"),
        "request": request,
        "rules": rule_catalogue(),
        "changelog": ruleset_changelog(),
        "severity_terms": [
            {"key": "pass", "label": "Pass", "detail": "Full pass with no issue."},
            {"key": "info", "label": "Info", "detail": "Likely pass or clean no-op with minor flags."},
            {"key": "warning", "label": "Warning", "detail": "Something may be wrong but can be reviewed."},
            {"key": "error", "label": "Error", "detail": "System or evidence failure needing investigation."},
        ],
        "effect_terms": [
            {"key": "candidate", "label": "Candidate", "detail": "Eligible to upload."},
            {"key": "review", "label": "Review", "detail": "Minor rule broken; manual decision needed."},
            {"key": "block", "label": "Block", "detail": "Clear rule broken; do not upload."},
            {"key": "skip", "label": "Skip", "detail": "Clean no-op; no valid target remains."},
            {"key": "retry", "label": "Retry", "detail": "Temporary failure; wait for retry window."},
            {"key": "error", "label": "Error", "detail": "Terminal failure; investigate rather than retry automatically."},
            {"key": "none", "label": "None", "detail": "Informational evidence only."},
        ],
        "message": message,
        "replay_result": replay_result,
    }


def _shell_context(
    request: Request,
    section: str = "",
    view: str = "",
    q: str = "",
    service_snapshot: Optional[Dict[str, Any]] = None,
    counts: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    service_snapshot = service_snapshot or request.app.state.service.snapshot()
    counts = dict(counts) if counts is not None else _effective_status_counts(request.app.state.db, request.app.state.db.status_counts())
    return {
        "section": section,
        "service": service_snapshot,
        "counts": counts,
        "dashboard_nav": _dashboard_nav(counts, service_snapshot, view=view, q=q),
        "search_query": q,
        "show_dashboard_search": section == "dashboard",
    }


def _effective_status_counts(db: Database, counts: Mapping[str, int]) -> Dict[str, int]:
    adjusted = {str(key): int(value or 0) for key, value in counts.items()}
    rows = db.list_dashboard_items_filtered(["candidate"], limit=1000)
    if not rows:
        return adjusted
    moved = sum(1 for row in rows if _effective_status_for_row(row) == "manual_review")
    if moved:
        adjusted["candidate"] = max(0, int(adjusted.get("candidate", 0)) - moved)
        adjusted["manual_review"] = int(adjusted.get("manual_review", 0)) + moved
    return adjusted


def _home_context(request: Request) -> Dict[str, Any]:
    service = request.app.state.service.snapshot()
    counts = _effective_status_counts(request.app.state.db, request.app.state.db.status_counts())
    total = sum(int(value or 0) for value in counts.values())
    return {
        **_shell_context(request, section="home", service_snapshot=service, counts=counts),
        "request": request,
        "summary_cards": [
            {"label": "Service", "value": "Running" if service["running"] else "Stopped", "detail": f"{service['running_jobs']} UA active"},
            {"label": "Queue", "value": service["queue"]["active"], "detail": f"{service['queue']['waiting_retries']} waiting retries"},
            {"label": "Baseline", "value": "Complete" if service["baseline_done"] else "Pending", "detail": f"{total} stored items"},
            {"label": "Maintenance", "value": str(service["maintenance"]["state"]).replace("_", " ").title(), "detail": service["maintenance"]["dependency"]},
            {
                "label": "Whacked",
                "value": f"{service['whacked']['holes_filled']} hole{'' if service['whacked']['holes_filled'] == 1 else 's'}",
                "detail": f"{service['whacked']['cross_seed_count']} cross-seeds · {service['whacked']['upload_count']} uploads",
            },
        ],
    }


def _dashboard_nav(counts: Dict[str, int], service: Dict[str, Any], view: str = "", q: str = "") -> List[Dict[str, Any]]:
    rows = []
    queue = service.get("queue") if isinstance(service.get("queue"), dict) else {}
    imports = service.get("imports") if isinstance(service.get("imports"), dict) else {}
    report_counts = service.get("reports") if isinstance(service.get("reports"), dict) else {}
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
    rows.append(
        {
            "key": "imports",
            "label": "Queued Imports",
            "total": int(imports.get("active") or 0),
            "href": "/imports",
            "selected": view == "imports",
        }
    )
    rows.append(
        {
            "key": "reports",
            "label": "Reports",
            "total": int(report_counts.get("open") or 0),
            "href": "/reports",
            "selected": view == "reports",
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


def _tracker_setting_options(db: Database, cfg: AppConfig) -> List[Dict[str, Any]]:
    options: Dict[str, Dict[str, Any]] = {}
    for tracker in default_tracker_policies().keys():
        options[str(tracker).upper()] = {"key": str(tracker).upper(), "label": str(tracker).upper(), "primary": True}
    for item in db.list_inventory_trackers():
        key = str(item.get("key") or "").upper()
        if not key:
            continue
        options[key] = {**item, "key": key, "label": str(item.get("label") or key)}
    for tracker in cfg.safety.high_quality_trackers:
        key = str(tracker or "").upper()
        if key:
            options.setdefault(key, {"key": key, "label": key, "primary": False})
    return sorted(options.values(), key=lambda row: (not bool(row.get("primary")), str(row.get("key") or "")))


def _coverage_for_rows(db: Database, rows: Sequence[Any]) -> Dict[str, List[Dict[str, Any]]]:
    group_keys = [str(dict(row).get("inventory_group_key") or item_inventory_meta(dict(row)).get("group_key") or "") for row in rows]
    return db.coverage_for_group_keys(group_keys)


def _coverage_for_row(db: Database, row: Any) -> Dict[str, List[Dict[str, Any]]]:
    return _coverage_for_rows(db, [row])


def _effective_status_filter_applies(statuses: Sequence[str]) -> bool:
    values = {str(status or "") for status in statuses}
    return bool(values.intersection({"candidate", "manual_review"}))


def _query_statuses_for_effective_filter(statuses: Sequence[str]) -> List[str]:
    values = [str(status) for status in statuses]
    if "manual_review" in values and "candidate" not in values:
        values.append("candidate")
    return values


def _matches_effective_status(item: Mapping[str, Any], statuses: Sequence[str]) -> bool:
    if not statuses:
        return True
    return str(item.get("effective_status") or _effective_status(item)) in {str(status) for status in statuses}


def _row_matches_effective_status(row: Any, statuses: Sequence[str]) -> bool:
    if not statuses:
        return True
    return _effective_status_for_row(row) in {str(status) for status in statuses}


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
    effective_filter = _effective_status_filter_applies(statuses)
    query_statuses = _query_statuses_for_effective_filter(statuses) if effective_filter else list(statuses)
    query_limit = max(limit + offset, 1000) if effective_filter else limit
    query_offset = 0 if effective_filter else offset
    rows = db.list_items_filtered(
        query_statuses,
        limit=query_limit,
        offset=query_offset,
        media=media,
        missing=missing,
        valid_for=valid_for,
        reasons=reasons,
        hide_any_primary=hide_any_primary,
        due_errors_only=due_errors_only,
        q=q,
    )
    if effective_filter:
        filtered_rows = [row for row in rows if _row_matches_effective_status(row, statuses)]
        total = len(filtered_rows)
        rows = filtered_rows[offset : offset + limit]
        return rows, total, _coverage_for_rows(db, rows)
    coverage = _coverage_for_rows(db, rows)
    total = db.count_items_filtered(
        query_statuses,
        media=media,
        missing=missing,
        valid_for=valid_for,
        reasons=reasons,
        hide_any_primary=hide_any_primary,
        due_errors_only=due_errors_only,
        q=q,
    )
    return rows, total, coverage


def _filtered_dashboard_items(
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
) -> tuple[List[Dict[str, Any]], int]:
    effective_filter = _effective_status_filter_applies(statuses)
    query_statuses = _query_statuses_for_effective_filter(statuses) if effective_filter else list(statuses)
    query_limit = max(limit + offset, 1000) if effective_filter else limit
    query_offset = 0 if effective_filter else offset
    rows = db.list_dashboard_items_filtered(
        query_statuses,
        limit=query_limit,
        offset=query_offset,
        media=media,
        missing=missing,
        valid_for=valid_for,
        reasons=reasons,
        hide_any_primary=hide_any_primary,
        due_errors_only=due_errors_only,
        q=q,
    )
    if effective_filter:
        filtered_rows = [row for row in rows if _row_matches_effective_status(row, statuses)]
        total = len(filtered_rows)
        page_rows = filtered_rows[offset : offset + limit]
        coverage = _coverage_for_rows(db, page_rows)
        return [_dashboard_row_dict(row, coverage) for row in page_rows], total
    coverage = _coverage_for_rows(db, rows)
    items = [_dashboard_row_dict(row, coverage) for row in rows]
    total = db.count_items_filtered(
        query_statuses,
        media=media,
        missing=missing,
        valid_for=valid_for,
        reasons=reasons,
        hide_any_primary=hide_any_primary,
        due_errors_only=due_errors_only,
        q=q,
    )
    return items, total


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
    return f"/dashboard?{urlencode(params, doseq=True)}"


def _imports_url(view: str = "queue", page: int = 1) -> str:
    selected = view if view in IMPORT_VIEW_STATUSES else "queue"
    params: Dict[str, Any] = {"view": selected, "page": max(1, int(page or 1))}
    return f"/imports?{urlencode(params)}"


def _clamped_imports_url(db: Database, view: str = "queue", page: int = 1) -> str:
    selected = view if view in IMPORT_VIEW_STATUSES else "queue"
    total = db.count_imports(IMPORT_VIEW_STATUSES[selected])
    max_page = max(1, (total + IMPORT_PAGE_SIZE - 1) // IMPORT_PAGE_SIZE)
    return _imports_url(selected, min(max(1, int(page or 1)), max_page))


def _next_item_url(db: Database, item: Dict[str, Any]) -> str:
    status_value = str(item.get("status") or "")
    view = next((key for key, _label, statuses in DASHBOARD_TABS if status_value in statuses), "all")
    rows = db.list_items(DASHBOARD_VIEWS.get(view, [status_value]), limit=500)
    ids = [int(row["id"]) for row in rows]
    try:
        index = ids.index(int(item["id"]))
    except (ValueError, KeyError, TypeError):
        return _dashboard_url(view)
    if index + 1 < len(ids):
        return f"/items/{ids[index + 1]}"
    return _dashboard_url(view)


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
    app.state.ua_execution = UaExecutionCoordinator()
    app.state.upload_console = UploadConsoleManager(app.state.ua_execution)
    app.state.service = WhackamoleService(app.state.config_manager, app.state.secrets, app.state.db, app.state.ua_execution)
    app.state.service.start()
    try:
        yield
    finally:
        await app.state.service.stop()


app = FastAPI(title="Whackamole", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    if request.query_params.get("view"):
        return RedirectResponse(url=f"/dashboard?{request.url.query}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    return templates.TemplateResponse(request, "home.html", _home_context(request))


@app.get("/dashboard", response_class=HTMLResponse)
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
    limit = 100 if selected in {"baseline", "inventory"} else 75
    offset = (page - 1) * limit
    filter_media = media_values if selected in FILTERABLE_VIEWS else []
    filter_missing = missing_values if selected in FILTERABLE_VIEWS else []
    filter_valid_for = valid_for_values if selected in FILTERABLE_VIEWS else []
    filter_reasons = reason_values if selected in FILTERABLE_VIEWS else []
    filter_hide_any = hide_any_primary if selected in FILTERABLE_VIEWS else False
    items, filtered_total = _filtered_dashboard_items(
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
    active_imports = request.app.state.db.active_imports_by_item_ids([int(item["id"]) for item in items])
    for item in items:
        active_import = active_imports.get(int(item["id"]))
        item["active_import"] = dict(active_import) if active_import is not None else {}
    service_snapshot = request.app.state.service.snapshot()
    counts = _effective_status_counts(request.app.state.db, request.app.state.db.status_counts())
    context = {
        **_shell_context(request, section="dashboard", view=selected, q=search_query, service_snapshot=service_snapshot, counts=counts),
        "request": request,
        "items": items,
        "view": selected,
        "counts": counts,
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
            "displayed": len(items),
            "limit": limit,
            "view": selected,
            "q": search_query,
            "label": {
                "baseline": "baseline",
                "candidates": "candidate",
                "covered": "covered",
                "rejected": "rejected",
                "blocked": "blocked",
                "skipped": "skipped",
                "manual": "manual review",
            }.get(selected, selected.replace("_", " ")),
        },
        "pagination": {
            "page": page,
            "limit": limit,
            "offset": offset,
            "total": filtered_total,
            "start": offset + 1 if filtered_total else 0,
            "end": offset + len(items),
            "prev_url": _dashboard_url(
                selected, page - 1, filter_media, filter_missing, filter_valid_for, filter_reasons, filter_hide_any, q=search_query
            )
            if page > 1
            else "",
            "next_url": _dashboard_url(
                selected, page + 1, filter_media, filter_missing, filter_valid_for, filter_reasons, filter_hide_any, q=search_query
            )
            if offset + len(items) < filtered_total
            else "",
        },
        "current_url": _dashboard_url(selected, page, filter_media, filter_missing, filter_valid_for, filter_reasons, filter_hide_any, q=search_query),
        "problem_view": selected in {"blocked", "manual", "rejected", "errors"},
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
    cfg = request.app.state.config_manager.load()
    item = _row_detail_dict(row, _coverage_for_row(request.app.state.db, row))
    active_import = request.app.state.db.active_import_for_item(item_id)
    item["active_import"] = dict(active_import) if active_import is not None else {}
    item["active_reports"] = [_report_payload(report) for report in request.app.state.db.list_reports(item_id=item_id)]
    item["attempted_reports"] = [
        _report_payload(report) for report in request.app.state.db.list_reports(state="attempted", item_id=item_id, limit=50)
    ]
    item["resolved_reports"] = [
        _report_payload(report) for report in request.app.state.db.list_reports(state="resolved", item_id=item_id, limit=50)
    ]
    return templates.TemplateResponse(
        request,
        "item.html",
        {
            **_shell_context(request, section="items"),
            "request": request,
            "item": item,
            "reporting_stages": REPORTING_STAGES,
            "next_item_url": _next_item_url(request.app.state.db, item),
            "upload_console_configured": bool(cfg.upload_assistant.url and request.app.state.secrets.has("ua_bearer_token")),
            "upload_console_session": request.app.state.upload_console.snapshot(),
        },
    )


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, state_filter: str = Query("active", alias="state")) -> HTMLResponse:
    state_value = _sanitize_report_state(state_filter)
    reports = [_report_payload(report) for report in request.app.state.db.list_reports(state=state_value, limit=500)]
    counts = request.app.state.db.report_counts()
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            **_shell_context(request, section="reports", view="reports"),
            "request": request,
            "report_state": state_value,
            "report_tabs": REPORT_TABS,
            "report_counts": counts,
            "report_groups": _report_groups(reports),
            "current_url": f"/reports?state={state_value}",
        },
    )


@app.get("/imports", response_class=HTMLResponse)
async def queued_imports(
    request: Request,
    view: str = Query("queue"),
    page: int = Query(1, ge=1),
) -> HTMLResponse:
    selected = view if view in IMPORT_VIEW_STATUSES else "queue"
    if selected != view:
        return RedirectResponse(url=_imports_url(selected, 1), status_code=status.HTTP_303_SEE_OTHER)
    statuses = IMPORT_VIEW_STATUSES[selected]
    total = request.app.state.db.count_imports(statuses)
    max_page = max(1, (total + IMPORT_PAGE_SIZE - 1) // IMPORT_PAGE_SIZE)
    if page > max_page:
        return RedirectResponse(url=_imports_url(selected, max_page), status_code=status.HTTP_303_SEE_OTHER)
    offset = (page - 1) * IMPORT_PAGE_SIZE
    rows = request.app.state.db.list_imports(statuses=statuses, limit=IMPORT_PAGE_SIZE, offset=offset)
    import_counts = request.app.state.db.queued_import_counts()
    tabs = []
    for tab in IMPORT_TABS:
        key = str(tab["key"])
        if key == "queue":
            count = int(import_counts.get("active") or 0)
        else:
            count = int(import_counts.get(key) or 0)
        tabs.append(
            {
                **tab,
                "count": count,
                "href": _imports_url(key, 1),
                "selected": key == selected,
            }
        )
    current_url = _imports_url(selected, page)
    return templates.TemplateResponse(
        request,
        "imports.html",
        {
            **_shell_context(request, section="imports", view="imports"),
            "request": request,
            "imports": [dict(row) for row in rows],
            "import_counts": import_counts,
            "import_tabs": tabs,
            "import_view": selected,
            "pagination": {
                "page": page,
                "limit": IMPORT_PAGE_SIZE,
                "total": total,
                "start": offset + 1 if total else 0,
                "end": offset + len(rows),
                "prev_url": _imports_url(selected, page - 1) if page > 1 else "",
                "next_url": _imports_url(selected, page + 1) if offset + len(rows) < total else "",
                "show": total > IMPORT_PAGE_SIZE,
            },
            "current_url": current_url,
        },
    )


@app.post("/imports/run-pending")
async def run_pending_imports(request: Request, return_to: str = Form("/imports?view=queue&page=1")) -> RedirectResponse:
    if request.app.state.db.has_pending_imports():
        await request.app.state.service.request_queued_import_run()
    return RedirectResponse(url=_safe_local_redirect(return_to, "/imports?view=queue&page=1"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/imports/{import_id}/cancel")
async def cancel_import(
    request: Request,
    import_id: int,
    view: str = Form("queue"),
    page: int = Form(1),
) -> RedirectResponse:
    request.app.state.db.cancel_import(import_id)
    return RedirectResponse(
        url=_clamped_imports_url(request.app.state.db, view, page),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/items/{item_id}/recheck")
async def recheck_item(item_id: int, return_to: str = Form("")) -> RedirectResponse:
    app.state.db.requeue(item_id)
    return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/items/{item_id}/grab-nfo")
async def grab_item_nfo(request: Request, item_id: int, return_to: str = Form("")) -> RedirectResponse:
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    nfo = await _grab_nfo_for_row(row, request)
    checks = _check_results(row["check_results"])
    request.app.state.db.update_check_results(item_id, merge_check_results(checks, nfo=nfo))
    return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}#discovarr"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/items/{item_id}/rename-video-file")
async def rename_item_video_file(
    request: Request,
    item_id: int,
    old_path: str = Form(""),
    new_name: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    item = _row_detail_dict(row, _coverage_for_row(request.app.state.db, row))
    video_files = _video_files_for_item(item)
    allowed = {str(file.get("path") or "") for file in video_files.get("files") or [] if isinstance(file, dict)}
    source = Path(old_path)
    if str(source) not in allowed or not source.is_file():
        raise HTTPException(status_code=404, detail="Video file not found")
    filename = Path(new_name.strip()).name
    if not filename or filename != new_name.strip() or Path(filename).suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="New name must be a video filename in the same folder")
    target = source.with_name(filename)
    if target == source:
        return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}#overview"), status_code=status.HTTP_303_SEE_OTHER)
    if target.exists():
        raise HTTPException(status_code=409, detail="A file with that name already exists")
    try:
        source.rename(target)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not rename video file: {exc}") from exc
    request.app.state.db.requeue(item_id)
    return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}#overview"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/items/{item_id}/reports")
async def create_item_report_form(
    request: Request,
    item_id: int,
    stage: str = Form("Other"),
    notes: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    request.app.state.db.create_report(item_id, str(row["name"] or ""), _sanitize_report_stage(stage), notes)
    return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}#reporting"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/items/{item_id}/reject")
async def reject_item_form(
    request: Request,
    item_id: int,
    stage: str = Form("Tracker Moderation"),
    notes: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    if not str(notes or "").strip():
        raise HTTPException(status_code=400, detail="Rejected reason is required")
    report_id = request.app.state.db.reject_item(item_id, _sanitize_report_stage(stage), notes)
    if report_id is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}#reporting"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/reports/attempt")
async def attempt_reports_form(request: Request, report_ids: Optional[List[int]] = Form(None), return_to: str = Form("")) -> RedirectResponse:
    request.app.state.db.mark_reports_attempted(report_ids or [])
    return RedirectResponse(url=_safe_local_redirect(return_to, "/reports?state=active"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/reports/{report_id}/attempt")
async def attempt_report_form(request: Request, report_id: int, return_to: str = Form("")) -> RedirectResponse:
    report = request.app.state.db.get_report(report_id)
    if report is None or str(report["state"]) == "deleted":
        raise HTTPException(status_code=404, detail="Report not found")
    if not request.app.state.db.mark_report_attempted(report_id):
        raise HTTPException(status_code=404, detail="Report not found")
    return RedirectResponse(
        url=_safe_local_redirect(return_to, f"/items/{int(report['item_id'])}#reporting"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/reports/{report_id}/resolve")
async def resolve_report_form(request: Request, report_id: int, return_to: str = Form("")) -> RedirectResponse:
    report = request.app.state.db.get_report(report_id)
    if report is None or str(report["state"]) == "deleted":
        raise HTTPException(status_code=404, detail="Report not found")
    request.app.state.db.resolve_report(report_id)
    return RedirectResponse(
        url=_safe_local_redirect(return_to, f"/items/{int(report['item_id'])}#reporting"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/reports/{report_id}/delete")
async def delete_report_form(request: Request, report_id: int, return_to: str = Form("")) -> RedirectResponse:
    report = request.app.state.db.get_report(report_id)
    if report is None or str(report["state"]) == "deleted":
        raise HTTPException(status_code=404, detail="Report not found")
    request.app.state.db.delete_report(report_id)
    return RedirectResponse(
        url=_safe_local_redirect(return_to, f"/items/{int(report['item_id'])}#reporting"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/items/{item_id}/upload-assistant/queue")
async def queue_item_upload_assistant_form(request: Request, item_id: int, return_to: str = Form("")) -> RedirectResponse:
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    existing = request.app.state.db.active_import_for_item(item_id)
    if existing is not None:
        return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}"), status_code=status.HTTP_303_SEE_OTHER)
    cfg = request.app.state.config_manager.load()
    item = _row_detail_dict(row, _coverage_for_row(request.app.state.db, row))
    if not item.get("can_upload"):
        return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}"), status_code=status.HTTP_303_SEE_OTHER)
    console = item["upload_console"]
    path = str(console.get("path") or "").strip()
    if cfg.upload_assistant.url and request.app.state.secrets.has("ua_bearer_token") and path:
        request.app.state.db.enqueue_import(
            item_id=item_id,
            item_name=str(item.get("name") or f"Item {item_id}"),
            path=path,
            args=_with_unattended_arg(str(console.get("args") or "")),
        )
    return RedirectResponse(url=_safe_local_redirect(return_to, f"/items/{item_id}"), status_code=status.HTTP_303_SEE_OTHER)


@app.post("/api/items/{item_id}/upload-assistant/execute")
async def execute_item_upload_assistant(request: Request, item_id: int) -> Any:
    row = request.app.state.db.get_item(item_id)
    if row is None:
        return JSONResponse({"error": "Item not found", "success": False}, status_code=404)
    cfg = request.app.state.config_manager.load()
    if not cfg.upload_assistant.url or not request.app.state.secrets.has("ua_bearer_token"):
        return JSONResponse({"error": "Upload Assistant is not configured.", "success": False}, status_code=400)

    item = _row_detail_dict(row, _coverage_for_row(request.app.state.db, row))
    if not item.get("can_upload"):
        return JSONResponse({"error": "This item is not uploadable in its current status.", "success": False}, status_code=400)
    console = item["upload_console"]
    path = str(console.get("path") or "").strip()
    if not path:
        return JSONResponse({"error": "No Upload Assistant path is available for this item.", "success": False}, status_code=400)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    args = _upload_payload_args(payload, console)

    session, busy = await request.app.state.upload_console.start(
        item_id=item_id,
        path=path,
        args=args,
        config=cfg,
        secrets=request.app.state.secrets,
    )
    if session is None:
        owner = busy.get("owner") if isinstance(busy.get("owner"), dict) else {}
        message = "Check running" if owner.get("kind") == "check" else str(busy.get("message") or "Upload Assistant is busy.")
        return JSONResponse({"error": message, "success": False, "owner": owner}, status_code=409)

    return StreamingResponse(
        session.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-UA-Session-ID": session.session_id},
    )


@app.post("/api/items/{item_id}/upload-assistant/queue")
async def queue_item_upload_assistant(request: Request, item_id: int) -> JSONResponse:
    row = request.app.state.db.get_item(item_id)
    if row is None:
        return JSONResponse({"error": "Item not found", "success": False}, status_code=404)
    cfg = request.app.state.config_manager.load()
    if not cfg.upload_assistant.url or not request.app.state.secrets.has("ua_bearer_token"):
        return JSONResponse({"error": "Upload Assistant is not configured.", "success": False}, status_code=400)

    existing = request.app.state.db.active_import_for_item(item_id)
    if existing is not None:
        return JSONResponse(
            {
                "success": True,
                "id": int(existing["id"]),
                "args": str(existing["args"] or ""),
                "already_queued": True,
                "status": str(existing["status"] or ""),
            }
        )

    item = _row_detail_dict(row, _coverage_for_row(request.app.state.db, row))
    if not item.get("can_upload"):
        return JSONResponse({"error": "This item is not uploadable in its current status.", "success": False}, status_code=400)
    console = item["upload_console"]
    path = str(console.get("path") or "").strip()
    if not path:
        return JSONResponse({"error": "No Upload Assistant path is available for this item.", "success": False}, status_code=400)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    args = _upload_payload_args(payload, console)
    queued_args = _with_unattended_arg(args)
    import_id = request.app.state.db.enqueue_import(
        item_id=item_id,
        item_name=str(item.get("name") or f"Item {item_id}"),
        path=path,
        args=queued_args,
    )
    return JSONResponse({"success": True, "id": import_id, "args": queued_args})


@app.get("/api/items/{item_id}/upload-assistant/stream")
async def stream_item_upload_assistant(request: Request, item_id: int, session_id: str = "") -> Any:
    session = request.app.state.upload_console.get(session_id)
    if session is None or session.item_id != item_id:
        return JSONResponse({"error": "No active Upload Assistant session for this item.", "success": False}, status_code=404)
    return StreamingResponse(
        session.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-UA-Session-ID": session.session_id},
    )


@app.post("/api/items/{item_id}/upload-assistant/input")
async def send_item_upload_assistant_input(request: Request, item_id: int) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    session_id = str(payload.get("session_id") or "") if isinstance(payload, dict) else ""
    user_input = str(payload.get("input") or "") if isinstance(payload, dict) else ""
    session = request.app.state.upload_console.get(session_id)
    if session is None or session.item_id != item_id:
        return JSONResponse({"error": "No active Upload Assistant session for this item.", "success": False}, status_code=404)
    try:
        result = await session.send_input(user_input)
    except httpx.HTTPStatusError as exc:
        return JSONResponse({"error": f"Upload Assistant HTTP error {exc.response.status_code}", "success": False}, status_code=exc.response.status_code)
    except Exception as exc:
        return JSONResponse({"error": str(exc), "success": False}, status_code=500)
    return JSONResponse(result)


@app.post("/api/items/{item_id}/upload-assistant/kill")
async def kill_item_upload_assistant(request: Request, item_id: int) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    session_id = str(payload.get("session_id") or "") if isinstance(payload, dict) else ""
    session = request.app.state.upload_console.get(session_id)
    if session is None or session.item_id != item_id:
        return JSONResponse({"error": "No active Upload Assistant session for this item.", "success": False}, status_code=404)
    try:
        result = await session.kill()
    except httpx.HTTPStatusError as exc:
        return JSONResponse({"error": f"Upload Assistant HTTP error {exc.response.status_code}", "success": False}, status_code=exc.response.status_code)
    except Exception as exc:
        return JSONResponse({"error": str(exc), "success": False}, status_code=500)
    return JSONResponse(result)


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
        "rejected": "rejected",
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


@app.get("/config/rules", response_class=HTMLResponse)
async def rules_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "rules.html", _rules_context(request))


@app.post("/config/rules/replay", response_class=HTMLResponse)
async def replay_rules(request: Request, mode: str = Form("preview")) -> HTMLResponse:
    apply_changes = mode == "apply"
    replay_result = request.app.state.db.reevaluate_stored_decisions(apply=apply_changes)
    changed = int(replay_result.get("changed") or 0)
    if apply_changes:
        message = f"Applied {changed} stored decision update{'' if changed == 1 else 's'}."
    else:
        message = f"Preview found {changed} stored decision update{'' if changed == 1 else 's'}."
    return templates.TemplateResponse(
        request,
        "rules.html",
        _rules_context(request, message=message, replay_result=replay_result),
    )


@app.post("/config", response_class=HTMLResponse)
async def save_config(
    request: Request,
    qui_url: str = Form(""),
    qui_instance_id: str = Form("1"),
    qui_page_limit: str = Form("200"),
    qui_api_key: str = Form(""),
    clear_qui_api_key: Optional[str] = Form(None),
    mediainfo_enabled: Optional[str] = Form(None),
    mediainfo_binary_path: str = Form("mediainfo"),
    mediainfo_timeout_seconds: str = Form("60"),
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
    max_qui_poll_pages: str = Form("100"),
    max_mediainfo_files_per_check: str = Form("8"),
    arr_search_timeout_seconds: str = Form("300"),
    arr_metadata_cache_seconds: str = Form("900"),
    recheck_cooldown_hours: str = Form("24"),
    max_error_retries: str = Form("3"),
    error_backoff_minutes: str = Form("15, 60, 360"),
    high_quality_trackers: Optional[List[str]] = Form(None),
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
    cfg.mediainfo.enabled = mediainfo_enabled == "on"
    cfg.mediainfo.binary_path = mediainfo_binary_path.strip() or "mediainfo"
    cfg.mediainfo.timeout_seconds = _as_int(mediainfo_timeout_seconds, cfg.mediainfo.timeout_seconds, minimum=1)
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
    cfg.safety.max_qui_poll_pages = _as_int(
        max_qui_poll_pages,
        cfg.safety.max_qui_poll_pages,
        minimum=1,
    )
    cfg.safety.max_mediainfo_files_per_check = _as_int(
        max_mediainfo_files_per_check,
        cfg.safety.max_mediainfo_files_per_check,
        minimum=1,
    )
    cfg.safety.arr_search_timeout_seconds = _as_int(
        arr_search_timeout_seconds,
        cfg.safety.arr_search_timeout_seconds,
        minimum=5,
    )
    cfg.safety.arr_metadata_cache_seconds = _as_int(
        arr_metadata_cache_seconds,
        cfg.safety.arr_metadata_cache_seconds,
        minimum=0,
    )
    cfg.safety.recheck_cooldown_hours = _as_int(recheck_cooldown_hours, cfg.safety.recheck_cooldown_hours, minimum=1)
    cfg.safety.max_error_retries = _as_int(max_error_retries, cfg.safety.max_error_retries, minimum=0)
    cfg.safety.error_backoff_minutes = [
        _as_int(item, 15, minimum=1)
        for item in parse_csv(error_backoff_minutes)
    ] or [15, 60, 360]
    cfg.safety.high_quality_trackers = _dedupe_trackers(high_quality_trackers or [])
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
    policy_reapply = request.app.state.db.reapply_release_group_policy(cfg.tracker_policies)
    message = "Settings saved."
    if policy_reapply["items"]:
        message = (
            f"Settings saved. Reapplied release group policy to {policy_reapply['items']} candidate"
            f"{'' if policy_reapply['items'] == 1 else 's'}"
            f"; {policy_reapply['blocked_trackers']} tracker"
            f"{'' if policy_reapply['blocked_trackers'] == 1 else 's'} blocked."
        )
    return templates.TemplateResponse(request, "config.html", _config_context(request, message=message))


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
            "counts": _effective_status_counts(request.app.state.db, request.app.state.db.status_counts()),
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


@app.post("/api/items/{item_id}/reports")
async def api_create_item_report(request: Request, item_id: int) -> JSONResponse:
    _require_api_auth(request)
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    report_id = request.app.state.db.create_report(
        item_id=item_id,
        item_name=str(row["name"] or ""),
        stage=_sanitize_report_stage(str(payload.get("stage") or "Other") if isinstance(payload, dict) else "Other"),
        notes=str(payload.get("notes") or "") if isinstance(payload, dict) else "",
    )
    report = request.app.state.db.get_report(report_id)
    return JSONResponse({"success": True, "report": _report_payload(report)}, status_code=201)


@app.post("/api/items/{item_id}/reject")
async def api_reject_item(request: Request, item_id: int) -> JSONResponse:
    _require_api_auth(request)
    row = request.app.state.db.get_item(item_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    notes = str(payload.get("notes") or "") if isinstance(payload, dict) else ""
    if not notes.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rejected reason is required")
    report_id = request.app.state.db.reject_item(
        item_id=item_id,
        stage=_sanitize_report_stage(str(payload.get("stage") or "Tracker Moderation") if isinstance(payload, dict) else "Tracker Moderation"),
        notes=notes,
    )
    if report_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
    report = request.app.state.db.get_report(report_id)
    rejected = request.app.state.db.get_item(item_id)
    return JSONResponse(
        {
            "success": True,
            "report": _report_payload(report),
            "item": _api_item_summary(_row_detail_dict(rejected, _coverage_for_row(request.app.state.db, rejected))),
        }
    )


@app.get("/api/reports")
async def api_reports(
    request: Request,
    state_filter: str = Query("active", alias="state"),
    item_id: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=500),
) -> JSONResponse:
    _require_api_auth(request)
    state_value = _sanitize_report_state(state_filter)
    reports = request.app.state.db.list_reports(state=state_value, item_id=item_id, limit=limit)
    return JSONResponse({"reports": [_report_payload(report) for report in reports], "state": state_value, "count": len(reports)})


@app.get("/api/reports/{report_id}")
async def api_report_detail(request: Request, report_id: int) -> JSONResponse:
    _require_api_auth(request)
    report = request.app.state.db.get_report(report_id)
    if report is None or str(report["state"]) == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return JSONResponse({"report": _report_payload(report)})


@app.post("/api/reports/{report_id}/attempt")
async def api_attempt_report(request: Request, report_id: int) -> JSONResponse:
    _require_api_auth(request)
    if not request.app.state.db.mark_report_attempted(report_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    report = request.app.state.db.get_report(report_id)
    return JSONResponse({"success": True, "report": _report_payload(report)})


@app.post("/api/reports/{report_id}/resolve")
async def api_resolve_report(request: Request, report_id: int) -> JSONResponse:
    _require_api_auth(request)
    if not request.app.state.db.resolve_report(report_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    report = request.app.state.db.get_report(report_id)
    return JSONResponse({"success": True, "report": _report_payload(report)})


@app.delete("/api/reports/{report_id}")
async def api_delete_report(request: Request, report_id: int) -> JSONResponse:
    _require_api_auth(request)
    if not request.app.state.db.delete_report(report_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return JSONResponse({"success": True})


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
