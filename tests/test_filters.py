from app.config import WatchConfig
from app.filters import is_completed_torrent, is_watchable_torrent


def test_watchable_completed_torrent_with_path_and_hash():
    torrent = {
        "progress": 1,
        "hash": "abc",
        "content_path": "/media/torrents/movie",
        "category": "movies",
        "tags": "",
    }

    assert is_watchable_torrent(torrent, WatchConfig())


def test_incomplete_torrent_is_not_watchable():
    torrent = {
        "progress": 0.5,
        "hash": "abc",
        "content_path": "/media/torrents/movie",
    }

    assert not is_watchable_torrent(torrent, WatchConfig())


def test_downloading_and_stalled_download_states_are_not_completed():
    assert not is_completed_torrent({"state": "downloading", "progress": 0.4, "amount_left": 100})
    assert not is_completed_torrent({"state": "stalledDL", "progress": 0.9, "amount_left": 100})


def test_seeding_and_stalled_upload_states_are_completed():
    assert is_completed_torrent({"state": "uploading", "progress": 1})
    assert is_completed_torrent({"state": "stalledUP", "progress": 1})
    assert is_completed_torrent({"state": "forcedUP", "progress": 1})


def test_cross_seed_category_is_excluded_by_default():
    torrent = {
        "progress": 1,
        "hash": "abc",
        "content_path": "/media/torrents/movie",
        "category": "cross-seed",
    }

    assert not is_watchable_torrent(torrent, WatchConfig())


def test_cross_seed_tag_is_excluded_by_default():
    torrent = {
        "progress": 1,
        "hash": "abc",
        "content_path": "/media/torrents/movie",
        "tags": "movies,cross-seed",
    }

    assert not is_watchable_torrent(torrent, WatchConfig())
