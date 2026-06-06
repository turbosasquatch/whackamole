from __future__ import annotations

import asyncio

from app.srrdb import apply_srrdb_result, archived_video_filenames, srrdb_lookup_name, verify_srrdb_release


class MemoryCache:
    def __init__(self):
        self.values = {}

    def get_kv(self, key):
        return self.values.get(key)

    def set_kv(self, key, value):
        self.values[key] = value


class FakeSrrdbClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def details(self, release_name):
        self.calls.append(release_name)
        return self.payload


def test_srrdb_lookup_name_turns_local_spaces_into_scene_style_name():
    assert (
        srrdb_lookup_name("The Panic in Needle Park 1971 1080p BluRay X264-AMIABLE.mkv")
        == "The.Panic.in.Needle.Park.1971.1080p.BluRay.X264-AMIABLE"
    )


def test_archived_video_filenames_keeps_only_archived_video_entries():
    payload = {
        "archived-files": [
            {"name": "Movie.2024.1080p.BluRay-GRP.mkv"},
            {"name": "Sample/Movie.2024.1080p.BluRay-GRP.sample.mkv"},
        ],
        "files": [{"name": "movie.rar"}],
    }

    assert archived_video_filenames(payload) == [
        "Movie.2024.1080p.BluRay-GRP.mkv",
        "Movie.2024.1080p.BluRay-GRP.sample.mkv",
    ]


def test_srrdb_verifier_marks_exact_archived_filename_as_verified():
    client = FakeSrrdbClient({"archived-files": [{"name": "Movie.2024.1080p.BluRay-GRP.mkv"}]})

    result = asyncio.run(
        verify_srrdb_release(
            item_name="Movie.2024.1080p.BluRay-GRP",
            media_result={"torrent_root": "Movie.2024.1080p.BluRay-GRP", "complete_names": ["Movie.2024.1080p.BluRay-GRP.mkv"]},
            client=client,
            cache=MemoryCache(),
            now=1000,
        )
    )

    assert result["status"] == "verified"
    assert result["matched"] is True
    assert client.calls == ["Movie.2024.1080p.BluRay-GRP"]


def test_srrdb_verifier_marks_archived_filename_mismatch_with_proper_filename():
    client = FakeSrrdbClient(
        {"archived-files": [{"name": "The.Panic.in.Needle.Park.1971.1080p.BluRay.X264-AMIABLE.mkv"}]}
    )

    result = asyncio.run(
        verify_srrdb_release(
            item_name="The Panic in Needle Park 1971 1080p BluRay X264-AMIABLE",
            media_result={
                "torrent_root": "The Panic in Needle Park 1971 1080p BluRay X264-AMIABLE",
                "complete_names": ["The Panic in Needle Park 1971 1080p BluRay X264-AMIABLE.mkv"],
            },
            client=client,
            cache=MemoryCache(),
            now=1000,
        )
    )

    assert result["status"] == "mismatch"
    assert result["proper_filenames"] == ["The.Panic.in.Needle.Park.1971.1080p.BluRay.X264-AMIABLE.mkv"]
    assert "Proper filename should be" in result["reason"]


def test_srrdb_verifier_marks_archived_size_mismatch_as_modified():
    client = FakeSrrdbClient({"archived-files": [{"name": "Movie.2024.1080p.BluRay-GRP.mkv", "size": 2000}]})

    result = asyncio.run(
        verify_srrdb_release(
            item_name="Movie.2024.1080p.BluRay-GRP",
            media_result={
                "torrent_root": "Movie.2024.1080p.BluRay-GRP",
                "complete_names": ["Movie.2024.1080p.BluRay-GRP.mkv"],
                "video_files": [{"basename": "Movie.2024.1080p.BluRay-GRP.mkv", "size": 1000}],
            },
            client=client,
            cache=MemoryCache(),
            now=1000,
        )
    )

    assert result["status"] == "mismatch"
    assert result["matched"] is False
    assert "file size mismatch" in result["reason"]


def test_srrdb_verifier_caches_not_found_without_status_change():
    cache = MemoryCache()
    client = FakeSrrdbClient([])
    media_result = {"torrent_root": "Missing.Movie.2024.1080p.WEB-DL-GRP", "complete_names": ["Missing.Movie.2024.1080p.WEB-DL-GRP.mkv"]}

    first = asyncio.run(
        verify_srrdb_release(
            item_name="Missing.Movie.2024.1080p.WEB-DL-GRP",
            media_result=media_result,
            client=client,
            cache=cache,
            now=1000,
        )
    )
    second = asyncio.run(
        verify_srrdb_release(
            item_name="Missing.Movie.2024.1080p.WEB-DL-GRP",
            media_result=media_result,
            client=client,
            cache=cache,
            now=1001,
        )
    )

    assert first["status"] == "not_found"
    assert second["status"] == "not_found"
    assert client.calls == ["Missing.Movie.2024.1080p.WEB-DL-GRP"]


def test_srrdb_result_mismatch_overrides_candidate_to_manual_review():
    status, verdict, reason, flags = apply_srrdb_result(
        status="candidate",
        verdict="candidate",
        reason="Valid upload candidate on: DP",
        flags=[],
        srrdb_result={
            "status": "mismatch",
            "reason": "srrDB archived filename mismatch. Proper filename should be: Proper.Name-GRP.mkv",
        },
    )

    assert status == "manual_review"
    assert verdict == "srrdb_filename_mismatch"
    assert "Proper filename" in reason
    assert flags[0]["key"] == "srrdb_filename_mismatch"
