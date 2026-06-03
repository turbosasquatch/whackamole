from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


CHECK_RESULT_VERSION = 1


@dataclass
class CheckResults:
    version: int = CHECK_RESULT_VERSION
    media: Dict[str, Any] = field(default_factory=dict)
    nfo: Dict[str, Any] = field(default_factory=dict)
    ua: Dict[str, Any] = field(default_factory=dict)
    arr: Dict[str, Any] = field(default_factory=dict)
    srrdb: Dict[str, Any] = field(default_factory=dict)
    release_group_policy: Dict[str, Any] = field(default_factory=dict)
    coverage_resolution: Dict[str, Any] = field(default_factory=dict)
    flags: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=lambda: {"stages": [], "last_error": {}})

    @classmethod
    def from_any(cls, value: Any) -> "CheckResults":
        payload = value if isinstance(value, Mapping) else {}
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
        stages = diagnostics.get("stages") if isinstance(diagnostics.get("stages"), list) else []
        last_error = diagnostics.get("last_error") if isinstance(diagnostics.get("last_error"), Mapping) else {}
        return cls(
            version=_int_value(payload.get("version"), CHECK_RESULT_VERSION),
            media=_dict_value(payload.get("media")),
            nfo=_dict_value(payload.get("nfo")),
            ua=_dict_value(payload.get("ua")),
            arr=_dict_value(payload.get("arr")),
            srrdb=_dict_value(payload.get("srrdb")),
            release_group_policy=_dict_value(payload.get("release_group_policy")),
            coverage_resolution=_dict_value(payload.get("coverage_resolution")),
            flags=_flag_list(payload.get("flags")),
            diagnostics={
                "stages": [dict(stage) for stage in stages if isinstance(stage, Mapping)],
                "last_error": dict(last_error),
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "media": self.media,
            "nfo": self.nfo,
            "ua": self.ua,
            "arr": self.arr,
            "srrdb": self.srrdb,
            "release_group_policy": self.release_group_policy,
            "coverage_resolution": self.coverage_resolution,
            "flags": self.flags,
            "diagnostics": self.diagnostics,
        }


def empty_check_results() -> Dict[str, Any]:
    return CheckResults().to_dict()


def merge_check_results(existing: Any, **updates: Any) -> Dict[str, Any]:
    payload = existing if isinstance(existing, Mapping) else {}
    result = CheckResults.from_any(payload).to_dict()
    for key, value in payload.items():
        if key not in result:
            result[str(key)] = value
    for key, value in updates.items():
        result[key] = value
    if "diagnostics" not in result or not isinstance(result["diagnostics"], Mapping):
        result["diagnostics"] = {"stages": [], "last_error": {}}
    return result


def add_stage_diagnostic(
    existing: Any,
    *,
    stage: str,
    status: str,
    reason: str = "",
    started_at: Optional[float] = None,
    error: Optional[BaseException] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    result = merge_check_results(existing)
    diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), Mapping) else {}
    stages = list(diagnostics.get("stages") if isinstance(diagnostics.get("stages"), list) else [])
    entry: Dict[str, Any] = {
        "stage": stage,
        "status": status,
        "at": int(time.time()),
    }
    if reason:
        entry["reason"] = reason
    if started_at is not None:
        entry["duration_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
    if extra:
        entry.update(dict(extra))
    if error is not None:
        entry["error_type"] = type(error).__name__
        entry["error"] = str(error)[:240]

    stages.append(entry)
    diagnostics = {"stages": stages, "last_error": diagnostics.get("last_error") if isinstance(diagnostics.get("last_error"), Mapping) else {}}
    if error is not None or status == "error":
        diagnostics["last_error"] = {
            "stage": stage,
            "status": status,
            "reason": reason,
            "error_type": entry.get("error_type", ""),
            "error": entry.get("error", ""),
            "at": entry["at"],
        }
    result["diagnostics"] = diagnostics
    return result


def _dict_value(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _flag_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(flag) for flag in value if isinstance(flag, Mapping)]


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
