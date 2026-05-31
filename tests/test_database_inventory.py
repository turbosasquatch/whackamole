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


def test_active_filter_only_includes_due_errors(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    now = int(time.time())
    _insert(db, "queued", "Queued.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "checking", "Checking.Show.S01E01.1080p.WEB-DL-GRP", status="checking")
    _insert(db, "due-error", "Due.Error.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "future-error", "Future.Error.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}
    db.update_status(int(rows["due-error"]["id"]), "error", "ua_error", "due", next_check_at=now - 1)
    db.update_status(int(rows["future-error"]["id"]), "error", "ua_error", "future", next_check_at=now + 3600)

    active = db.list_items_filtered(["queued", "deferred", "checking", "error"], due_errors_only=True)
    all_errors = db.list_items_filtered(["error"])
    active_error_count = db.count_items_filtered(["queued", "deferred", "checking", "error"], due_errors_only=True)

    assert {row["hash"] for row in active} == {"queued", "checking", "due-error"}
    assert {row["hash"] for row in all_errors} == {"due-error", "future-error"}
    assert active_error_count == 3


def test_queue_counts_split_due_and_waiting_errors(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    now = int(time.time())
    _insert(db, "queued", "Queued.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "deferred", "Deferred.Show.S01E01.1080p.WEB-DL-GRP", status="deferred")
    _insert(db, "checking", "Checking.Show.S01E01.1080p.WEB-DL-GRP", status="checking")
    _insert(db, "due-error", "Due.Error.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    _insert(db, "future-error", "Future.Error.Show.S01E01.1080p.WEB-DL-GRP", status="queued")
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}
    db.update_status(int(rows["due-error"]["id"]), "error", "ua_error", "due", next_check_at=now - 1)
    db.update_status(int(rows["future-error"]["id"]), "error", "ua_error", "future", next_check_at=now + 3600)

    counts = db.queue_counts()

    assert counts["queued"] == 1
    assert counts["deferred"] == 1
    assert counts["checking"] == 1
    assert counts["due_errors"] == 1
    assert counts["waiting_errors"] == 1
    assert counts["active"] == 4


def test_recover_stale_checking_moves_rows_to_retryable_error(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    next_check_at = int(time.time()) + 900
    _insert(db, "checking", "Checking.Show.S01E01.1080p.WEB-DL-GRP", status="checking")
    _insert(db, "queued", "Queued.Show.S01E01.1080p.WEB-DL-GRP", status="queued")

    recovered = db.recover_stale_checking(next_check_at)
    rows = {row["hash"]: row for row in db.list_items([], limit=20)}

    assert recovered == 1
    assert rows["checking"]["status"] == "error"
    assert rows["checking"]["verdict"] == "interrupted_check"
    assert rows["checking"]["reason"] == "Whackamole restarted while this check was running. It will retry after backoff."
    assert rows["checking"]["next_check_at"] == next_check_at
    assert rows["queued"]["status"] == "queued"
