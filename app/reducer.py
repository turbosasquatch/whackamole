from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from app.ua_logs import normalize_ua_log


TRACKER_BUCKETS = ("passed", "dupe", "skipped", "error")
INTERRUPTION_MARKERS = (
    "received sigterm",
    "web ui server stopped",
    "shutdown complete",
    "error during terminal reset",
    "i/o operation on closed file",
)


@dataclass
class UAReduction:
    status: str
    verdict: str
    reason: str
    tracker_results: Dict[str, List[str]] = field(
        default_factory=lambda: {bucket: [] for bucket in TRACKER_BUCKETS}
    )

    @property
    def trackers(self) -> List[str]:
        for bucket in TRACKER_BUCKETS:
            values = self.tracker_results.get(bucket, [])
            if values:
                return values
        return []


def reduce_ua_log(log: str) -> UAReduction:
    text = normalize_ua_log(log)
    lowered = text.lower()
    tracker_results = _extract_tracker_results(text)

    passed = tracker_results["passed"]
    if passed:
        return UAReduction(
            status="candidate",
            verdict="candidate",
            reason=f"UA says this is missing/upload-worthy on: {', '.join(passed)}",
            tracker_results=tracker_results,
        )

    if tracker_results["dupe"]:
        trackers = tracker_results["dupe"]
        return UAReduction(
            status="blocked",
            verdict="dupe",
            reason=f"UA found potential dupes on: {', '.join(trackers) if trackers else 'one or more trackers'}",
            tracker_results=tracker_results,
        )

    if "exact match found" in lowered:
        return UAReduction(
            status="blocked",
            verdict="exact_match",
            reason="UA reported an exact match.",
            tracker_results=tracker_results,
        )

    if tracker_results["skipped"]:
        trackers = tracker_results["skipped"]
        return UAReduction(
            status="blocked",
            verdict="skipped",
            reason=f"UA skipped tracker conditions: {', '.join(trackers) if trackers else 'see log'}",
            tracker_results=tracker_results,
        )

    if "not enough successful trackers" in lowered or "no trackers remain" in lowered:
        return UAReduction(
            status="blocked",
            verdict="no_tracker_passed",
            reason="No tracker passed UA checks.",
            tracker_results=tracker_results,
        )

    if "no video files found" in lowered:
        return UAReduction(
            status="manual_review",
            verdict="no_video_files",
            reason="UA could not find video files at the mapped path. Check the torrent path/mount or rerun after mover maintenance.",
            tracker_results=tracker_results,
        )

    if any(marker in lowered for marker in INTERRUPTION_MARKERS):
        tracker_results["error"] = ["UA"]
        return UAReduction(
            status="error",
            verdict="ua_interrupted",
            reason="UA was interrupted before producing a clear decision. Whackamole will retry after backoff.",
            tracker_results=tracker_results,
        )

    if any(marker in lowered for marker in ["traceback", "error in gather_prep", "failed to", "unauthorized"]):
        tracker_results["error"] = ["UA"]
        return UAReduction(
            status="error",
            verdict="error",
            reason="UA returned an error. See log.",
            tracker_results=tracker_results,
        )

    return UAReduction(
        status="blocked",
        verdict="unknown",
        reason="UA completed without a positive recommendation.",
        tracker_results=tracker_results,
    )


def _extract_tracker_results(text: str) -> Dict[str, List[str]]:
    return {
        "passed": _extract_after_colon(text, "Trackers passed all checks"),
        "dupe": _extract_after_colon(text, "Found potential dupes on"),
        "skipped": _extract_after_colon(text, "Skipped due to specific tracker conditions"),
        "error": [],
    }


def _extract_after_colon(text: str, label: str) -> List[str]:
    pattern = re.escape(label) + r":?\s*([^\n\r]+)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return []
    return _split_tracker_list(match.group(1))


def _split_tracker_list(value: str) -> List[str]:
    cleaned = normalize_ua_log(value)
    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", cleaned)
    parts = re.split(r",|\band\b", cleaned)
    trackers = []
    for part in parts:
        candidate = part.strip(" .:-[]'\"").upper()
        if candidate and len(candidate) <= 12 and candidate.replace("-", "").isalnum():
            trackers.append(candidate)
    return list(dict.fromkeys(trackers))
