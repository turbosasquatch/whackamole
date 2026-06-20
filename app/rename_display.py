from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Iterable as IterableABC
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Sequence


KIND_LABELS = {
    "same_group_arr_title_mismatch": "Arr title mismatch",
    "srrdb_mismatch": "srrDB mismatch",
    "srrdb_verified": "srrDB verified",
    "folder_scene_normalization": "Folder normalization",
    "mapped_root_mismatch": "Mapped root mismatch",
    "empty_title_token": "Empty title token",
    "random_video_basename": "Random video basename",
    "file_group_mismatch": "Release group mismatch",
    "mixed_release_groups": "Mixed release groups",
    "mixed_technical_tail": "Mixed technical tail",
    "folder_name_warning": "Folder name warning",
    "possible_renamed_release": "Possible renamed release",
    "renamed_release_warning": "Rename warning",
}

SCOPE_LABELS = {
    "arr_title": "Discovarr",
    "srrdb": "srrDB",
    "folder": "Folder",
    "file": "Video file",
    "siblings": "Video files",
    "legacy": "Legacy evidence",
}

SCOPE_FIELDS = (
    ("season", "Season"),
    ("episode", "Episode"),
    ("season_pack", "Season pack"),
    ("resolution", "Resolution"),
    ("source_tag", "Source"),
    ("source_provider", "Provider"),
    ("rip_type", "Rip type"),
)


def build_rename_check(rename_detection: Mapping[str, Any] | None) -> Dict[str, Any]:
    rename = rename_detection if isinstance(rename_detection, Mapping) else {}
    evidence = [dict(item) for item in rename.get("evidence", []) if isinstance(item, Mapping)]
    rows: List[Dict[str, Any]] = []
    for index, item in enumerate(evidence):
        rows.extend(_rows_for_evidence(item, index))

    counts = {
        "total": len(rows),
        "high": sum(1 for row in rows if row["confidence"] == "high"),
        "medium": sum(1 for row in rows if row["confidence"] == "medium"),
        "info": sum(1 for row in rows if row["severity"] == "info"),
    }
    status_value = str(rename.get("status") or ("pass" if not rows else "warning"))
    return {
        "version": 1,
        "status": status_value,
        "status_label": _status_label(status_value),
        "state": _status_state(status_value),
        "confidence": str(rename.get("confidence") or ("low" if not rows else "medium")),
        "reason": str(rename.get("reason") or "No Rename Check evidence recorded."),
        "summary_counts": counts,
        "rows": rows,
    }


def _rows_for_evidence(evidence: Mapping[str, Any], index: int) -> List[Dict[str, Any]]:
    kind = str(evidence.get("kind") or "")
    if kind.startswith("srrdb") and isinstance(evidence.get("comparison_pairs"), list):
        rows = []
        for pair_index, pair in enumerate(evidence.get("comparison_pairs") or []):
            if not isinstance(pair, Mapping):
                continue
            row_evidence = dict(evidence)
            row_evidence["value"] = str(pair.get("local_name") or "")
            row_evidence["expected"] = str(pair.get("archived_name") or "")
            row_evidence["local_size"] = pair.get("local_size")
            row_evidence["archived_size"] = pair.get("archived_size")
            row_evidence["pair_status"] = pair.get("status")
            rows.append(_base_row(row_evidence, f"{index}-{pair_index}"))
        if rows:
            return rows
    return [_base_row(evidence, str(index))]


def _base_row(evidence: Mapping[str, Any], row_id: str) -> Dict[str, Any]:
    kind = str(evidence.get("kind") or "rename_evidence")
    confidence = str(evidence.get("confidence") or "low").lower()
    local_value = _local_value(evidence)
    remote_value = _remote_value(evidence)
    diff = _empty_token_diff(local_value) if kind == "empty_title_token" else _diff_values(local_value, remote_value)
    row = {
        "id": f"{kind}-{row_id}",
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind.replace("_", " ").title()),
        "source": str(evidence.get("source") or SCOPE_LABELS.get(str(evidence.get("scope") or ""), "Rename Check")),
        "scope": str(evidence.get("scope") or ""),
        "scope_label": SCOPE_LABELS.get(str(evidence.get("scope") or ""), str(evidence.get("scope") or "Evidence").title()),
        "confidence": confidence,
        "severity": _severity(confidence, kind),
        "severity_label": _severity(confidence, kind).title(),
        "tracker": str(evidence.get("tracker") or ""),
        "local_label": _local_label(kind),
        "remote_label": _remote_label(kind),
        "local_value": local_value,
        "remote_value": remote_value,
        "diff": diff,
        "diff_segments": diff,
        "token_chips": _token_chips(local_value, remote_value),
        "difference_summary": _difference_summary(kind, evidence, local_value, remote_value),
        "reason": str(evidence.get("reason") or ""),
        "meta": _meta_rows(evidence),
        "files": _file_groups(evidence),
    }
    return row


def _local_value(evidence: Mapping[str, Any]) -> str:
    if str(evidence.get("kind") or "") == "file_group_mismatch":
        value = str(evidence.get("file_group") or "")
        if value:
            return value
    for key in ("local_title", "value", "filename"):
        value = str(evidence.get(key) or "")
        if value:
            return value
    return ""


def _remote_value(evidence: Mapping[str, Any]) -> str:
    if str(evidence.get("kind") or "") == "file_group_mismatch":
        value = str(evidence.get("root_group") or evidence.get("expected") or "")
        if value:
            return value
    for key in ("remote_title", "expected"):
        value = str(evidence.get(key) or "")
        if value:
            return value
    return ""


def _local_label(kind: str) -> str:
    if kind.startswith("srrdb"):
        return "Local file"
    if kind == "same_group_arr_title_mismatch":
        return "Our record"
    if kind in {"folder_scene_normalization", "mapped_root_mismatch"}:
        return "Current folder"
    if kind == "file_group_mismatch":
        return "Video release group"
    if kind in {"mixed_release_groups", "mixed_technical_tail"}:
        return "Detected values"
    return "Our file"


def _remote_label(kind: str) -> str:
    if kind.startswith("srrdb"):
        return "srrDB proper file"
    if kind == "same_group_arr_title_mismatch":
        return "Their record"
    if kind == "folder_scene_normalization":
        return "Normalized folder"
    if kind == "mapped_root_mismatch":
        return "Torrent root"
    if kind == "file_group_mismatch":
        return "Folder/root release group"
    if kind in {"mixed_release_groups", "mixed_technical_tail"}:
        return "Expected context"
    if kind == "empty_title_token":
        return "Expected pattern"
    return "Expected value"


def _severity(confidence: str, kind: str) -> str:
    if kind == "srrdb_verified" or confidence == "low":
        return "info"
    return "warning"


def _status_label(status: str) -> str:
    value = str(status or "").replace("_", " ").strip()
    return value.title() if value else "Not Run"


def _status_state(status: str) -> str:
    value = str(status or "").lower()
    if value == "pass":
        return "pass"
    if value in {"manual_review", "warning"}:
        return "warning"
    if value:
        return "neutral"
    return "neutral"


def _difference_summary(kind: str, evidence: Mapping[str, Any], local_value: str, remote_value: str) -> str:
    tracker = str(evidence.get("tracker") or "")
    if kind == "same_group_arr_title_mismatch":
        source = f" on {tracker}" if tracker else ""
        return f"Arr found a same-group release{source} with a different title."
    if kind == "srrdb_mismatch":
        if str(evidence.get("pair_status") or "") == "size_mismatch":
            return "srrDB filename matches, but the archived size differs from the local file."
        return "srrDB archived filename differs from the local filename."
    if kind == "srrdb_verified":
        return "srrDB archived filename matches the local file."
    if kind == "folder_scene_normalization":
        return "Folder name only differs by scene-style normalization."
    if kind == "mapped_root_mismatch":
        return "Mapped folder name differs from the torrent root name."
    if kind == "empty_title_token":
        return "Filename contains an empty title token, usually a doubled separator."
    if kind == "random_video_basename":
        return "Video basename looks random inside a structured release folder."
    if kind == "file_group_mismatch":
        return "Video filename release group differs from the folder/root release group."
    if kind == "mixed_release_groups":
        return "Video files in the same folder use different release groups."
    if kind == "mixed_technical_tail":
        return "Video files in the same folder have inconsistent technical release tails."
    if local_value and remote_value:
        return "Compared values differ."
    return str(evidence.get("reason") or "Rename Check evidence recorded.")


def _diff_values(local_value: str, remote_value: str) -> Dict[str, List[Dict[str, str]]]:
    local = str(local_value or "")
    remote = str(remote_value or "")
    if not local and not remote:
        return {"local": [], "remote": []}
    if not remote:
        return {"local": [{"type": "equal", "text": local}], "remote": []}
    matcher = difflib.SequenceMatcher(a=local, b=remote, autojunk=False)
    local_segments: List[Dict[str, str]] = []
    remote_segments: List[Dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            local_segments.append({"type": "equal", "text": local[i1:i2]})
            remote_segments.append({"type": "equal", "text": remote[j1:j2]})
        elif tag == "delete":
            local_segments.append({"type": "delete", "text": local[i1:i2]})
        elif tag == "insert":
            remote_segments.append({"type": "insert", "text": remote[j1:j2]})
        elif tag == "replace":
            local_segments.append({"type": "replace", "text": local[i1:i2]})
            remote_segments.append({"type": "replace", "text": remote[j1:j2]})
    return {"local": _merge_segments(local_segments), "remote": _merge_segments(remote_segments)}


def _empty_token_diff(value: str) -> Dict[str, List[Dict[str, str]]]:
    text = str(value or "")
    segments: List[Dict[str, str]] = []
    position = 0
    for match in re.finditer(r"([._ -])\1+", text):
        if match.start() > position:
            segments.append({"type": "equal", "text": text[position : match.start()]})
        segments.append({"type": "replace", "text": match.group(0)})
        position = match.end()
    if position < len(text):
        segments.append({"type": "equal", "text": text[position:]})
    return {"local": segments or [{"type": "equal", "text": text}], "remote": []}


def _merge_segments(segments: Sequence[Mapping[str, str]]) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    for segment in segments:
        text = str(segment.get("text") or "")
        kind = str(segment.get("type") or "equal")
        if not text:
            continue
        if merged and merged[-1]["type"] == kind:
            merged[-1]["text"] += text
        else:
            merged.append({"type": kind, "text": text})
    return merged


def _token_chips(local_value: str, remote_value: str) -> List[Dict[str, str]]:
    if not local_value or not remote_value:
        return []
    local_tokens = _tokens(local_value)
    remote_tokens = _tokens(remote_value)
    local_counts = Counter(local_tokens)
    remote_counts = Counter(remote_tokens)
    chips: List[Dict[str, str]] = []
    for token in local_tokens:
        if local_counts[token] > remote_counts[token]:
            chips.append({"side": "local", "label": "Only ours", "token": token})
            local_counts[token] -= 1
    for token in remote_tokens:
        if remote_counts[token] > local_counts[token]:
            chips.append({"side": "remote", "label": "Only theirs", "token": token})
            remote_counts[token] -= 1
    return chips[:16]


def _tokens(value: str) -> List[str]:
    return [token for token in re.split(r"[.\s_\-/()]+", str(value or "")) if token]


def _meta_rows(evidence: Mapping[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for key, label in (
        ("tracker", "Tracker"),
        ("release_group", "Release group"),
        ("local_key", "Our normalized key"),
        ("remote_key", "Their normalized key"),
        ("queried_name", "srrDB query"),
        ("pair_status", "Comparison status"),
        ("filename", "Filename"),
        ("file_group", "Video release group"),
        ("root_group", "Folder/root release group"),
        ("local_size", "Local size"),
        ("archived_size", "Archived size"),
    ):
        value = evidence.get(key)
        if value not in ("", None, [], {}):
            rows.append({"label": label, "value": _format_meta_value(value, key)})
    rows.extend(_scope_meta(evidence.get("local_scope"), evidence.get("remote_scope")))
    return rows


def _scope_meta(local_scope: Any, remote_scope: Any) -> List[Dict[str, str]]:
    if not isinstance(local_scope, Mapping) and not isinstance(remote_scope, Mapping):
        return []
    local = local_scope if isinstance(local_scope, Mapping) else {}
    remote = remote_scope if isinstance(remote_scope, Mapping) else {}
    rows: List[Dict[str, str]] = []
    for key, label in SCOPE_FIELDS:
        left = local.get(key)
        right = remote.get(key)
        if left in ("", None) and right in ("", None):
            continue
        marker = "same" if str(left) == str(right) else "differs"
        rows.append({"label": label, "value": f"{left or '-'} / {right or '-'} ({marker})"})
    return rows


def _format_meta_value(value: Any, key: str) -> str:
    if key.endswith("size"):
        try:
            amount = int(value or 0)
        except (TypeError, ValueError):
            return str(value)
        return _format_bytes(amount) if amount else "-"
    return str(value)


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(amount) < 1024.0 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024.0
    return f"{value} B"


def _file_groups(evidence: Mapping[str, Any]) -> List[Dict[str, Any]]:
    groups = evidence.get("groups")
    if isinstance(groups, Mapping):
        return [
            {"label": str(label), "items": [str(item) for item in items if str(item)]}
            for label, items in groups.items()
            if isinstance(items, IterableABC) and not isinstance(items, (str, bytes))
        ]
    signatures = evidence.get("signatures")
    if isinstance(signatures, Mapping):
        return [
            {"label": str(label), "items": [str(item) for item in items if str(item)]}
            for label, items in signatures.items()
            if isinstance(items, IterableABC) and not isinstance(items, (str, bytes))
        ]
    files = evidence.get("files")
    if isinstance(files, list):
        return [{"label": "Files", "items": [str(item) for item in files if str(item)]}]
    value = str(evidence.get("value") or "")
    if evidence.get("kind") in {"file_group_mismatch", "empty_title_token", "random_video_basename"} and value:
        return [{"label": "File", "items": [PurePosixPath(value).name]}]
    return []
