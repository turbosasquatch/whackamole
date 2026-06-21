import json

from app.database import Database
from app.rules import evaluate_decision, rule_catalogue, ruleset_changelog


def _insert(db, torrent_hash, name, status="queued"):
    db.insert_discovered(
        1,
        {
            "hash": torrent_hash,
            "name": name,
            "category": "tv",
            "tags": "",
            "content_path": f"/media/torrents/tv/{name}",
            "progress": 1,
        },
        status=status,
        baseline=False,
    )
    return int(db.list_items([], limit=1)[0]["id"])


def test_rule_catalogue_has_unique_valid_entries():
    rows = rule_catalogue()
    ids = [row["id"] for row in rows]

    assert len(ids) == len(set(ids))
    assert all(row["severity"] in {"pass", "info", "warning", "error"} for row in rows)
    assert all(row["effect"] in {"none", "candidate", "review", "block", "skip", "retry", "error"} for row in rows)
    assert ruleset_changelog()[0]["version"] == 3


def test_evaluator_keeps_valid_tracker_candidate():
    decision = evaluate_decision(
        current_status="candidate",
        current_verdict="candidate",
        current_reason="Valid upload candidate on: IHD",
        tracker_results={"passed": ["IHD"], "dupe": [], "skipped": [], "error": []},
        arr_results={"status": "candidate", "decisions": [{"tracker": "IHD", "status": "candidate"}]},
        check_results={"ua": {"status": "candidate"}},
    )

    assert decision.status == "candidate"
    assert decision.effect == "candidate"
    assert decision.winning_rule_id == "final.candidate"


def test_evaluator_skips_when_no_tracker_passed():
    decision = evaluate_decision(
        current_status="blocked",
        current_verdict="no_tracker_passed",
        current_reason="No tracker passed UA checks.",
        tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
        check_results={"ua": {"status": "blocked", "verdict": "no_tracker_passed"}},
    )

    assert decision.status == "skipped"
    assert decision.effect == "skip"
    assert decision.winning_rule_id == "ua.no_uploadable_trackers"


def test_evaluator_skips_duplicates_when_no_targets_remain():
    decision = evaluate_decision(
        current_status="blocked",
        current_verdict="dupe",
        current_reason="UA found potential dupes on: DP, ULCX",
        tracker_results={"passed": [], "dupe": ["DP", "ULCX"], "skipped": [], "error": []},
        check_results={"ua": {"status": "blocked", "verdict": "dupe"}},
    )

    assert decision.status == "skipped"
    assert decision.verdict == "dupe"
    assert decision.winning_rule_id == "ua.duplicates_no_targets"


def test_evaluator_skips_arr_equal_or_better_everywhere():
    decision = evaluate_decision(
        current_status="skipped",
        current_verdict="not_upgrade",
        current_reason="UA passed, but Arr found equal-or-better torrent results.",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        arr_results={
            "status": "skipped",
            "reason": "UA passed, but Arr found equal-or-better torrent results.",
            "decisions": [{"tracker": "DP", "status": "blocked", "reason": "Arr found an equal-or-better torrent result."}],
        },
        check_results={"ua": {"status": "candidate"}},
    )

    assert decision.status == "skipped"
    assert decision.winning_rule_id == "arr.equal_or_better_no_targets"


def test_evaluator_blocks_hard_media_policy():
    decision = evaluate_decision(
        current_status="candidate",
        current_verdict="candidate",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        check_results={
            "ua": {"status": "candidate"},
            "flags": [{"key": "bloated_audio", "label": "MediaInfo Error", "severity": "blocker", "detail": "Bloated audio"}],
        },
    )

    assert decision.status == "blocked"
    assert decision.effect == "block"
    assert decision.winning_rule_id == "media.hard_block"


def test_evaluator_keeps_source_missing_candidate_informational():
    decision = evaluate_decision(
        current_status="candidate",
        current_verdict="candidate",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        check_results={
            "ua": {"status": "candidate"},
            "flags": [{"key": "source_missing", "label": "Source Missing", "severity": "warning", "detail": "Missing provider"}],
        },
    )

    assert decision.status == "candidate"
    assert decision.effect == "candidate"
    assert decision.winning_rule_id == "final.candidate"


def test_evaluator_reviews_high_confidence_rename_check():
    decision = evaluate_decision(
        current_status="manual_review",
        current_verdict="renamed_release_warning",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        check_results={
            "ua": {"status": "candidate"},
            "rename_detection": {
                "status": "manual_review",
                "confidence": "high",
                "reason": "File contains an empty title token.",
                "evidence": [{"kind": "empty_title_token"}],
            },
        },
    )

    assert decision.status == "manual_review"
    assert decision.verdict == "renamed_release_warning"
    assert decision.winning_rule_id == "review.rename_check"


def test_evaluator_ignores_legacy_rename_flag_without_structured_detection():
    decision = evaluate_decision(
        current_status="candidate",
        current_verdict="candidate",
        current_reason="Valid upload candidate on: DP",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        check_results={
            "ua": {"status": "candidate"},
            "flags": [
                {
                    "key": "renamed_release_warning",
                    "label": "Rename Check",
                    "severity": "warning",
                    "detail": "Legacy rename flag.",
                }
            ],
        },
    )

    assert decision.status == "candidate"
    assert decision.verdict == "candidate"
    assert decision.winning_rule_id == "final.candidate"


def test_evaluator_errors_no_video_files():
    decision = evaluate_decision(
        current_status="error",
        current_verdict="no_video_files",
        current_reason="UA could not find video files.",
        tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
        check_results={"ua": {"status": "error", "verdict": "no_video_files"}},
    )

    assert decision.status == "error"
    assert decision.effect == "error"
    assert decision.winning_rule_id == "system.no_video_files"


def test_evaluator_errors_unavailable_mediainfo_evidence():
    for key in ("mediainfo_unavailable", "mediainfo_missing"):
        decision = evaluate_decision(
            current_status="candidate",
            current_verdict="candidate",
            current_reason="Valid upload candidate on: DP",
            tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
            check_results={
                "ua": {"status": "candidate"},
                "flags": [
                    {
                        "key": key,
                        "label": "MediaInfo Error",
                        "severity": "blocker",
                        "detail": "Whackamole could not read QUI MediaInfo.",
                    }
                ],
            },
        )

        assert decision.status == "error"
        assert decision.verdict == key
        assert decision.effect == "error"
        assert decision.winning_rule_id == "system.mediainfo_unavailable"


def test_evaluator_reviews_pre_release_arr_failure():
    decision = evaluate_decision(
        current_status="manual_review",
        current_verdict="pre_release",
        current_reason="Arr comparison unavailable: Radarr movie has not released yet.",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        arr_results={"status": "manual_review", "verdict": "pre_release", "reason": "Radarr movie has not released yet."},
        check_results={"ua": {"status": "candidate"}, "arr": {"status": "manual_review", "verdict": "pre_release"}},
    )

    assert decision.status == "manual_review"
    assert decision.verdict == "pre_release"
    assert decision.winning_rule_id == "arr.pre_release"


def test_evaluator_reviews_generic_mediainfo_error_candidate():
    decision = evaluate_decision(
        current_status="candidate",
        current_verdict="candidate",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        check_results={
            "ua": {"status": "candidate"},
            "flags": [
                {
                    "key": "audio_object_missing",
                    "label": "MediaInfo Error",
                    "severity": "blocker",
                    "detail": "Dolby Atmos should include object/JOC metadata.",
                }
            ],
        },
    )

    assert decision.status == "manual_review"
    assert decision.effect == "review"
    assert decision.winning_rule_id == "review.evidence_warning"


def test_evaluator_retries_transient_ua_failure():
    decision = evaluate_decision(
        current_status="retry",
        current_verdict="ua_interrupted",
        current_reason="UA was interrupted.",
        tracker_results={"passed": [], "dupe": [], "skipped": [], "error": ["UA"]},
        check_results={"ua": {"status": "error", "verdict": "ua_interrupted"}},
    )

    assert decision.status == "retry"
    assert decision.retryable is True
    assert decision.winning_rule_id == "system.retry_transient"


def test_evaluator_errors_when_arr_media_does_not_match():
    decision = evaluate_decision(
        current_status="manual_review",
        current_verdict="manual_review",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        arr_results={"status": "manual_review", "reason": "No matching Radarr movie found"},
        check_results={"ua": {"status": "candidate"}},
    )

    assert decision.status == "error"
    assert decision.effect == "error"
    assert decision.winning_rule_id == "arr.no_matching_media"


def test_evaluator_marks_empty_legacy_rows_not_replayable():
    decision = evaluate_decision()

    assert decision.replayable is False
    assert decision.winning_rule_id == "system.not_replayable"


def test_stored_decision_replay_previews_then_applies_status_change(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert(db, "blocked", "Blocked.Show.S01E01.1080p.WEB-DL-GRP")
    db.update_status(
        item_id,
        "blocked",
        "no_tracker_passed",
        "No tracker passed UA checks.",
        tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
        check_results={"ua": {"status": "blocked", "verdict": "no_tracker_passed", "reason": "No tracker passed UA checks."}},
    )

    preview = db.reevaluate_stored_decisions(apply=False)
    row_after_preview = db.get_item(item_id)
    applied = db.reevaluate_stored_decisions(apply=True)
    row_after_apply = db.get_item(item_id)
    checks = json.loads(row_after_apply["check_results"])

    assert preview["checked"] == 1
    assert preview["changed"] == 1
    assert preview["movements"] == {"blocked -> skipped": 1}
    assert row_after_preview["status"] == "blocked"
    assert applied["changed"] == 1
    assert row_after_apply["status"] == "skipped"
    assert checks["decision"]["winning_rule_id"] == "ua.no_uploadable_trackers"
    assert checks["diagnostics"]["stages"][-1]["stage"] == "rules_replay"


def test_stored_decision_replay_protects_terminal_errors(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert(db, "error", "Error.Show.S01E01.1080p.WEB-DL-GRP")
    db.update_status(item_id, "error", "path_mapping", "Path mapping failed.")

    preview = db.reevaluate_stored_decisions(apply=False)

    assert preview["checked"] == 0
    assert preview["protected"] == 1
    assert db.get_item(item_id)["status"] == "error"
