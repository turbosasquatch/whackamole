from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


RULESET_VERSION = 4
TRACKER_BUCKETS = ("passed", "covered", "dupe", "skipped", "error")
TERMINAL_REPLAY_STATUSES = {"candidate", "manual_review", "blocked", "skipped"}
PROTECTED_REPLAY_STATUSES = {"queued", "deferred", "checking", "retry", "error", "covered", "rejected", "baseline", "inventory", "ignored"}

VALID_SEVERITIES = {"pass", "info", "warning", "error"}
VALID_EFFECTS = {"none", "candidate", "review", "block", "skip", "retry", "error"}


RULESET_CHANGELOG: List[Dict[str, Any]] = [
    {
        "version": RULESET_VERSION,
        "changed_rules": ["policy.moderation_queue_no_targets"],
        "summary": "Skip episode uploads when moderation queues remove every otherwise valid tracker.",
        "replay_recommended": True,
    },
    {
        "version": 3,
        "changed_rules": [
            "system.mediainfo_unavailable",
            "system.no_video_files",
            "review.evidence_warning",
        ],
        "summary": "Route missing QUI MediaInfo evidence to terminal errors instead of manual review.",
        "replay_recommended": True,
    },
    {
        "version": 2,
        "changed_rules": [
            "review.rename_check",
            "review.folder_name_warning",
        ],
        "summary": "Replace folder-only review with structured Rename Check evidence and protect rejected moderation feedback from replay.",
        "replay_recommended": True,
    },
    {
        "version": 1,
        "changed_rules": [
            "ua.no_uploadable_trackers",
            "ua.duplicates_no_targets",
            "arr.equal_or_better_no_targets",
            "policy.all_trackers_banned",
            "media.hard_block",
            "review.source_missing",
            "arr.pre_release",
            "system.no_video_files",
            "review.folder_name_warning",
            "review.srrdb_mismatch",
            "system.retry_transient",
            "system.terminal_error",
            "final.candidate",
        ],
        "summary": "Split evidence from final decisions and introduce skipped/retry terminal lanes.",
        "replay_recommended": True,
    }
]


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    label: str
    kind: str
    stage: str
    severity: str
    effect: str
    evidence: str
    configurable: str = "No"
    notes: str = ""
    order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuleResult:
    rule_id: str
    status: str
    severity: str
    effect: str
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FinalDecision:
    status: str
    verdict: str
    reason: str
    effect: str
    severity: str
    winning_rule_id: str
    ruleset_version: int = RULESET_VERSION
    replayable: bool = True
    retryable: bool = False
    rules: List[RuleResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "verdict": self.verdict,
            "reason": self.reason,
            "effect": self.effect,
            "severity": self.severity,
            "winning_rule_id": self.winning_rule_id,
            "ruleset_version": self.ruleset_version,
            "replayable": self.replayable,
            "retryable": self.retryable,
        }

    def rules_payload(self) -> List[Dict[str, Any]]:
        return [rule.to_dict() for rule in self.rules]


def rule_catalogue() -> List[Dict[str, Any]]:
    return [rule.to_dict() for rule in RULE_DEFINITIONS]


def ruleset_changelog() -> List[Dict[str, Any]]:
    return [dict(item) for item in RULESET_CHANGELOG]


def apply_decision_payload(check_results: Any, decision: FinalDecision) -> Dict[str, Any]:
    payload = dict(check_results) if isinstance(check_results, Mapping) else {}
    payload.setdefault("version", 1)
    payload["decision"] = decision.to_dict()
    payload["rules"] = decision.rules_payload()
    payload["ruleset_version"] = RULESET_VERSION
    return payload


def add_replay_audit(
    check_results: Any,
    *,
    previous: Mapping[str, Any],
    decision: FinalDecision,
    applied_at: Optional[int] = None,
) -> Dict[str, Any]:
    payload = apply_decision_payload(check_results, decision)
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
    stages = list(diagnostics.get("stages") if isinstance(diagnostics.get("stages"), list) else [])
    stages.append(
        {
            "stage": "rules_replay",
            "status": decision.status,
            "reason": decision.reason,
            "at": int(applied_at or time.time()),
            "ruleset_version": RULESET_VERSION,
            "previous": {
                "status": str(previous.get("status") or ""),
                "verdict": str(previous.get("verdict") or ""),
                "reason": str(previous.get("reason") or ""),
            },
            "decision": decision.to_dict(),
        }
    )
    payload["diagnostics"] = {
        "stages": stages,
        "last_error": diagnostics.get("last_error") if isinstance(diagnostics.get("last_error"), Mapping) else {},
    }
    return payload


def evaluate_decision(
    *,
    item_name: str = "",
    current_status: str = "",
    current_verdict: str = "",
    current_reason: str = "",
    tracker_results: Any = None,
    arr_results: Any = None,
    check_results: Any = None,
) -> FinalDecision:
    checks = _dict_value(check_results)
    groups = _tracker_result_groups(tracker_results)
    ua = _dict_value(checks.get("ua"))
    arr = _dict_value(arr_results) or _dict_value(checks.get("arr"))
    policy = _dict_value(checks.get("release_group_policy"))
    srrdb = _dict_value(checks.get("srrdb"))
    rename_detection = _dict_value(checks.get("rename_detection"))
    flags = _flag_list(checks.get("flags"))
    status = str(current_status or ua.get("status") or arr.get("status") or "")
    verdict = str(current_verdict or ua.get("verdict") or "")
    reason = str(current_reason or ua.get("reason") or arr.get("reason") or "")
    rules: List[RuleResult] = []

    if not _has_structured_evidence(checks, groups, arr, status, verdict, reason):
        return _decision(
            "system.not_replayable",
            "none",
            "info",
            "none",
            "not_replayable",
            "Not enough structured evidence is available to replay this decision.",
            rules,
            replayable=False,
        )

    transient_verdicts = {"http_error", "ua_error", "ua_interrupted", "interrupted_check"}
    ua_verdict = str(ua.get("verdict") or "")
    if verdict in transient_verdicts or ua_verdict in transient_verdicts:
        return _decision(
            "system.retry_transient",
            "retry",
            "warning",
            "retry",
            verdict or ua_verdict or "retry",
            reason or "Transient check failure; retry scheduled.",
            rules,
            retryable=True,
        )

    if verdict == "path_mapping":
        return _decision("system.terminal_error", "error", "error", "error", verdict, reason or "Path mapping failed.", rules)

    arr_reason = str(arr.get("reason") or reason)
    if _arr_identity_error(arr):
        return _decision("arr.no_matching_media", "error", "error", "error", "arr_no_matching_media", arr_reason, rules)

    mediainfo_error_verdicts = {"mediainfo_unavailable", "mediainfo_missing"}
    mediainfo_unavailable_flag = _first_flag(flags, mediainfo_error_verdicts)
    if verdict in mediainfo_error_verdicts or ua_verdict in mediainfo_error_verdicts or mediainfo_unavailable_flag:
        if verdict in mediainfo_error_verdicts:
            error_verdict = verdict
        elif ua_verdict in mediainfo_error_verdicts:
            error_verdict = ua_verdict
        else:
            error_verdict = str(mediainfo_unavailable_flag.get("key") or "mediainfo_unavailable")
        return _decision(
            "system.mediainfo_unavailable",
            "error",
            "error",
            "error",
            error_verdict,
            reason or _flag_reason(mediainfo_unavailable_flag) or "QUI MediaInfo is unavailable.",
            rules,
            {"flag": mediainfo_unavailable_flag} if mediainfo_unavailable_flag else {},
        )

    no_video_flag = _first_flag(flags, {"no_video_files"})
    if verdict == "no_video_files" or ua_verdict == "no_video_files" or no_video_flag:
        return _decision(
            "system.no_video_files",
            "error",
            "error",
            "error",
            "no_video_files",
            reason or _flag_reason(no_video_flag) or "No video files were found.",
            rules,
            {"flag": no_video_flag} if no_video_flag else {},
        )

    if verdict == "error" or ua_verdict == "error":
        return _decision("system.terminal_error", "error", "error", "error", "ua_error", reason or "UA returned an error.", rules)

    hard_flag = _first_flag(flags, {"bloated_audio", "primary_language"})
    if hard_flag:
        return _decision(
            "media.hard_block",
            "blocked",
            "error",
            "block",
            str(hard_flag.get("key") or "media_hard_block"),
            _flag_reason(hard_flag),
            rules,
            {"flag": hard_flag},
        )

    if _policy_all_trackers_banned(policy, flags, verdict):
        blocked = _tracker_list(policy.get("blocked_trackers"))
        return _decision(
            "policy.all_trackers_banned",
            "blocked",
            "error",
            "block",
            "banned_release_group",
            reason or f"Release group is banned on every otherwise valid tracker: {', '.join(blocked)}",
            rules,
            {"blocked_trackers": blocked},
        )

    if status == "manual_review" and verdict == "pre_release":
        return _decision(
            "arr.pre_release",
            "manual_review",
            "warning",
            "review",
            "pre_release",
            reason or arr_reason or "Arr says this media has not released yet.",
            rules,
            {"arr": arr},
        )

    valid_trackers = _valid_trackers(groups, arr, policy, status)
    if not valid_trackers:
        skipped = _skipped_reason(groups, arr, policy, status, verdict, reason)
        if skipped:
            rule_id, skip_verdict, skip_reason, evidence = skipped
            return _decision(rule_id, "skipped", "info", "skip", skip_verdict, skip_reason, rules, evidence)

    review_flag = _first_review_flag(
        flags,
        {
            "media_error",
            "srrdb_filename_mismatch",
            "missing_release_group",
        },
    )
    if str(srrdb.get("status") or "") == "mismatch":
        return _decision(
            "review.srrdb_mismatch",
            "manual_review",
            "warning",
            "review",
            "srrdb_filename_mismatch",
            str(srrdb.get("reason") or "srrDB archived filename does not match local evidence."),
            rules,
            {"srrdb": srrdb},
        )
    if review_flag:
        rule_id = {
            "missing_release_group": "review.missing_release_group",
        }.get(str(review_flag.get("key") or ""), "review.evidence_warning")
        return _decision(
            rule_id,
            "manual_review",
            "warning",
            "review",
            str(review_flag.get("key") or "manual_review"),
            _flag_reason(review_flag),
            rules,
            {"flag": review_flag},
        )
    if str(rename_detection.get("status") or "") == "manual_review":
        return _decision(
            "review.rename_check",
            "manual_review",
            "warning",
            "review",
            "renamed_release_warning",
            str(rename_detection.get("reason") or "Rename Check found high-confidence renamed release evidence."),
            rules,
            {"rename_detection": rename_detection},
        )

    if valid_trackers:
        return _decision(
            "final.candidate",
            "candidate",
            "pass",
            "candidate",
            "candidate",
            reason or f"Valid upload candidate on: {', '.join(valid_trackers)}",
            rules,
            {"valid_trackers": valid_trackers},
        )

    return _decision(
        "ua.no_uploadable_trackers",
        "skipped",
        "info",
        "skip",
        "no_uploadable_trackers",
        reason or "No uploadable trackers remain.",
        rules,
    )


def _decision(
    rule_id: str,
    status: str,
    severity: str,
    effect: str,
    verdict: str,
    reason: str,
    rules: List[RuleResult],
    evidence: Optional[Mapping[str, Any]] = None,
    *,
    replayable: bool = True,
    retryable: bool = False,
) -> FinalDecision:
    rule = RuleResult(
        rule_id=rule_id,
        status="matched",
        severity=severity,
        effect=effect,
        reason=reason,
        evidence=dict(evidence or {}),
    )
    return FinalDecision(
        status=status,
        verdict=verdict,
        reason=reason,
        effect=effect,
        severity=severity,
        winning_rule_id=rule_id,
        replayable=replayable,
        retryable=retryable,
        rules=[*rules, rule],
    )


def _has_structured_evidence(
    checks: Mapping[str, Any],
    groups: Mapping[str, Sequence[str]],
    arr: Mapping[str, Any],
    status: str,
    verdict: str,
    reason: str,
) -> bool:
    if checks:
        return True
    if any(groups.get(bucket) for bucket in TRACKER_BUCKETS):
        return True
    if arr:
        return True
    return bool(status or verdict or reason)


def _tracker_result_groups(value: Any) -> Dict[str, List[str]]:
    if isinstance(value, Mapping):
        raw_groups = value.get("groups") if isinstance(value.get("groups"), Mapping) else value
        return {
            bucket: [str(item).upper() for item in raw_groups.get(bucket, []) if str(item).strip()]
            if isinstance(raw_groups.get(bucket), list)
            else []
            for bucket in TRACKER_BUCKETS
        }
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        parsed = []
    if isinstance(parsed, Mapping):
        return _tracker_result_groups(parsed)
    if isinstance(parsed, list):
        return {"passed": [str(item).upper() for item in parsed if str(item).strip()], "covered": [], "dupe": [], "skipped": [], "error": []}
    return {bucket: [] for bucket in TRACKER_BUCKETS}


def _valid_trackers(
    groups: Mapping[str, Sequence[str]],
    arr: Mapping[str, Any],
    policy: Mapping[str, Any],
    status: str,
) -> List[str]:
    policy_candidates = _tracker_list(policy.get("candidate_trackers"))
    if "candidate_trackers" in policy and (policy_candidates or status != "manual_review"):
        return policy_candidates

    decisions = arr.get("decisions") if isinstance(arr.get("decisions"), list) else []
    if decisions:
        return _dedupe(
            str(decision.get("tracker") or "").upper()
            for decision in decisions
            if isinstance(decision, Mapping)
            and str(decision.get("status") or "").lower() == "candidate"
            and str(decision.get("tracker") or "").strip()
        )

    if status == "candidate":
        return _dedupe(str(tracker).upper() for tracker in groups.get("passed", []) if str(tracker).strip())
    return []


def _skipped_reason(
    groups: Mapping[str, Sequence[str]],
    arr: Mapping[str, Any],
    policy: Mapping[str, Any],
    status: str,
    verdict: str,
    reason: str,
) -> Optional[tuple[str, str, str, Dict[str, Any]]]:
    moderation_queues = _tracker_list(policy.get("moderation_queue_trackers"))
    policy_candidates = _tracker_list(policy.get("candidate_trackers"))
    policy_blocked = _tracker_list(policy.get("blocked_trackers"))
    if moderation_queues and not policy_candidates and not policy_blocked:
        return (
            "policy.moderation_queue_no_targets",
            "moderation_queue_no_targets",
            reason or f"Episode uploads are skipped on every otherwise valid tracker: {', '.join(moderation_queues)}.",
            {"trackers": moderation_queues},
        )
    if groups.get("dupe") or verdict in {"dupe", "exact_match"}:
        trackers = _dedupe(groups.get("dupe", []))
        return (
            "ua.duplicates_no_targets",
            verdict if verdict in {"dupe", "exact_match"} else "dupe",
            reason or f"Duplicate or exact match exists on all uploadable trackers: {', '.join(trackers)}",
            {"trackers": trackers},
        )
    if groups.get("skipped") or verdict == "skipped":
        trackers = _dedupe(groups.get("skipped", []))
        return (
            "ua.tracker_conditions_no_targets",
            "tracker_conditions_skipped",
            reason or f"UA tracker conditions skipped all uploadable trackers: {', '.join(trackers)}",
            {"trackers": trackers},
        )
    if verdict == "no_tracker_passed" or status == "blocked" and "No tracker passed" in reason:
        return ("ua.no_uploadable_trackers", "no_uploadable_trackers", reason or "No tracker passed UA checks.", {})
    if _arr_equal_or_better_only(arr, status, reason, verdict):
        return (
            "arr.equal_or_better_no_targets",
            "not_upgrade",
            str(arr.get("reason") or reason or "Arr found equal-or-better results for every otherwise valid tracker."),
            {"decisions": arr.get("decisions") if isinstance(arr.get("decisions"), list) else []},
        )
    if policy and not _tracker_list(policy.get("candidate_trackers")):
        return (
            "tracker.no_remaining_valid_targets",
            "no_remaining_valid_targets",
            reason or "No valid tracker remains after policy filtering.",
            {"policy": policy},
        )
    return None


def _arr_equal_or_better_only(arr: Mapping[str, Any], status: str, reason: str, verdict: str) -> bool:
    decisions = arr.get("decisions") if isinstance(arr.get("decisions"), list) else []
    if not decisions:
        return status == "blocked" and (verdict == "not_upgrade" or "equal-or-better" in reason.lower())
    candidates = [
        decision for decision in decisions
        if isinstance(decision, Mapping) and str(decision.get("status") or "").lower() == "candidate"
    ]
    blocked = [
        decision for decision in decisions
        if isinstance(decision, Mapping) and str(decision.get("status") or "").lower() == "blocked"
    ]
    text = json.dumps(arr, sort_keys=True).lower()
    return bool(blocked) and not candidates and "equal-or-better" in text


def _policy_all_trackers_banned(policy: Mapping[str, Any], flags: Sequence[Mapping[str, Any]], verdict: str) -> bool:
    blocked = _tracker_list(policy.get("blocked_trackers"))
    candidates = _tracker_list(policy.get("candidate_trackers"))
    if not blocked or candidates:
        return False
    if verdict == "banned_release_group" or _first_flag(flags, {"banned_release_group"}):
        return True
    decisions = policy.get("decisions") if isinstance(policy.get("decisions"), list) else []
    return bool(decisions) and all("banned" in str(decision.get("reason") or "").lower() for decision in decisions if isinstance(decision, Mapping))


def _arr_identity_error(arr: Mapping[str, Any]) -> bool:
    if str(arr.get("status") or "").lower() != "manual_review":
        return False
    reason = str(arr.get("reason") or "").lower()
    return "no matching sonarr" in reason or "no matching radarr" in reason


def _first_flag(flags: Sequence[Mapping[str, Any]], keys: set[str]) -> Dict[str, Any]:
    for flag in flags:
        if not isinstance(flag, Mapping):
            continue
        if str(flag.get("key") or "") in keys:
            return dict(flag)
    return {}


def _first_review_flag(flags: Sequence[Mapping[str, Any]], keys: set[str]) -> Dict[str, Any]:
    for flag in flags:
        if not isinstance(flag, Mapping):
            continue
        key = str(flag.get("key") or "")
        label = str(flag.get("label") or "")
        severity = str(flag.get("severity") or "")
        if key in keys or (label == "MediaInfo Error" and severity == "blocker"):
            return dict(flag)
    return {}


def _flag_reason(flag: Mapping[str, Any]) -> str:
    return str(flag.get("detail") or flag.get("message") or flag.get("label") or "Review before upload.")


def _dict_value(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _flag_list(value: Any) -> List[Dict[str, Any]]:
    return [dict(flag) for flag in value if isinstance(flag, Mapping)] if isinstance(value, list) else []


def _tracker_list(value: Any) -> List[str]:
    return _dedupe(str(tracker).upper() for tracker in value if str(tracker).strip()) if isinstance(value, list) else []


def _dedupe(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(str(value).upper() for value in values if str(value).strip()))


RULE_DEFINITIONS: List[RuleDefinition] = [
    RuleDefinition("media.identity", "MediaInfo identity", "Information", "MediaInfo", "pass", "none", "check_results.media", notes="Confirms release traits from QUI/local MediaInfo.", order=10),
    RuleDefinition("media.hard_block", "Hard media policy", "Decision", "MediaInfo", "error", "block", "check_results.flags", notes="Blocks clear media-policy failures such as bloated audio or primary language.", order=20),
    RuleDefinition("review.source_missing", "WEB source missing", "Information", "Source Detection", "info", "none", "check_results.flags", notes="Records WEB releases without a provider in title, MediaInfo, or NFO.", order=30),
    RuleDefinition("arr.pre_release", "Pre-release media", "Decision", "Discovarr", "warning", "review", "check_results.arr", notes="Reviews Arr media that has not aired, released, or started its season.", order=35),
    RuleDefinition("system.mediainfo_unavailable", "MediaInfo unavailable", "Decision", "MediaInfo", "error", "error", "check_results.flags", notes="Errors when QUI cannot provide MediaInfo evidence for video files.", order=37),
    RuleDefinition("system.no_video_files", "No video files", "Decision", "MediaInfo", "error", "error", "check_results.flags", notes="Errors when QUI or UA cannot find any video files for the item.", order=38),
    RuleDefinition("system.terminal_error", "Terminal system error", "Decision", "System", "error", "error", "stage diagnostics", notes="Broken setup or impossible evidence that needs investigation.", order=40),
    RuleDefinition("system.retry_transient", "Transient retry", "Decision", "System", "warning", "retry", "UA/client errors", notes="Retries temporary UA/interruption/rate-limit style failures.", order=50),
    RuleDefinition("ua.no_uploadable_trackers", "No uploadable trackers", "Decision", "Upload Assistant", "info", "skip", "check_results.ua", notes="Skips clean no-op checks where no tracker accepted the release.", order=60),
    RuleDefinition("ua.duplicates_no_targets", "Duplicates on all targets", "Decision", "Upload Assistant", "info", "skip", "tracker_results.dupe", notes="Skips when duplicates or exact matches leave no upload target.", order=70),
    RuleDefinition("ua.tracker_conditions_no_targets", "Tracker conditions skipped all targets", "Decision", "Upload Assistant", "info", "skip", "tracker_results.skipped", notes="Skips when UA tracker conditions leave no upload target.", order=80),
    RuleDefinition("arr.no_matching_media", "No matching Arr media", "Decision", "Discovarr", "error", "error", "check_results.arr", notes="Errors when Sonarr/Radarr cannot link the release to configured media.", order=90),
    RuleDefinition("arr.equal_or_better_no_targets", "No upgrade needed", "Decision", "Discovarr", "info", "skip", "arr_results.decisions", notes="Skips when Arr found equal-or-better results for every target.", order=100),
    RuleDefinition("policy.all_trackers_banned", "Release group banned everywhere", "Decision", "Release Group", "error", "block", "release_group_policy", configurable="Settings", notes="Blocks when the group is banned on every otherwise valid tracker.", order=110),
    RuleDefinition("policy.moderation_queue_no_targets", "Moderation queues remove all targets", "Decision", "Tracker Policy", "info", "skip", "release_group_policy.moderation_queue_trackers", configurable="Settings", notes="Skips episode uploads when moderation queues remove every otherwise valid tracker.", order=120),
    RuleDefinition("tracker.no_remaining_valid_targets", "No remaining valid targets", "Decision", "Tracker Validation", "info", "skip", "release_group_policy", notes="Skips cleanly when filtering leaves no valid tracker.", order=125),
    RuleDefinition("review.missing_release_group", "Missing release group", "Decision", "Release Group", "warning", "review", "release_group_policy", notes="Reviews when policy needs a release group and none can be parsed.", order=130),
    RuleDefinition("review.srrdb_mismatch", "srrDB mismatch", "Decision", "srrDB", "warning", "review", "check_results.srrdb", notes="Reviews when archived filenames or sizes differ.", order=140),
    RuleDefinition("review.rename_check", "Rename Check", "Decision", "Rename Check", "warning", "review", "check_results.rename_detection", notes="Reviews high-confidence renamed folder, file, sibling, Arr, or srrDB evidence.", order=150),
    RuleDefinition("review.folder_name_warning", "Folder name normalization", "Information", "Rename Check", "info", "none", "check_results.rename_detection", notes="Legacy folder normalization is informational; rename routing uses structured Rename Check evidence.", order=155),
    RuleDefinition("review.evidence_warning", "Evidence warning", "Decision", "Rules", "warning", "review", "check_results.flags", notes="Reviews candidate-affecting warning evidence.", order=160),
    RuleDefinition("final.candidate", "Candidate", "Decision", "Final", "pass", "candidate", "valid tracker set", notes="Allows upload when at least one valid tracker remains.", order=900),
    RuleDefinition("system.not_replayable", "Not replayable", "Information", "Rules", "info", "none", "legacy row", notes="Used when stored evidence is insufficient for replay.", order=1000),
]
