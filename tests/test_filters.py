from app.config import WatchConfig
from app.filters import is_watchable_torrent


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
