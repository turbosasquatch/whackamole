from __future__ import annotations

from typing import Any, Dict, List, Mapping

import httpx

from app.upload_console import _valid_for_trackers

_SOURCE_BUCKET_ORDER = ("passed", "covered", "dupe", "skipped", "error")


def _format_bytes(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "-"
    if amount <= 0:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(amount) < 1024.0 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024.0
    return f"{value} B"


def _source_trackers(tracker_groups: Mapping[str, Any]) -> str:
    for bucket in _SOURCE_BUCKET_ORDER:
        trackers = tracker_groups.get(bucket) or []
        if trackers:
            return ", ".join(str(tracker) for tracker in trackers)
    return "Unknown"


def _flag_lines(check_results: Mapping[str, Any]) -> str:
    flags = check_results.get("flags") if isinstance(check_results.get("flags"), list) else []
    lines = [
        f"{flag.get('label') or flag.get('key')} ({flag.get('severity') or 'info'})"
        for flag in flags
        if isinstance(flag, Mapping) and (flag.get("label") or flag.get("key"))
    ]
    return "\n".join(lines) if lines else "None"


def build_candidate_embed(
    *,
    event_title: str,
    item: Mapping[str, Any],
    tracker_groups: Mapping[str, Any],
    arr_result: Mapping[str, Any],
    check_results: Mapping[str, Any],
    reason: str,
) -> Dict[str, Any]:
    item = dict(item)
    tracker_groups = dict(tracker_groups)
    item_name = str(item.get("name") or "")
    valid_for = _valid_for_trackers(item, tracker_groups, dict(arr_result), dict(check_results))
    fields: List[Dict[str, Any]] = [
        {"name": "Source", "value": _source_trackers(tracker_groups), "inline": True},
        {"name": "Valid For", "value": ", ".join(valid_for) if valid_for else "None", "inline": True},
        {"name": "Size", "value": _format_bytes(item.get("size")), "inline": True},
        {"name": "Warnings / Errors", "value": _flag_lines(check_results), "inline": False},
    ]
    return {
        "title": event_title,
        "description": item_name + (f"\n{reason}" if reason else ""),
        "fields": fields,
    }


async def send_discord_notification(webhook_url: str, embed: Dict[str, Any]) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(webhook_url, json={"embeds": [embed]})
        response.raise_for_status()
