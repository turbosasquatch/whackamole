from app.inventory import (
    build_inventory_meta,
    coverage_index,
    detect_media_type,
    filter_inventory_rows,
    missing_primary_trackers,
    release_group_key,
)


def test_release_group_key_matches_space_and_dot_variants():
    dotted = "Ruby.and.Jodi.A.Cult.of.Sin.and.Influence.S01.1080p.AMZN.WEB-DL.DDP2.0.H.264-Kitsune"
    spaced = "Ruby and Jodi A Cult of Sin and Influence S01 1080p AMZN WEB-DL DDP2 0 H 264-Kitsune"

    assert release_group_key(dotted) == release_group_key(spaced)


def test_inventory_meta_detects_primary_upload_tracker():
    meta = build_inventory_meta(
        {
            "name": "Movie.2024.1080p.WEB-DL-GRP.mkv",
            "category": "uploads",
            "tags": "upload",
            "save_path": "/media/torrents/uploads/IHD",
            "content_path": "/media/torrents/uploads/IHD/Movie.2024.1080p.WEB-DL-GRP.mkv",
        }
    )

    assert meta["is_upload"]
    assert meta["is_support"]
    assert meta["tracker"]["key"] == "IHD"
    assert meta["tracker"]["primary"] is True


def test_inventory_meta_detects_other_cross_seed_tracker():
    meta = build_inventory_meta(
        {
            "name": "Show.S01.1080p.WEB-DL-GRP",
            "category": "tv.cross",
            "tags": "cross-seed",
            "save_path": "/media/torrents/cross-seeds/IPTorrents",
            "content_path": "/media/torrents/cross-seeds/IPTorrents/Show.S01.1080p.WEB-DL-GRP",
        }
    )

    assert meta["is_cross_seed"]
    assert meta["is_support"]
    assert meta["tracker"]["key"] == "IPT"
    assert meta["tracker"]["primary"] is False


def test_media_type_filters_movies_tv_and_episodes():
    assert detect_media_type({"name": "Movie.2024.1080p.WEB-DL-GRP", "category": "movies"}) == "movie"
    assert detect_media_type({"name": "Show.S03.1080p.WEB-DL-GRP", "category": "tv"}) == "tv"
    assert detect_media_type({"name": "Show.S03E11.1080p.WEB-DL-GRP", "category": "tv"}) == "episode"


def test_coverage_index_and_missing_filters():
    source = {
        "id": 1,
        "name": "Show.S01.1080p.WEB-DL-GRP",
        "category": "tv",
        "tags": "",
        "content_path": "/media/torrents/tv/Show.S01.1080p.WEB-DL-GRP",
        "raw_torrent": "{}",
        "inventory_meta": {},
    }
    dp_cross = {
        "id": 2,
        "name": "Show.S01.1080p.WEB-DL-GRP",
        "category": "tv.cross",
        "tags": "cross-seed",
        "content_path": "/media/torrents/cross-seeds/DarkPeers/Show.S01.1080p.WEB-DL-GRP",
        "raw_torrent": "{}",
        "inventory_meta": {},
    }
    index = coverage_index([source, dp_cross])
    coverage = index[release_group_key(source["name"])]

    assert [item["key"] for item in coverage] == ["DP"]
    assert missing_primary_trackers(coverage) == ["ULCX", "IHD"]
    assert filter_inventory_rows([source], index, missing=["DP"]) == []
    assert filter_inventory_rows([source], index, missing=["IHD"]) == [source]
