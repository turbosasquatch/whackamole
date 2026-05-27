import pytest

from app.config import PathMapping
from app.pathmap import map_path


def test_map_path_uses_longest_matching_prefix():
    mappings = [
        PathMapping("/media", "/data"),
        PathMapping("/media/torrents", "/data/torrents"),
    ]

    assert map_path("/media/torrents/show/file.mkv", mappings) == "/data/torrents/show/file.mkv"


def test_map_path_matches_exact_source():
    assert map_path("/media/torrents", [PathMapping("/media/torrents", "/data/torrents")]) == "/data/torrents"


def test_map_path_raises_when_no_mapping_matches():
    with pytest.raises(ValueError):
        map_path("/other/file.mkv", [PathMapping("/media/torrents", "/data/torrents")])
