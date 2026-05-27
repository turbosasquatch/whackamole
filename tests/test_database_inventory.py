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
