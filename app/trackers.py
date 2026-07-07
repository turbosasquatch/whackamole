from __future__ import annotations

import re
from typing import Dict, Optional, Sequence


PRIMARY_TRACKERS = ("DP", "ULCX", "IHD", "LUME")

TRACKER_LABELS = {
    "DP": "DP",
    "ULCX": "ULCX",
    "IHD": "IHD",
    "LUME": "LUME",
    "DC": "DC",
    "TL": "TL",
    "IPT": "IPT",
    "SP": "Seedpool",
}

TRACKER_ALIASES: Dict[str, Sequence[str]] = {
    "DP": ("darkpeers", "darkpeer", "dp"),
    "ULCX": ("upload.cx", "uploadcx", "ulcx"),
    "IHD": ("infinityhd", "ihd"),
    "LUME": ("lume", "luminarr", "luminarr api", "luminarrapi"),
    "DC": ("digitalcore", "dc"),
    "TL": ("torrentleech", "tleechreload", "tl"),
    "IPT": ("iptorrents", "ipt"),
    "SP": ("seedpool", "sp"),
}

PRIMARY_TRACKER_ORDER = {tracker: index for index, tracker in enumerate(PRIMARY_TRACKERS)}


def compact_tracker_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def canonical_tracker(value: str, exact_short: bool = False) -> Optional[str]:
    compact = compact_tracker_name(value)
    if not compact:
        return None
    for canonical, aliases in TRACKER_ALIASES.items():
        alias_compacts = {compact_tracker_name(alias) for alias in aliases}
        if compact == compact_tracker_name(canonical) or compact in alias_compacts:
            return canonical
        if not exact_short and any(len(alias) >= 4 and alias in compact for alias in alias_compacts):
            return canonical
    return None


def canonicalize_tracker_name(value: str) -> str:
    return canonical_tracker(value) or str(value or "").strip().upper()


def tracker_label(canonical: str) -> str:
    return TRACKER_LABELS.get(canonical, canonical)


def is_primary_tracker(canonical: str) -> bool:
    return canonical in PRIMARY_TRACKERS


def tracker_payload(canonical: str) -> Dict[str, object]:
    return {
        "key": canonical,
        "label": tracker_label(canonical),
        "primary": is_primary_tracker(canonical),
    }
