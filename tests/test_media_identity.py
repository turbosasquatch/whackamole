from app.media_identity import analyze_media_payloads, parse_release_traits


def test_release_identity_normalizes_common_user_aliases():
    traits = parse_release_traits("Movie.2024.2160p.WEB-DL.DD+5.1.DoVi.H265-GRP")

    assert traits.audio_format == "DD+"
    assert traits.audio_channels == 5.1
    assert traits.codec == "HEVC"
    assert "Dolby Vision" in traits.hdr_formats
    assert traits.release_group == "GRP"


def test_release_identity_parses_truehd_dovi_and_h265_aliases():
    traits = parse_release_traits("Movie.2024.2160p.BluRay.REMUX.TruHD.7.1.DoVi.H265-GRP")

    assert traits.source == "bluray_remux"
    assert traits.audio_format == "TrueHD"
    assert traits.audio_channels == 7.1
    assert traits.codec == "HEVC"
    assert "Dolby Vision" in traits.hdr_formats


def test_release_identity_parses_dts_hd_ma():
    traits = parse_release_traits("Movie.2024.1080p.BluRay.DTS-HD.MA.5.1.AVC-GRP")

    assert traits.audio_format == "DTS-HD MA"
    assert traits.audio_channels == 5.1
    assert traits.codec == "AVC"


def test_media_analysis_blocks_clear_metadata_mismatch():
    release = "Movie.2024.1080p.WEB-DL.DDP5.1.H.264-GRP"
    result = analyze_media_payloads(
        release_title=release,
        media_files=[{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}],
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

    assert result["status"] == "manual_review"
    assert result["media_status"] == "error"
    assert any(issue["key"] == "audio_codec_mismatch" and issue["severity"] == "ERROR" for issue in result["issues"])


def test_media_analysis_blocks_missing_claimed_atmos_metadata():
    release = "Movie.2024.2160p.WEB-DL.DDP5.1.Atmos.H.265-GRP"
    result = analyze_media_payloads(
        release_title=release,
        media_files=[{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}],
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "6"},
                    {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                ],
            }
        ],
    )

    assert result["media_status"] == "error"
    assert any(issue["key"] == "audio_object_missing" and issue["severity"] == "ERROR" for issue in result["issues"])


def test_media_analysis_keeps_warnings_non_blocking():
    release = "Movie.2024.1080p.WEB-DL.DDP5.1.H.264-GRP"
    result = analyze_media_payloads(
        release_title=release,
        media_files=[{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}],
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {
                        "@type": "Video",
                        "Format": "AVC",
                        "Width": "1920",
                        "Height": "800",
                        "ScanType": "Progressive",
                        "BitRate": "3000000",
                    },
                    {
                        "@type": "Audio",
                        "Format": "E-AC-3",
                        "Format_Commercial_IfAny": "Dolby Digital Plus",
                        "Channels": "6",
                    },
                ],
            }
        ],
    )

    assert result["status"] == "passed"
    assert result["media_status"] == "warning"
    assert any(issue["key"] == "video_bitrate_low" and issue["severity"] == "WARNING" for issue in result["issues"])
