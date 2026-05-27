from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class UAReduction:
    status: str
    verdict: str
    reason: str
    trackers: List[str] = field(default_factory=list)


def reduce_ua_log(log: str) -> UAReduction:
    text = log or ""
    lowered = text.lower()

    passed = _extract_passed_trackers(text)
    if passed:
        return UAReduction(
            status="candidate",
            verdict="candidate",
            reason=f"UA says these trackers passed checks: {', '.join(passed)}",
            trackers=passed,
        )

    if "found potential dupes on" in lowered:
        trackers = _extract_after_colon(text, "Found potential dupes on")
        return UAReduction(
            status="blocked",
            verdict="dupe",
            reason=f"UA found potential dupes on: {', '.join(trackers) if trackers else 'one or more trackers'}",
            trackers=trackers,
        )

    if "exact match found" in lowered:
        return UAReduction(status="blocked", verdict="exact_match", reason="UA reported an exact match.")

    if "skipped due to specific tracker conditions" in lowered:
        trackers = _extract_after_colon(text, "Skipped due to specific tracker conditions")
        return UAReduction(
            status="blocked",
            verdict="skipped",
            reason=f"UA skipped tracker conditions: {', '.join(trackers) if trackers else 'see log'}",
            trackers=trackers,
        )

    if "not enough successful trackers" in lowered or "no trackers remain" in lowered:
        return UAReduction(status="blocked", verdict="no_tracker_passed", reason="No tracker passed UA checks.")

    if any(marker in lowered for marker in ["traceback", "error in gather_prep", "failed to", "unauthorized"]):
        return UAReduction(status="error", verdict="error", reason="UA returned an error. See log.")

    return UAReduction(status="blocked", verdict="unknown", reason="UA completed without a positive recommendation.")


def _extract_passed_trackers(text: str) -> List[str]:
    match = re.search(r"Trackers passed all checks:\s*(.+)", text, flags=re.IGNORECASE)
    if not match:
        return []
    return _split_tracker_list(match.group(1))


def _extract_after_colon(text: str, label: str) -> List[str]:
    pattern = re.escape(label) + r":\s*(.+)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return []
    return _split_tracker_list(match.group(1))


def _split_tracker_list(value: str) -> List[str]:
    cleaned = re.sub(r"\[[^\]]+\]", "", value)
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", cleaned)
    parts = re.split(r"[,.\n]", cleaned)
    trackers = []
    for part in parts:
        candidate = part.strip(" .:-[]'\"").upper()
        if candidate and len(candidate) <= 12 and candidate.replace("-", "").isalnum():
            trackers.append(candidate)
    return list(dict.fromkeys(trackers))

