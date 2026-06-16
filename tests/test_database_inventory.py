import json
import time

from app.database import Database


def _insert(db, torrent_hash, name, status="baseline", category="tv", tags="", path_prefix="/media/torrents/tv"):
    db.insert_discovered(
        1,
        {
            "hash": torrent_hash,
            "name": name,
            "category": category,
            "tags": tags,
            "content_path": f"{path_prefix}/{name}",
            "progress": 1,
        },
        status=status,
        baseline=status in {"baseline", "inventory"},
    )


def test_sql_baseline_filters_use_stored_inventory_columns(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "source", "Example.Show.S01E01.1080p.WEB-DL-GRP")
    _insert(
        db,
        "dp-cross",
        "Example.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="tv.cross",
        tags="cross-seed",
        path_prefix="/media/torrents/cross-seeds/DarkPeers",
    )
    _insert(db, "other", "Other.Show.S01E01.1080p.WEB-DL-GRP")

    hidden_by_dp = db.list_items_filtered(["baseline"], missing=["DP"])
    visible_missing_ihd = db.list_items_filtered(["baseline"], media="episode", missing=["IHD"])
    hidden_by_any_primary = db.list_items_filtered(["baseline"], hide_any_primary=True)

    assert [row["hash"] for row in hidden_by_dp] == ["other"]
    assert {row["hash"] for row in visible_missing_ihd} == {"source", "other"}
    assert [row["hash"] for row in hidden_by_any_primary] == ["other"]


def test_coverage_lookup_is_limited_to_requested_group_keys(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "source", "Example.Show.S01E01.1080p.WEB-DL-GRP")
    _insert(
        db,
        "dp-cross",
        "Example.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="tv.cross",
        tags="cross-seed",
        path_prefix="/media/torrents/cross-seeds/DarkPeers",
    )
    _insert(
        db,
        "ihd-cross",
        "Other.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="tv.cross",
        tags="cross-seed",
        path_prefix="/media/torrents/cross-seeds/IHD",
    )
    source = db.list_items(["baseline"], limit=1)[0]

    coverage = db.coverage_for_group_keys([source["inventory_group_key"]])

    assert list(coverage.keys()) == [source["inventory_group_key"]]
    assert [tracker["key"] for tracker in coverage[source["inventory_group_key"]]] == ["DP"]


def test_prune_missing_inventory_removes_deleted_coverage_rows(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "source", "Example.Show.S01E01.1080p.WEB-DL-GRP")
    _insert(
        db,
        "dp-cross",
        "Example.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="tv.cross",
        tags="cross-seed",
        path_prefix="/media/torrents/cross-seeds/DarkPeers",
    )
    _insert(db, "candidate", "Candidate.Show.S01E01.1080p.WEB-DL-GRP", status="candidate")
    source = db.list_items(["baseline"], limit=1)[0]

    removed = db.prune_missing_inventory(1, ["source", "candidate"])
    coverage = db.coverage_for_group_keys([source["inventory_group_key"]])
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}

    assert removed == 1
    assert coverage[source["inventory_group_key"]] == []
    assert "dp-cross" not in rows
    assert rows["candidate"]["status"] == "candidate"


def test_requeue_covered_with_missing_coverage(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "covered", "Example.Show.S01E01.1080p.WEB-DL-GRP", status="covered")
    item_id = int(db.list_items(["covered"], limit=1)[0]["id"])
    db.update_status(
        item_id,
        "covered",
        "covered",
        "Covered in QUI: DP",
        check_results={"coverage_resolution": {"status": "covered", "resolved_trackers": ["DP"]}},
    )

    result = db.requeue_covered_with_missing_coverage()
    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])

    assert result == {"items": 1, "trackers": 1}
    assert row["status"] == "queued"
    assert row["verdict"] == ""
    assert "DP" in row["reason"]
    assert checks["coverage_resolution"]["status"] == "lost"


def test_bulk_requeue_baseline_filtered_only_updates_found_set(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "source", "Example.Show.S01E01.1080p.WEB-DL-GRP")
    _insert(
        db,
        "dp-cross",
        "Example.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="tv.cross",
        tags="cross-seed",
        path_prefix="/media/torrents/cross-seeds/DarkPeers",
    )
    _insert(db, "other", "Other.Show.S01E01.1080p.WEB-DL-GRP")
    _insert(db, "candidate", "Candidate.Show.S01E01.1080p.WEB-DL-GRP", status="candidate")
    db.ignore(int(db.list_items(["candidate"], limit=1)[0]["id"]))

    queued = db.bulk_requeue_baseline_filtered(media="episode", missing=["DP"])
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}

    assert queued == 1
    assert rows["other"]["status"] == "queued"
    assert rows["other"]["reason"] == "Bulk recheck requested from baseline filtered set"
    assert rows["source"]["status"] == "baseline"
    assert rows["dp-cross"]["status"] == "inventory"
    assert rows["candidate"]["status"] == "ignored"


def test_bulk_requeue_filtered_updates_selected_final_status_only(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "candidate", "Example.Show.S01E01.1080p.WEB-DL-GRP", status="candidate")
    _insert(db, "blocked", "Example.Show.S01E02.1080p.WEB-DL-GRP", status="blocked")
    _insert(db, "manual", "Example.Show.S01E03.1080p.WEB-DL-GRP", status="manual_review")

    queued = db.bulk_requeue_filtered(["candidate"], media="episode", reason="Bulk recheck requested from candidate filtered set")
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}

    assert queued == 1
    assert rows["candidate"]["status"] == "queued"
    assert rows["candidate"]["reason"] == "Bulk recheck requested from candidate filtered set"
    assert rows["blocked"]["status"] == "blocked"
    assert rows["manual"]["status"] == "manual_review"


def test_resolve_covered_candidates_uses_arr_candidate_trackers(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "candidate", "Example.Show.S01E01.1080p.WEB-DL-GRP", status="candidate")
    candidate_id = int(db.list_items(["candidate"], limit=1)[0]["id"])
    db.update_status(
        candidate_id,
        "candidate",
        "candidate",
        "Valid upload candidate on: IHD, ULCX",
        tracker_results={"passed": ["IHD", "ULCX"], "dupe": [], "skipped": [], "error": []},
        arr_results={
            "status": "candidate",
            "reason": "Valid upload candidate on: IHD, ULCX",
            "decisions": [
                {"tracker": "IHD", "status": "candidate", "reason": "ok"},
                {"tracker": "ULCX", "status": "candidate", "reason": "ok"},
            ],
        },
    )
    _insert(
        db,
        "ihd-upload",
        "Example.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="uploads",
        tags="upload",
        path_prefix="/media/torrents/uploads/IHD",
    )

    partial = db.resolve_covered_candidates()
    assert partial == {"items": 0, "trackers": 0}
    assert db.get_item(candidate_id)["status"] == "candidate"

    _insert(
        db,
        "ulcx-cross",
        "Example.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="tv.cross",
        tags="cross-seed",
        path_prefix="/media/torrents/cross-seeds/ULCX",
    )
    resolved = db.resolve_covered_candidates()
    row = db.get_item(candidate_id)

    assert resolved == {"items": 1, "trackers": 2}
    assert row["status"] == "covered"
    assert row["verdict"] == "covered"
    assert row["reason"] == "Covered in QUI: IHD, ULCX"
    assert db.whacked_stats()["holes_filled"] == 2


def test_resolve_covered_candidates_falls_back_to_passed_trackers(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "candidate", "Fallback.Show.S01E01.1080p.WEB-DL-GRP", status="candidate")
    candidate_id = int(db.list_items(["candidate"], limit=1)[0]["id"])
    db.update_status(
        candidate_id,
        "candidate",
        "candidate",
        "Valid upload candidate on: IHD",
        tracker_results={"passed": ["IHD"], "dupe": [], "skipped": [], "error": []},
        arr_results={},
    )
    _insert(
        db,
        "dp-cross",
        "Fallback.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="tv.cross",
        tags="cross-seed",
        path_prefix="/media/torrents/cross-seeds/DarkPeers",
    )

    unrelated = db.resolve_covered_candidates()
    assert unrelated == {"items": 0, "trackers": 0}

    _insert(
        db,
        "ihd-upload",
        "Fallback.Show.S01E01.1080p.WEB-DL-GRP",
        status="inventory",
        category="uploads",
        tags="upload",
        path_prefix="/media/torrents/uploads/IHD",
    )
    resolved = db.resolve_covered_candidates()
    row = db.get_item(candidate_id)

    assert resolved == {"items": 1, "trackers": 1}
    assert row["status"] == "covered"
    assert '"covered": ["IHD"]' in row["tracker_results"]


def test_reapply_release_group_policy_updates_candidate_without_recheck(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "candidate", "Policy.Show.S01E01.1080p.WEB-DL-GRP", status="candidate")
    candidate_id = int(db.list_items(["candidate"], limit=1)[0]["id"])
    db.update_status(
        candidate_id,
        "candidate",
        "candidate",
        "Valid upload candidate on: DP, IHD",
        tracker_results={"passed": ["DP", "IHD"], "covered": [], "dupe": [], "skipped": [], "error": []},
        arr_results={
            "status": "candidate",
            "reason": "Valid upload candidate on: DP, IHD",
            "decisions": [
                {"tracker": "DP", "status": "candidate", "reason": "ok"},
                {"tracker": "IHD", "status": "candidate", "reason": "ok"},
            ],
        },
    )

    result = db.reapply_release_group_policy(
        {
            "DP": {"banned_release_groups": ["GRP"], "ranked_release_groups": []},
            "IHD": {"banned_release_groups": [], "ranked_release_groups": []},
        }
    )
    row = db.get_item(candidate_id)
    tracker_results = json.loads(row["tracker_results"])
    arr_results = json.loads(row["arr_results"])
    check_results = json.loads(row["check_results"])

    assert result == {"items": 1, "blocked_items": 0, "blocked_trackers": 1}
    assert row["status"] == "candidate"
    assert row["reason"] == "Valid upload candidate on: IHD"
    assert tracker_results["passed"] == ["IHD"]
    assert arr_results["decisions"][0]["status"] == "blocked"
    assert arr_results["decisions"][0]["banned_match"] == "GRP"
    assert check_results["release_group_policy"]["blocked_trackers"] == ["DP"]


def test_reapply_release_group_policy_blocks_candidate_when_all_trackers_banned(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    _insert(db, "candidate", "Blocked.Show.S01E01.1080p.WEB-DL-GRP", status="candidate")
    candidate_id = int(db.list_items(["candidate"], limit=1)[0]["id"])
    db.update_status(
        candidate_id,
        "candidate",
        "candidate",
        "Valid upload candidate on: IHD",
        tracker_results={"passed": ["IHD"], "covered": [], "dupe": [], "skipped": [], "error": []},
        arr_results={"decisions": [{"tracker": "IHD", "status": "candidate", "reason": "ok"}]},
    )

    result = db.reapply_release_group_policy(
        {"IHD": {"banned_release_groups": ["GRP"], "ranked_release_groups": []}}
    )
    row = db.get_item(candidate_id)

    assert result == {"items": 1, "blocked_items": 1, "blocked_trackers": 1}
    assert row["status"] == "blocked"
    assert row["verdict"] == "banned_release_group"
    assert row["reason"] == "GRP is banned on every otherwise valid tracker."


def test_active_filter_only_includes_due_retries(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    now = int(time.time())
    _insert(db, "queued", "Queued.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "checking", "Checking.Show.S01E01.1080p.WEB-DL-GRP", status="checking")
    _insert(db, "due-retry", "Due.Retry.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "future-retry", "Future.Retry.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "terminal-error", "Terminal.Error.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}
    db.update_status(int(rows["due-retry"]["id"]), "retry", "ua_error", "due", next_check_at=now - 1)
    db.update_status(int(rows["future-retry"]["id"]), "retry", "ua_error", "future", next_check_at=now + 3600)
    db.update_status(int(rows["terminal-error"]["id"]), "error", "path_mapping", "terminal")

    active = db.list_items_filtered(["queued", "deferred", "checking", "retry"], due_errors_only=True)
    all_retries = db.list_items_filtered(["retry"])
    all_errors = db.list_items_filtered(["error"])
    active_retry_count = db.count_items_filtered(["queued", "deferred", "checking", "retry"], due_errors_only=True)

    assert {row["hash"] for row in active} == {"queued", "checking", "due-retry"}
    assert {row["hash"] for row in all_retries} == {"due-retry", "future-retry"}
    assert {row["hash"] for row in all_errors} == {"terminal-error"}
    assert active_retry_count == 3


def test_queue_counts_split_due_and_waiting_retries(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    now = int(time.time())
    _insert(db, "queued", "Queued.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "deferred", "Deferred.Show.S01E01.1080p.WEB-DL-GRP", status="deferred")
    _insert(db, "checking", "Checking.Show.S01E01.1080p.WEB-DL-GRP", status="checking")
    _insert(db, "due-retry", "Due.Retry.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "future-retry", "Future.Retry.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}
    db.update_status(int(rows["due-retry"]["id"]), "retry", "ua_error", "due", next_check_at=now - 1)
    db.update_status(int(rows["future-retry"]["id"]), "retry", "ua_error", "future", next_check_at=now + 3600)

    counts = db.queue_counts()

    assert counts["queued"] == 1
    assert counts["deferred"] == 1
    assert counts["checking"] == 1
    assert counts["due_retries"] == 1
    assert counts["waiting_retries"] == 1
    assert counts["due_errors"] == 1
    assert counts["waiting_errors"] == 1
    assert counts["active"] == 4


def test_recover_stale_checking_moves_rows_to_retry(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    next_check_at = int(time.time()) + 900
    _insert(db, "checking", "Checking.Show.S01E01.1080p.WEB-DL-GRP", status="checking")
    _insert(db, "queued", "Queued.Show.S01E01.1080p.WEB-DL-GRP", status="queued")

    recovered = db.recover_stale_checking(next_check_at)
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}

    assert recovered == 1
    assert rows["checking"]["status"] == "retry"
    assert rows["checking"]["verdict"] == "interrupted_check"
    assert rows["checking"]["reason"] == "Whackamole restarted while this check was running. It will retry after backoff."
    assert rows["checking"]["next_check_at"] == next_check_at
    assert rows["queued"]["status"] == "queued"
