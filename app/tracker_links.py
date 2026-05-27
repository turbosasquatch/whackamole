from __future__ import annotations

from typing import Dict, List


TRACKER_BASE_URLS: Dict[str, str] = {
    "DP": "https://darkpeers.org",
    "IHD": "https://infinityhd.net",
    "ULCX": "https://upload.cx",
}


def tracker_links(tracker: str) -> List[Dict[str, str]]:
    base_url = TRACKER_BASE_URLS.get(tracker.upper())
    if not base_url:
        return []
    return [
        {"label": "Open", "url": base_url},
        {"label": "Browse", "url": f"{base_url}/torrents"},
        {"label": "Upload", "url": f"{base_url}/upload"},
    ]
