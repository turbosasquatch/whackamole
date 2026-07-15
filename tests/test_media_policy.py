from app.media_policy import (
    analyze_mediainfo,
    apply_release_group_policy,
    build_media_manual_result,
    merge_mediainfo_provider_results,
    video_file_payloads,
)


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


def test_mediainfo_analysis_marks_no_video_files_as_error():
    release = "Movie.2024.1080p.WEB-DL-GRP"

    result = analyze_mediainfo(item_name=release, files=[{"index": 0, "name": f"{release}/notes.nfo"}], mediainfo_payloads=[])

    assert result["status"] == "error"
    assert result["verdict"] == "no_video_files"
    assert any(flag["key"] == "no_video_files" for flag in result["flags"])


def test_mediainfo_analysis_marks_missing_mediainfo_as_error():
    release = "Movie.2024.1080p.WEB-DL-GRP"

    result = analyze_mediainfo(item_name=release, files=[{"index": 0, "name": f"{release}/{release}.mkv"}], mediainfo_payloads=[])

    assert result["status"] == "error"
    assert result["verdict"] == "mediainfo_missing"
    assert any(flag["key"] == "mediainfo_missing" for flag in result["flags"])


def test_mediainfo_analysis_blocks_bloated_1080p_bluray_audio():
    release = "Movie.2024.1080p.BluRay.DTS-HD.MA.5.1.x264-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                {
                    "@type": "Audio",
                    "Format": "DTS",
                    "Format_Commercial_IfAny": "DTS-HD Master Audio",
                    "Channels": "6",
                },
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert result["status"] == "manual_review"
    assert any(issue["key"] == "bloated_audio" for issue in result["issues"])
    assert any(flag["key"] == "bloated_audio" for flag in result["flags"])


def test_mediainfo_analysis_blocks_bloated_1080p_bluray_remux_audio():
    release = "Movie.2024.1080p.BluRay.REMUX.DTS-HD.MA.5.1.AVC-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "DTS", "Format_Commercial_IfAny": "DTS-HD Master Audio", "Channels": "6"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert result["status"] == "manual_review"
    assert any(issue["key"] == "bloated_audio" for issue in result["issues"])


def test_mediainfo_analysis_uses_confirmed_dts_hd_ma_for_bloated_audio():
    release = "The.King.and.I.1956.1080p.BluRay.DTS-HD.x264-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "DTS", "Format_Commercial_IfAny": "DTS-HD Master Audio", "Channels": "6"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert any(issue["key"] == "bloated_audio" for issue in result["issues"])
    assert not any(issue["key"] == "audio_codec_mismatch" for issue in result["issues"])


def test_mediainfo_analysis_blocks_multichannel_flac():
    release = "Father.of.the.Bride.1991.1080p.BluRay.FLAC.x264-O2STK"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "FLAC", "Channels": "6"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert result["status"] == "manual_review"
    assert any(issue["key"] == "bloated_audio" for issue in result["issues"])


def test_mediainfo_analysis_accepts_mp4_dolby_vision_codec_id():
    release = "It.Comes.at.Night.2017.2160p.WEB-DL.DD5.1.DV.MP4.x265-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mp4", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mp4",
            "streams": [
                {
                    "@type": "Video",
                    "Format": "HEVC",
                    "CodecID": "dvh1",
                    "CodecID_Info": "Dolby Vision",
                    "Width": "3840",
                    "Height": "2160",
                    "ScanType": "Progressive",
                    "BitDepth": "10",
                },
                {"@type": "Audio", "Format": "AC-3", "Channels": "6"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert "Dolby Vision" in result["mediainfo_files"][0]["traits"]["hdr_formats"]
    assert not any(issue["key"] == "dolby_vision_missing" for issue in result["issues"])


def test_mediainfo_analysis_blocks_undeclared_primary_language():
    release = "Movie.2024.2160p.WEB-DL.HDR.H.265-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Language": "German", "Default": "Yes"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert result["status"] == "manual_review"
    assert any(issue["key"] == "primary_language" for issue in result["issues"])
    assert any(flag["key"] == "primary_language" for flag in result["flags"])


def test_mediainfo_analysis_blocks_non_english_default_when_english_audio_exists():
    release = "Lee.Cronins.The.Mummy.2026.German.DL.HDR.2160p.WEB.h265-W4K"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "1600", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Language": "German", "Default": "Yes"},
                {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Language": "English", "Default": "No"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert result["status"] == "manual_review"
    assert any(issue["key"] == "primary_language" for issue in result["issues"])
    assert any(flag["key"] == "primary_language" for flag in result["flags"])


def test_mediainfo_analysis_treats_hebrew_language_code_as_confident_primary_language():
    release = "Tehran.S01E01.2160p.ATVP.WEB-DL.DDP5.1.H.265-HONE"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Language": "He", "Default": "Yes"},
                {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Language": "English", "Default": "No"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert any(issue["key"] == "primary_language" for issue in result["issues"])
    assert not any(issue["key"] == "primary_language_unverified" for issue in result["issues"])
    assert result["mediainfo_files"][0]["default_audio"]["language"] == "hebrew"


def test_mediainfo_analysis_reviews_unknown_primary_language_code():
    release = "Movie.2026.2160p.WEB-DL.DDP5.1.H.265-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    mediainfo = [
        {
            "fileIndex": 0,
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Language": "zz", "Default": "Yes"},
                {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Language": "English", "Default": "No"},
            ],
        }
    ]

    result = analyze_mediainfo(item_name=release, files=files, mediainfo_payloads=mediainfo)

    assert result["status"] == "manual_review"
    assert any(issue["key"] == "primary_language_unverified" for issue in result["issues"])
    assert not any(issue["key"] == "primary_language" for issue in result["issues"])
    assert any(flag["key"] == "primary_language_unverified" for flag in result["flags"])


def test_local_mediainfo_confirms_atmos_missing_from_qui():
    release = "Movie.2024.2160p.WEB-DL.DDP5.1.Atmos.H.265-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    qui = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "6"},
                ],
            }
        ],
    )
    local = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "media": {
                    "track": [
                        {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
                        {
                            "@type": "Audio",
                            "Format": "E-AC-3",
                            "Format_Commercial_IfAny": "Dolby Digital Plus with Dolby Atmos",
                            "Format_AdditionalFeatures": "JOC",
                            "Channels": "6",
                        },
                    ]
                },
            }
        ],
    )

    merged = merge_mediainfo_provider_results(qui, local)

    assert merged["status"] == "passed"
    assert not any(issue["key"] == "audio_object_missing" for issue in merged["issues"])
    assert merged["resolved_mediainfo_issues"][0]["key"] == "audio_object_missing"
    assert "Atmos" in merged["media_tags"]
    assert {tag["label"]: tag["state"] for tag in merged["title_tag_matches"]}["Atmos"] == "match"


def test_local_mediainfo_confirms_dolby_vision_missing_from_qui():
    release = "It.Comes.at.Night.2017.2160p.WEB-DL.DD5.1.DV.MP4.x265-DVSUX"
    files = [{"index": 0, "name": f"{release}/{release}.mp4", "size": 1000}]
    qui = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mp4",
                "streams": [
                    {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "AC-3", "Channels": "6", "Language": "en"},
                    {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                ],
            }
        ],
    )
    local = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mp4",
                "media": {
                    "track": [
                        {
                            "@type": "Video",
                            "Format": "HEVC",
                            "Width": "3840",
                            "Height": "2160",
                            "ScanType": "Progressive",
                            "CodecID": "dvh1.05",
                            "CodecID_Info": "Dolby Vision",
                        },
                        {"@type": "Audio", "Format": "AC-3", "Channels": "6", "Language": "en"},
                        {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                    ]
                },
            }
        ],
    )

    assert any(issue["key"] == "dolby_vision_missing" for issue in qui["issues"])

    merged = merge_mediainfo_provider_results(qui, local)

    assert merged["status"] == "passed"
    assert not any(issue["key"] == "dolby_vision_missing" for issue in merged["issues"])
    assert merged["resolved_mediainfo_issues"][0]["key"] == "dolby_vision_missing"
    assert "Dolby Vision" in merged["media_tags"]
    assert {tag["label"]: tag["state"] for tag in merged["title_tag_matches"]}["Dolby Vision"] == "match"


def test_mediainfo_missing_provider_field_is_not_disagreement():
    release = "Movie.2024.WEB-DL.DDP2.0-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    qui = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
                ],
            }
        ],
    )
    local = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
                ],
            }
        ],
    )

    merged = merge_mediainfo_provider_results(qui, local)

    assert not any(issue["key"] == "mediainfo_provider_disagreement" for issue in merged["issues"])


def test_mediainfo_provider_disagreement_requires_review():
    release = "Movie.2024.1080p.WEB-DL.DDP2.0.H.264-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    qui = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
                ],
            }
        ],
    )
    local = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "HEVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
                ],
            }
        ],
    )

    merged = merge_mediainfo_provider_results(qui, local)

    assert merged["status"] == "manual_review"
    assert any(issue["key"] == "mediainfo_provider_disagreement" for issue in merged["issues"])
    assert "AVC" not in merged["media_tags"]
    assert "HEVC" not in merged["media_tags"]
    assert {tag["label"]: tag["state"] for tag in merged["title_tag_matches"]}["AVC"] == "mismatch"


def test_mediainfo_provider_disagreement_treats_aac_and_he_aac_as_same_family():
    release = "Movie.2024.1080p.WEB-DL.AAC2.0.H.264-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    qui = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "AAC", "Channels": "2"},
                ],
            }
        ],
    )
    local = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "AAC", "Format_Profile": "HE-AAC", "Channels": "2"},
                ],
            }
        ],
    )

    merged = merge_mediainfo_provider_results(qui, local)

    assert not any(issue["key"] == "mediainfo_provider_disagreement" for issue in merged["issues"])


def test_mediainfo_provider_channel_disagreement_uses_primary_audio_title_confirmation():
    release = "Tunnelen.2019.1080p.BluRay.DD+7.1.x264-LoRD"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    qui = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "804", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Title": "DD+Plus 7.1 ch"},
                    {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                ],
            }
        ],
    )
    local = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "804", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "8"},
                    {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                ],
            }
        ],
    )

    merged = merge_mediainfo_provider_results(qui, local)

    assert merged["status"] == "passed"
    assert merged["verdict"] == "mediainfo_passed"
    assert any(issue["key"] == "audio_channels_mismatch" for issue in merged["resolved_mediainfo_issues"])
    assert not any(issue["key"] == "audio_channels_mismatch" for issue in merged["issues"])
    assert not any(issue["key"] == "mediainfo_provider_disagreement" for issue in merged["issues"])
    assert "7.1" in merged["media_tags"]
    assert {tag["label"]: tag["state"] for tag in merged["title_tag_matches"]}["7.1"] == "match"


def test_mediainfo_provider_channel_disagreement_requires_review_without_primary_audio_title_confirmation():
    release = "Movie.2024.1080p.BluRay.DD+7.1.x264-GRP"
    files = [{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}]
    qui = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "6"},
                ],
            }
        ],
    )
    local = analyze_mediainfo(
        item_name=release,
        files=files,
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1080", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "8"},
                ],
            }
        ],
    )

    merged = merge_mediainfo_provider_results(qui, local)

    assert merged["status"] == "manual_review"
    assert any(issue["key"] == "mediainfo_provider_disagreement" for issue in merged["issues"])


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


def test_episode_moderation_queue_skips_one_tracker_and_keeps_candidate():
    status, verdict, reason, policy, _flags = apply_release_group_policy(
        tracker_results={"passed": ["DP", "IHD"], "dupe": [], "skipped": [], "error": []},
        arr_results={"decisions": [
            {"tracker": "DP", "status": "candidate"},
            {"tracker": "IHD", "status": "candidate"},
        ]},
        release_group="GRP",
        tracker_policies={
            "DP": {"banned_release_groups": [], "moderation_queue": True},
            "IHD": {"banned_release_groups": [], "moderation_queue": False},
        },
        flags=[],
        item_name="Show.S01E02.1080p.WEB-DL-GRP",
        media_type="episode",
    )

    assert status == "candidate"
    assert verdict == "candidate"
    assert "IHD" in reason
    assert policy["version"] == 2
    assert policy["candidate_trackers"] == ["IHD"]
    assert policy["moderation_queue_trackers"] == ["DP"]
    assert next(row for row in policy["decisions"] if row["tracker"] == "DP")["status"] == "skipped"


def test_episode_all_moderation_queue_trackers_returns_policy_skip():
    status, verdict, _reason, policy, _flags = apply_release_group_policy(
        tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
        arr_results={"decisions": [{"tracker": "DP", "status": "candidate"}]},
        release_group="GRP",
        tracker_policies={"DP": {"banned_release_groups": [], "moderation_queue": True}},
        flags=[],
        item_name="Show.S01E02.1080p.WEB-DL-GRP",
        media_type="episode",
    )

    assert status == "skipped"
    assert verdict == "moderation_queue_no_targets"
    assert policy["candidate_trackers"] == []
    assert policy["moderation_queue_trackers"] == ["DP"]


def test_moderation_queue_does_not_affect_movies_or_season_packs():
    for name, media_type in (
        ("Movie.2026.1080p.WEB-DL-GRP", "movie"),
        ("Show.S01.1080p.WEB-DL-GRP", "season"),
    ):
        status, _verdict, _reason, policy, _flags = apply_release_group_policy(
            tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
            arr_results={"decisions": [{"tracker": "DP", "status": "candidate"}]},
            release_group="GRP",
            tracker_policies={"DP": {"banned_release_groups": [], "moderation_queue": True}},
            flags=[],
            item_name=name,
            media_type=media_type,
        )
        assert status == "candidate"
        assert policy["candidate_trackers"] == ["DP"]
        assert policy["moderation_queue_trackers"] == []


def test_banned_policy_wins_when_only_banned_and_moderation_trackers_remain():
    status, verdict, _reason, policy, _flags = apply_release_group_policy(
        tracker_results={"passed": ["DP", "IHD"], "dupe": [], "skipped": [], "error": []},
        arr_results={"decisions": [
            {"tracker": "DP", "status": "candidate"},
            {"tracker": "IHD", "status": "candidate"},
        ]},
        release_group="GRP",
        tracker_policies={
            "DP": {"banned_release_groups": [], "moderation_queue": True},
            "IHD": {"banned_release_groups": ["GRP"], "moderation_queue": False},
        },
        flags=[],
        item_name="Show.S01E02.1080p.WEB-DL-GRP",
        media_type="episode",
    )

    assert status == "blocked"
    assert verdict == "banned_release_group"
    assert policy["blocked_trackers"] == ["IHD"]
    assert policy["moderation_queue_trackers"] == ["DP"]
