from app.media_policy import analyze_mediainfo, apply_release_group_policy, build_media_manual_result, video_file_payloads


def test_mediainfo_analysis_accepts_single_file():
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
    assert result["verdict"] == "media_error"


def test_video_file_payloads_ignore_nfo_files():
    files = [
        {"name": "Release/one.nfo"},
        {"name": "Release/two.nfo"},
        {"name": "Release/video.mkv"},
    ]

    videos = video_file_payloads(files)
    manual = build_media_manual_result("mediainfo_unavailable", "No MediaInfo", files)

    assert [item["index"] for item in videos] == [2]
    assert manual["source"] == "mediainfo"
    assert manual["video_files"][0]["name"] == "Release/video.mkv"


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


def test_release_group_policy_blocks_banned_group_parsed_from_non_dash_tail():
    status, verdict, _reason, policy, flags = apply_release_group_policy(
        tracker_results={"passed": ["ULCX"], "dupe": [], "skipped": [], "error": []},
        arr_results={"status": "candidate", "decisions": [{"tracker": "ULCX", "status": "candidate"}]},
        release_group="Will1869",
        tracker_policies={"ULCX": {"banned_release_groups": ["Will1869"], "ranked_release_groups": []}},
        flags=[],
        item_name="Convicting.A.Murderer.2023.S01.1080p.WebRip.X264.Will1869",
    )

    assert status == "blocked"
    assert verdict == "banned_release_group"
    assert policy["blocked_trackers"] == ["ULCX"]
    assert flags[0]["key"] == "banned_release_group"


def test_release_group_policy_sends_missing_group_to_review():
    status, verdict, reason, policy, flags = apply_release_group_policy(
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        arr_results={"status": "candidate", "decisions": [{"tracker": "DP", "status": "candidate"}]},
        release_group="",
        tracker_policies={"DP": {"banned_release_groups": [], "ranked_release_groups": []}},
        flags=[],
        item_name="Odd.Release.7",
    )

    assert status == "manual_review"
    assert verdict == "manual_review"
    assert "release group" in reason
    assert policy["decisions"][0]["status"] == "manual_review"
    assert flags[0]["key"] == "missing_release_group"


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
