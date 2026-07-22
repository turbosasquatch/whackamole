import json
from pathlib import Path

from app.media_identity import (
    analyze_media_payloads,
    extract_release_group,
    language_is_confident,
    normalize_language_label,
    parse_release_traits,
    release_is_equal_or_better,
    traits_from_mediainfo,
    traits_payload,
)
from app.source_providers import (
    extract_provider_abbreviation,
    extract_provider_from_release_title,
    provider_abbreviation_for_label,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mediainfo"


def test_release_identity_normalizes_common_user_aliases():
    traits = parse_release_traits("Movie.2024.2160p.WEB-DL.DD+5.1.DoVi.H265-GRP")

    assert traits.audio_format == "DD+"
    assert traits.audio_channels == 5.1
    assert traits.codec == "HEVC"
    assert "Dolby Vision" in traits.hdr_formats
    assert traits.release_group == "GRP"


def test_language_normalization_preserves_multiple_languages_alias():
    assert normalize_language_label("Multiple Languages") == "multi"
    assert language_is_confident("Multiple Languages")


def test_source_provider_can_be_extracted_from_nfo_long_and_short_names():
    assert extract_provider_abbreviation("Site: Netflix") == "NF"
    assert extract_provider_abbreviation("Network: Amazon Prime Video") == "AMZN"
    assert extract_provider_abbreviation("Source : DSNP") == "DSNP"


def test_source_provider_can_be_extracted_from_release_title_web_position():
    assert extract_provider_from_release_title("Amy_Bradley_Is_Missing_S01E03_2025_2160p_NF_WEB-DL_DDP5_1") == "NF"
    assert extract_provider_from_release_title("Squatters.S01E07.1080p.HULU.WEB-DL.AAC2.0") == "HULU"
    assert extract_provider_from_release_title("24.Hours.in.Police.Custody.S01.1080p.Amazon.WEB-DL.DD+.2.0.x264-TrollHD") == "AMZN"
    assert extract_provider_from_release_title("She.Said.2022.2160p.MA.WEB-DL.DDP5.1.Atmos.H.265-HONE") == "MA"
    assert extract_provider_from_release_title("Shrinking.S03.2160p.ATV.WEB-DL.DDP5.1.Atmos.H.265-HONE") == "ATVP"


def test_source_provider_recognises_movies_anywhere_and_apple_tv_aliases():
    assert provider_abbreviation_for_label("Movies Anywhere") == "MA"
    assert provider_abbreviation_for_label("Apple TV") == "ATVP"
    assert extract_provider_abbreviation("Source: MA") == "MA"


def test_source_provider_title_extraction_avoids_short_title_words():
    assert extract_provider_from_release_title("It.2026.1080p.WEB-DL.DDP5.1.H.264-GRP") == ""


def test_source_provider_preserves_longest_alias_precedence_and_boundaries():
    assert extract_provider_abbreviation("Source: HBO Max") == "HMAX"
    assert extract_provider_abbreviation("Source: Amazon Prime Video") == "AMZN"
    assert extract_provider_abbreviation("Source: huluish") == ""


def test_source_provider_title_lookup_preserves_short_token_and_false_positive_rules():
    assert extract_provider_from_release_title("Film.2026.1080p.MA.WEB-DL-GRP") == "MA"
    assert extract_provider_from_release_title("Film.2026.1080p.HULUISH.WEB-DL-GRP") == ""


def test_source_provider_title_lookup_distinguishes_plain_and_plus_services():
    assert extract_provider_from_release_title("Film.2026.1080p.Disney.WEB-DL-GRP") == "DSNY"
    assert extract_provider_from_release_title("Film.2026.1080p.Disney+.WEB-DL-GRP") == "DSNP"
    assert extract_provider_from_release_title("Film.2026.1080p.Canal+.WEB-DL-GRP") == "CNLP"
    assert extract_provider_from_release_title("Film.2026.1080p.Discovery+.WEB-DL-GRP") == "DSCP"
    assert extract_provider_from_release_title("Film.2026.1080p.Paramount+.WEB-DL-GRP") == "PMTP"
    assert extract_provider_from_release_title("Film.2026.1080p.Star+.WEB-DL-GRP") == "STRP"


def test_release_identity_parses_symbol_release_group():
    traits = parse_release_traits("1923.S02E01.2160p.WEBRip.DDP5.1.DV.HDR.H.265-R&H")

    assert traits.release_group == "R&H"


def test_release_identity_parses_non_dash_tail_group_but_rejects_format_tail():
    assert extract_release_group("Convicting.A.Murderer.2023.S01.1080p.WebRip.X264.Will1869") == "Will1869"
    assert extract_release_group("Tom Clancys Jack Ryan (2018) S03 (2160p AMZN WEB-DL H265 HDR10+ DDP Atmos 5.1 English - HONE)") == "HONE"
    assert extract_release_group("1917.(2019).(2160p.MA.WEB-DL.Hybrid.H265.DV.HDR.DDP.Atmos.5.1.English.-.HONE)") == "HONE"
    assert extract_release_group("Odd.Release.7") == ""
    assert extract_release_group("Movie.2024.2160p.WEB-DL.H.265") == ""
    assert extract_release_group("Mile.22.2018.HYBRiD.2160p.WEB-DL.DoVi.HDR10Plus.HEVC.DTS-HD.MA.7") == ""


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


def test_release_identity_parses_compact_dts_hd_ma():
    traits = parse_release_traits("Free.State.of.Jones.2016.1080p.BluRay.DTS-HD.MA5.1.x264-iFT")

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


def test_media_display_tags_are_only_confirmed_from_mediainfo():
    release = "Movie.2024.2160p.BluRay.TrueHD.Atmos.7.1.HDR10.x265-GRP"
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
                        "Format": "HEVC",
                        "Width": "3840",
                        "Height": "2160",
                        "ScanType": "Progressive",
                        "BitDepth": "10",
                        "HDR_Format": "HDR10",
                    },
                    {"@type": "Audio", "Format": "TrueHD", "Channels": "6"},
                    {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                ],
            }
        ],
    )
    title_states = {tag["label"]: tag["state"] for tag in result["title_tag_matches"]}

    assert "5.1" in result["media_tags"]
    assert "7.1" not in result["media_tags"]
    assert "7.1" not in result["confirmed_tags"]
    assert title_states["2160p"] == "match"
    assert title_states["HEVC"] == "match"
    assert title_states["HDR10"] == "match"
    assert title_states["7.1"] == "mismatch"
    assert title_states["Atmos"] == "mismatch"
    assert title_states["BluRay"] == "neutral"


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


def test_media_analysis_ignores_sample_file_bitrate_warnings():
    release = "True.Story.2015.BluRay.1080p.DTS.x264-iFT"
    result = analyze_media_payloads(
        release_title=release,
        media_files=[
            {"index": 0, "name": f"{release}/Sample.mkv", "size": 1000},
            {"index": 1, "name": f"{release}/{release}.mkv", "size": 1000},
        ],
        mediainfo_payloads=[
            {
                "fileIndex": 0,
                "relativePath": f"{release}/Sample.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1040", "ScanType": "Progressive", "BitRate": "22000000"},
                    {"@type": "Audio", "Format": "DTS", "Channels": "6"},
                ],
            },
            {
                "fileIndex": 1,
                "relativePath": f"{release}/{release}.mkv",
                "streams": [
                    {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1040", "ScanType": "Progressive", "BitRate": "14000000"},
                    {"@type": "Audio", "Format": "DTS", "Channels": "6"},
                    {"@type": "Text", "Language": "en"},
                ],
            },
        ],
    )

    assert result["media_status"] == "confirmed"
    assert not any(issue["key"] == "video_bitrate_high" for issue in result["issues"])


def test_media_analysis_does_not_warn_when_bitrate_is_exactly_boundary():
    release = "True.Story.2015.BluRay.1080p.DTS.x264-iFT"
    result = _analyze_sample(
        release,
        {
            "relativePath": f"{release}/{release}.mkv",
            "streams": [
                {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1040", "ScanType": "Progressive", "BitRate": "22000000"},
                {"@type": "Audio", "Format": "DTS", "Channels": "6"},
                {"@type": "Text", "Language": "en"},
            ],
        },
    )

    assert not any(issue["key"] == "video_bitrate_high" for issue in result["issues"])


def test_real_shape_item_2719_infers_hdr10_without_hdr_format():
    release = "Straight.Outta.Compton.2015.Directors.Cut.UHD.BluRay.2160p.DDP.7.1.HDR.x265-hallowed"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "5"},
            {
                "@type": "Video",
                "Format": "HEVC",
                "Format_Profile": "Main 10",
                "Width": "3840",
                "Height": "1600",
                "BitDepth": "10",
                "colour_primaries": "BT.2020",
                "transfer_characteristics": "PQ",
                "matrix_coefficients": "BT.2020 non-constant",
            },
            {
                "@type": "Audio",
                "Format": "E-AC-3",
                "Format_Commercial_IfAny": "Dolby Digital Plus",
                "Channels": "8",
                "Language": "en",
                "Default": "Yes",
            },
            {"@type": "Text", "Format": "PGS", "Language": "en"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert result["media_status"] in {"confirmed", "warning"}
    assert "HDR10" in traits["hdr_formats"]
    assert not any(issue["key"] == "hdr10_missing" for issue in result["issues"])


def test_real_shape_item_2785_detects_dv_profile_8_and_hdr10_compatibility():
    release = "1923.S02.E01.The.Killing.Season.2160p.WEBRip.DDP5.1.DV.HDR.H.265-R&H"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "3"},
            {
                "@type": "Video",
                "Format": "HEVC",
                "Width": "3840",
                "Height": "2160",
                "BitDepth": "10",
                "HDR_Format": "Dolby Vision",
                "HDR_Format_Compatibility": "HDR10",
                "HDR_Format_Profile": "dvhe.08",
                "HDR_Format_Settings": "BL+RPU",
                "colour_primaries": "BT.2020",
                "transfer_characteristics": "PQ",
            },
            {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "Language": "en-US", "Default": "Yes"},
            {"@type": "Text", "Format": "UTF-8", "Language": "en-US"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert {"Dolby Vision", "HDR10"}.issubset(set(traits["hdr_formats"]))
    assert traits["dv_profile"] == "DV P8"
    assert not any(issue["severity"] == "ERROR" for issue in result["issues"])


def test_release_comparison_treats_hdr_title_as_same_for_dv_hdr_fallback_local():
    local = parse_release_traits("CODA.2021.2160p.ATVP.WEB-DL.DDP5.1.Atmos.DV.HDR.HEVC-XEBEC")
    remote = parse_release_traits("Coda.2021.2160p.ATVP.WEB-DL.DDP5.1.Atmos.HDR.H.265-FLUX.mkv")
    dv_only = parse_release_traits("Under.the.Bridge.S01.2160p.DSNP.WEB-DL.DD+5.1.DV.H.265-FLUX")
    hdr_only = parse_release_traits("Under.the.Bridge.S01.2160p.DSNP.WEB-DL.DD+5.1.HDR.H.265-OTHER")

    assert release_is_equal_or_better(local, remote)
    assert not release_is_equal_or_better(dv_only, hdr_only)


def test_live_qui_stream_wrappers_prefer_raw_json_tracks():
    release = "1923.S02.E01.The.Killing.Season.2160p.WEBRip.DDP5.1.DV.HDR.H.265-R&H"
    raw_payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "3"},
            {
                "@type": "Video",
                "Format": "HEVC",
                "Width": "3840",
                "Height": "2160",
                "BitDepth": "10",
                "HDR_Format": "Dolby Vision",
                "HDR_Format_Compatibility": "HDR10",
                "HDR_Format_Profile": "dvhe.08",
                "HDR_Format_Settings": "BL+RPU",
                "colour_primaries": "BT.2020",
                "transfer_characteristics": "PQ",
            },
            {"@type": "Audio", "Format": "E-AC-3", "Format_Commercial_IfAny": "Dolby Digital Plus", "Channels": "6", "Language": "en-US", "Default": "Yes"},
            {"@type": "Text", "Format": "UTF-8", "Language": "en-US"},
        ],
    )
    payload = {
        "fileIndex": 0,
        "relativePath": f"{release}/{release}.mkv",
        "streams": [
            {"kind": "Video", "fields": [{"name": "Format", "value": "HEVC"}]},
            {"kind": "Audio", "fields": [{"name": "Format", "value": "E-AC-3"}]},
        ],
        "rawJSON": json.dumps(raw_payload),
    }

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert {"Dolby Vision", "HDR10"}.issubset(set(traits["hdr_formats"]))
    assert traits["audio_format"] == "DD+"
    assert "Subtitles" in traits["subtitle_tags"]
    assert not any(issue["severity"] == "ERROR" for issue in result["issues"])


def test_live_qui_stream_field_wrappers_are_flattened_without_raw_json():
    release = "1923.S02.E01.The.Killing.Season.2160p.WEBRip.DDP5.1.DV.HDR.H.265-R&H"
    payload = {
        "fileIndex": 0,
        "relativePath": f"{release}/{release}.mkv",
        "streams": [
            {
                "kind": "Video",
                "fields": [
                    {"name": "Format", "value": "HEVC"},
                    {"name": "HDR format", "value": "Dolby Vision, Version 1.0, Profile 8.1, dvhe.08.06, BL+RPU, HDR10 compatible"},
                    {"name": "Width", "value": "3 840 pixels"},
                    {"name": "Height", "value": "2 160 pixels"},
                    {"name": "Bit depth", "value": "10 bits"},
                    {"name": "Color primaries", "value": "BT.2020"},
                    {"name": "Transfer characteristics", "value": "PQ"},
                ],
            },
            {
                "kind": "Audio",
                "fields": [
                    {"name": "Format", "value": "E-AC-3"},
                    {"name": "Commercial name", "value": "Dolby Digital Plus"},
                    {"name": "Channel(s)", "value": "6 channels"},
                    {"name": "Language", "value": "English (US)"},
                    {"name": "Default", "value": "Yes"},
                ],
            },
            {"kind": "Text", "fields": [{"name": "Format", "value": "UTF-8"}, {"name": "Language", "value": "English (US)"}]},
            {"kind": "Text", "fields": [{"name": "Format", "value": "UTF-8"}, {"name": "Language", "value": "English (US)"}, {"name": "Title", "value": "SDH"}]},
            {"kind": "Text", "fields": [{"name": "Format", "value": "UTF-8"}, {"name": "Language", "value": "Portuguese"}, {"name": "Default", "value": "Yes"}]},
        ],
    }

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert {"Dolby Vision", "HDR10"}.issubset(set(traits["hdr_formats"]))
    assert traits["audio_format"] == "DD+"
    assert traits["audio_channels"] == 5.1
    assert traits["languages"] == ["english"]
    assert traits["subtitle_tags"] == ["Subtitles", "Default Subs"]
    assert not any(issue["severity"] == "ERROR" for issue in result["issues"])


def test_mediainfo_mul_audio_is_treated_as_multi_not_missing_english():
    release = "The.Cave.2019.1080p.BluRay.DD-EX.5.1.x264-iFT"
    payload = {
        "fileIndex": 0,
        "relativePath": f"{release}/{release}.mkv",
        "streams": [
            {
                "kind": "Video",
                "fields": [
                    {"name": "Format", "value": "AVC"},
                    {"name": "Width", "value": "1 920 pixels"},
                    {"name": "Height", "value": "808 pixels"},
                    {"name": "Language", "value": "English"},
                ],
            },
            {
                "kind": "Audio",
                "fields": [
                    {"name": "Format", "value": "AC-3"},
                    {"name": "Commercial name", "value": "Dolby Digital"},
                    {"name": "Channel(s)", "value": "6 channels"},
                    {"name": "Language", "value": "mul"},
                    {"name": "Title", "value": "AC3 DD-EX 5.1"},
                ],
            },
            {"kind": "Text", "fields": [{"name": "Format", "value": "UTF-8"}, {"name": "Language", "value": "English"}]},
        ],
    }

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert traits["languages"] == ["multi"]
    assert not any(issue["key"] == "no_english_audio" for issue in result["issues"])


def test_live_qui_display_fields_infer_hdr10_without_hdr_format():
    release = "Straight.Outta.Compton.2015.Directors.Cut.UHD.BluRay.2160p.DDP.7.1.HDR.x265-hallowed"
    payload = {
        "fileIndex": 0,
        "relativePath": f"{release}/{release}.mkv",
        "streams": [
            {
                "kind": "Video",
                "fields": [
                    {"name": "Format", "value": "HEVC"},
                    {"name": "Width", "value": "3 840 pixels"},
                    {"name": "Height", "value": "1 600 pixels"},
                    {"name": "Bit depth", "value": "10 bits"},
                    {"name": "Color primaries", "value": "BT.2020"},
                    {"name": "Transfer characteristics", "value": "PQ"},
                ],
            },
            {
                "kind": "Audio",
                "fields": [
                    {"name": "Format", "value": "E-AC-3"},
                    {"name": "Commercial name", "value": "Dolby Digital Plus"},
                    {"name": "Channel(s)", "value": "8 channels"},
                    {"name": "Language", "value": "English"},
                    {"name": "Default", "value": "Yes"},
                ],
            },
            {"kind": "Text", "fields": [{"name": "Format", "value": "PGS"}, {"name": "Language", "value": "English"}]},
        ],
    }

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert "HDR10" in traits["hdr_formats"]
    assert not any(issue["key"] == "hdr10_missing" for issue in result["issues"])


def test_static_hdr10_metadata_counts_without_transfer_or_primaries():
    release = "Untold.The.Death.and.Life.of.Lamar.Odom.2026.HDR.2160p.WEB.h265-EDITH"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "2"},
            {
                "@type": "Video",
                "Format": "HEVC",
                "Width": "3840",
                "Height": "2160",
                "Format_Profile": "Main 10",
                "BitDepth": "10",
                "MasteringDisplay_ColorPrimaries": "Display P3",
                "MasteringDisplay_Luminance": "min: 0.0001 cd/m2, max: 1000 cd/m2",
                "MaxCLL": "658 cd/m2",
                "MaxFALL": "211 cd/m2",
            },
            {
                "@type": "Audio",
                "Format": "E-AC-3",
                "Format_Commercial_IfAny": "Dolby Digital Plus with Dolby Atmos",
                "Format_AdditionalFeatures": "JOC",
                "Channels": "6",
                "Default": "Yes",
            },
            {"@type": "Text", "Format": "UTF-8", "Language": "en"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert "HDR10" in traits["hdr_formats"]
    assert not any(issue["key"] == "hdr10_missing" for issue in result["issues"])


def test_fixture_static_hdr10_metadata_counts_without_transfer_or_primaries():
    release = "Untold.The.Death.and.Life.of.Lamar.Odom.2026.HDR.2160p.WEB.h265-EDITH"
    result = _analyze_sample(release, _fixture("static_hdr10.json"))
    traits = result["mediainfo_files"][0]["traits"]

    assert "HDR10" in traits["hdr_formats"]
    assert not any(issue["key"] == "hdr10_missing" for issue in result["issues"])


def test_secondary_atmos_track_satisfies_atmos_claim():
    release = "Marty.Supreme.2025.2160p.UHD.BluRay.HDR10Plus.DoVi.TrueHD.7.1.Atmos.x265-SPHD"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "4"},
            {
                "@type": "Video",
                "Format": "HEVC",
                "Width": "3840",
                "Height": "2160",
                "BitDepth": "10",
                "HDR_Format": "Dolby Vision / SMPTE ST 2094 App 4",
                "HDR_Format_Compatibility": "HDR10",
            },
            {"@type": "Audio", "Format": "TrueHD", "Channels": "8", "Default": "Yes"},
            {
                "@type": "Audio",
                "Format": "E-AC-3",
                "CommercialName": "Dolby Digital Plus with Dolby Atmos",
                "Format_AdditionalFeatures": "JOC",
                "Title": "E-AC-3 JOC",
                "Channels": "6",
                "extra": {"NumberOfDynamicObjects": "15"},
            },
            {"@type": "Text", "Format": "PGS", "Language": "en"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert "Atmos" in traits["audio_objects"]
    assert not any(issue["key"] == "audio_object_missing" for issue in result["issues"])


def test_fixture_secondary_atmos_track_satisfies_atmos_claim():
    release = "Marty.Supreme.2025.2160p.UHD.BluRay.HDR10Plus.DoVi.TrueHD.7.1.Atmos.x265-SPHD"
    result = _analyze_sample(release, _fixture("secondary_atmos.json"))
    traits = result["mediainfo_files"][0]["traits"]

    assert "Atmos" in traits["audio_objects"]
    assert not any(issue["key"] == "audio_object_missing" for issue in result["issues"])


def test_fixture_live_qui_stream_wrapper_shape():
    release = "1923.S02.E01.The.Killing.Season.2160p.WEBRip.DDP5.1.DV.HDR.H.265-R&H"
    result = _analyze_sample(release, _fixture("live_qui_stream_wrapper.json"))
    traits = result["mediainfo_files"][0]["traits"]

    assert {"Dolby Vision", "HDR10"}.issubset(set(traits["hdr_formats"]))
    assert traits["audio_format"] == "DD+"
    assert "Subtitles" in traits["subtitle_tags"]
    assert not any(issue["severity"] == "ERROR" for issue in result["issues"])


def test_release_title_atmos_does_not_fake_mediainfo_atmos_metadata():
    release = "Pirates.2003.2160p.UHD.BluRay.ATMOS.DV.x265-W4NK3R"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "4"},
            {
                "@type": "Video",
                "Format": "HEVC",
                "Title": release,
                "Width": "3840",
                "Height": "2160",
                "BitDepth": "10",
                "HDR_Format": "Dolby Vision",
            },
            {"@type": "Audio", "Format": "TrueHD", "Channels": "8", "Default": "Yes"},
            {"@type": "Text", "Format": "PGS", "Language": "en"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert "Atmos" not in traits["audio_objects"]
    assert any(issue["key"] == "audio_object_missing" for issue in result["issues"])
    assert not any(issue["key"] == "audio_codec_mismatch" for issue in result["issues"])


def test_real_shape_item_2728_accepts_dv_profile_5_without_hdr10_fallback():
    release = "Dutton.Ranch.S01E04.DV.2160p.WEB.h265-GRACE"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "1"},
            {
                "@type": "Video",
                "Format": "HEVC",
                "Width": "3832",
                "Height": "1920",
                "BitDepth": "10",
                "HDR_Format": "Dolby Vision",
                "HDR_Format_Profile": "dvhe.05",
                "HDR_Format_Settings": "BL+RPU",
            },
            {
                "@type": "Audio",
                "Format": "E-AC-3",
                "Format_Commercial_IfAny": "Dolby Digital Plus with Dolby Atmos",
                "Channels": "6",
                "Language": "en",
                "Default": "Yes",
            },
            {"@type": "Text", "Format": "UTF-8", "Language": "en"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert traits["hdr_formats"] == ["Dolby Vision"]
    assert traits["dv_profile"] == "DV P5"
    assert not any(issue["key"] in {"dolby_vision_missing", "dv_without_hdr10"} for issue in result["issues"])


def test_real_shape_item_2720_detects_pgs_subtitles_and_dts_hd_ma():
    release = "Free.State.of.Jones.2016.1080p.BluRay.DTS-HD.MA5.1.x264-iFT"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "3"},
            {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1040", "BitDepth": "8"},
            {
                "@type": "Audio",
                "Format": "DTS XLL",
                "Format_Commercial_IfAny": "DTS-HD Master Audio",
                "Channels": "6",
                "Default": "Yes",
            },
            {"@type": "Text", "Format": "PGS", "Language": "en", "Default": "Yes"},
            {"@type": "Text", "Format": "PGS", "Language": "es"},
            {"@type": "Text", "Format": "PGS", "Language": "fr"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert traits["audio_format"] == "DTS-HD MA"
    assert "Subtitles" in traits["subtitle_tags"]
    assert "Default Subs" in traits["subtitle_tags"]
    assert not any(issue["key"] in {"no_subtitles", "audio_codec_mismatch"} for issue in result["issues"])


def test_mediainfo_audio_track_title_does_not_promote_lossy_dts():
    release = "True.Story.2015.BluRay.1080p.DTS.x264-iFT"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "3"},
            {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1040", "BitDepth": "8"},
            {
                "@type": "Audio",
                "Format": "DTS",
                "Channels": "6",
                "Title": "English DTSHD-MA Core",
                "Language": "en",
                "Default": "Yes",
            },
            {"@type": "Text", "Format": "PGS", "Language": "en"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert traits["audio_format"] == "DTS"
    assert not any(issue["key"] == "audio_codec_mismatch" for issue in result["issues"])


def test_mediainfo_seven_channel_lfe_layout_is_6_1():
    release = "Movie.2024.1080p.BluRay.DTS-HD.MA.6.1.x264-GRP"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "3"},
            {"@type": "Video", "Format": "AVC", "Width": "1920", "Height": "1040", "BitDepth": "8"},
            {
                "@type": "Audio",
                "Format": "DTS XLL",
                "Format_Commercial_IfAny": "DTS-HD Master Audio",
                "Channels": "7",
                "ChannelLayout": "C L R Ls Rs LFE Cs",
                "Language": "en",
                "Default": "Yes",
            },
            {"@type": "Text", "Format": "PGS", "Language": "en"},
        ],
    )

    result = _analyze_sample(release, payload)
    traits = result["mediainfo_files"][0]["traits"]

    assert traits["audio_channels"] == 6.1
    assert not any(issue["key"] == "audio_channels_mismatch" for issue in result["issues"])


def test_real_shape_item_85_detects_truehd_atmos_from_title():
    release = "The.Rocky.Horror.Picture.Show.1975.Extended.2160p.BluRay.TrueHD.Atmos.7.1.DV.HDR10.x265-Softboat"
    payload = _payload(
        release,
        [
            {"@type": "General", "TextCount": "53"},
            {
                "@type": "Video",
                "Format": "HEVC",
                "Title": "Dolby Vision Profile 8.1",
                "Width": "3584",
                "Height": "2160",
                "BitDepth": "10",
                "HDR_Format": "Dolby Vision",
                "HDR_Format_Compatibility": "HDR10",
                "HDR_Format_Profile": "dvhe.08",
                "HDR_Format_Settings": "BL+RPU",
            },
            {
                "@type": "Audio",
                "Format": "TrueHD",
                "CodecID": "A_TRUEHD",
                "Channels": "8",
                "ChannelLayout": "C L R Ls Rs Lb Rb LFE",
                "Title": "Dolby Atmos 7.1 Remix",
                "Language": "en",
                "Default": "Yes",
            },
            {"@type": "Text", "Format": "UTF-8", "Language": "en"},
        ],
    )

    traits = traits_payload(traits_from_mediainfo(payload))
    result = _analyze_sample(release, payload)

    assert traits["audio_format"] == "TrueHD Atmos"
    assert "Atmos" in traits["audio_objects"]
    assert not any(issue["key"] in {"audio_object_missing", "atmos_title_without_metadata"} for issue in result["issues"])


def _payload(release: str, tracks):
    return {
        "media": {
            "@ref": f"/media/torrents/{release}/{release}.mkv",
            "track": tracks,
        }
    }


def _fixture(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def _analyze_sample(release: str, payload):
    return analyze_media_payloads(
        release_title=release,
        media_files=[{"index": 0, "name": f"{release}/{release}.mkv", "size": 1000}],
        mediainfo_payloads=[payload],
    )
