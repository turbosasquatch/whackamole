from app.nfo_policy import (
    analyze_mediainfo,
    analyze_nfo,
    apply_release_group_policy,
    build_nfo_manual_result,
    decode_nfo_bytes,
    nfo_file_candidates,
    parse_nfo_complete_names,
    parse_nfo_release_title,
)


NFO = """
+---------+
| Release |
+---------+
Maxine.S01.BluRay.1080i.DTS-HD.MA.5.1.AVC.REMUX-GRP

+-----------+
| Mediainfo |
+-----------+
General
Complete name                            : Maxine.S01E01.1080i.DTS-HD.MA.5.1.AVC.REMUX-GRP.mkv
"""


def test_nfo_parser_extracts_release_and_complete_names():
    assert parse_nfo_release_title(NFO) == "Maxine.S01.BluRay.1080i.DTS-HD.MA.5.1.AVC.REMUX-GRP"
    assert parse_nfo_complete_names(NFO) == ["Maxine.S01E01.1080i.DTS-HD.MA.5.1.AVC.REMUX-GRP.mkv"]


def test_nfo_analysis_accepts_matching_season_pack_with_episode_complete_name():
    release = "Maxine.S01.BluRay.1080i.DTS-HD.MA.5.1.AVC.REMUX-GRP"
    files = [
        {"index": 0, "name": f"{release}/{release}.nfo", "size": 100},
        {"index": 1, "name": f"{release}/Maxine.S01E01.1080i.DTS-HD.MA.5.1.AVC.REMUX-GRP.mkv", "size": 1000},
    ]

    result = analyze_nfo(item_name=release, files=files, nfo_file=files[0], nfo_text=NFO)

    assert result["status"] == "passed"
    assert result["release_group"] == "GRP"
    assert result["flags"] == []


def test_nfo_analysis_flags_renamed_video_without_rejecting():
    release = "Maxine.S01.BluRay.1080i.DTS-HD.MA.5.1.AVC.REMUX-GRP"
    files = [
        {"index": 0, "name": f"{release}/{release}.nfo", "size": 100},
        {"index": 1, "name": f"{release}/renamed-file.mkv", "size": 1000},
    ]

    result = analyze_nfo(item_name=release, files=files, nfo_file=files[0], nfo_text=NFO)

    assert result["status"] == "passed"
    assert result["flags"][0]["key"] == "renamed_files"


def test_nfo_analysis_rejects_title_mismatch():
    release = "Maxine.S01.BluRay.1080i.DTS-HD.MA.5.1.AVC.REMUX-GRP"
    files = [
        {"index": 0, "name": f"{release}/{release}.nfo", "size": 100},
        {"index": 1, "name": f"{release}/{release}.mkv", "size": 1000},
    ]

    result = analyze_nfo(
        item_name=release,
        files=files,
        nfo_file=files[0],
        nfo_text=NFO.replace("Maxine.S01.BluRay", "Other.Show.S01.BluRay"),
    )

    assert result["status"] == "manual_review"
    assert result["verdict"] == "nfo_mismatch"


def test_mediainfo_analysis_accepts_single_file_without_nfo():
    release = "Two.Distant.Strangers.2020.1080p.WEB.h264-EDITH"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1514907502}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "E-AC-3", "Format_Commercial_IfAny": "Dolby Digital Plus", "Channels": "6"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert result["status"] == "passed"
    assert result["verdict"] == "mediainfo_passed"
    assert result["source"] == "mediainfo"
    assert result["release_group"] == "EDITH"
    assert result["mediainfo_files"][0]["traits"]["resolution"] == "1080p"


def test_mediainfo_analysis_rejects_trait_mismatch():
    release = "Movie.2024.2160p.WEB.h265-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [{"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"}],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert result["status"] == "manual_review"
    assert result["verdict"] == "mediainfo_mismatch"


def test_multiple_nfo_candidates_are_discoverable_for_ambiguous_gate():
    files = [
        {"name": "Release/one.nfo"},
        {"name": "Release/two.nfo"},
        {"name": "Release/video.mkv"},
    ]

    candidates = nfo_file_candidates(files)
    manual = build_nfo_manual_result("nfo_ambiguous", "Multiple NFO files were found.", files)

    assert [item["index"] for item in candidates] == [0, 1]
    assert manual["verdict"] == "nfo_ambiguous"


def test_release_group_policy_blocks_only_banned_tracker():
    status, verdict, reason, policy, flags = apply_release_group_policy(
        tracker_results={"passed": ["DP", "IHD"], "dupe": [], "skipped": [], "error": []},
        arr_results={
            "status": "candidate",
            "decisions": [
                {"tracker": "DP", "status": "candidate"},
                {"tracker": "IHD", "status": "candidate"},
            ],
        },
        release_group="GRP",
        tracker_policies={
            "DP": {"banned_release_groups": ["GRP"], "ranked_release_groups": []},
            "IHD": {"banned_release_groups": [], "ranked_release_groups": ["GRP"]},
        },
        flags=[],
        item_name="Movie.2024.1080p.WEB-DL-GRP",
    )

    assert status == "candidate"
    assert verdict == "candidate"
    assert "IHD" in reason
    assert policy["blocked_trackers"] == ["DP"]
    assert policy["candidate_trackers"] == ["IHD"]
    assert flags[0]["key"] == "banned_release_group"


def test_release_group_policy_blocks_when_all_candidates_banned():
    status, verdict, _reason, policy, _flags = apply_release_group_policy(
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        arr_results={"status": "candidate", "decisions": [{"tracker": "DP", "status": "candidate"}]},
        release_group="GRP",
        tracker_policies={"DP": {"banned_release_groups": ["GRP"], "ranked_release_groups": []}},
        flags=[],
        item_name="Movie.2024.1080p.WEB-DL-GRP",
    )

    assert status == "blocked"
    assert verdict == "banned_release_group"
    assert policy["candidate_trackers"] == []


def test_decode_nfo_bytes_handles_common_encoding():
    assert "Release" in decode_nfo_bytes("Release".encode("cp1252"))
