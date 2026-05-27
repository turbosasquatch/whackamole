from __future__ import annotations

import html
import json
import re
from typing import Any, List


def normalize_ua_log(log: str) -> str:
    lines: List[str] = []
    for raw_line in (log or "").splitlines():
        line = normalize_ua_event_line(raw_line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def normalize_ua_event_line(raw_line: str) -> str:
    line = (raw_line or "").strip()
    if not line:
        return ""
    if line.startswith("data:"):
        line = line[5:].strip()

    try:
        payload: Any = json.loads(line)
    except json.JSONDecodeError:
        return _strip_html(line)

    if not isinstance(payload, dict):
        return _strip_html(str(payload))

    event_type = str(payload.get("type") or "")
    if event_type == "keepalive":
        return ""

    data = payload.get("data")
    if data is None:
        return ""
    return _strip_html(str(data))


def _strip_html(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|pre|div|code|li|tr|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
