from app.rules import evaluate_decision, rule_catalogue, ruleset_changelog


def test_rule_catalogue_has_unique_valid_entries():
    rows = rule_catalogue()
    ids = [row["id"] for row in rows]

    assert len(ids) == len(set(ids))
    assert all(row["severity"] in {"pass", "info", "warning", "error"} for row in rows)
    assert all(row["effect"] in {"none", "candidate", "review", "block", "skip", "retry", "error"} for row in rows)
    assert ruleset_changelog()[0]["version"] == 1


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


def test_evaluator_reviews_source_missing_candidate():
    decision = evaluate_decision(
        current_status="candidate",
        current_verdict="candidate",
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        check_results={
            "ua": {"status": "candidate"},
            "flags": [{"key": "source_missing", "label": "Source Missing", "severity": "warning", "detail": "Missing provider"}],
        },
    )

    assert decision.status == "manual_review"
    assert decision.effect == "review"
    assert decision.winning_rule_id == "review.source_missing"


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
