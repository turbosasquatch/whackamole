import asyncio

from app.arr_compare import (
    _season_appears_fully_released,
    canonical_tracker,
    compare_item_with_arr,
    evaluate_tracker_decisions,
    parse_media_identity,
    parse_release_traits,
    release_is_equal_or_better,
    summarize_decisions,
)


def test_tracker_aliases_match_ua_and_prowlarr_names():
    assert canonical_tracker("IHD") == "IHD"
    assert canonical_tracker("InfinityHD (API) (Prowlarr)") == "IHD"
    assert canonical_tracker("Darkpeers (API) (Prowlarr)") == "DP"
    assert canonical_tracker("upload.cx (API) (Prowlarr)") == "ULCX"
    assert canonical_tracker("UnknownTracker") is None


def test_release_traits_parse_resolution_source_hdr_audio_and_pack():
    traits = parse_release_traits("Love.Island.All.Stars.S03.1080p.AMZN.WEB-DL.DDP2.0.H.264-Kitsune")

    assert traits.resolution == "1080p"
    assert traits.scan_type == "progressive"
    assert traits.source == "web"
    assert traits.hdr_rank == 0
    assert traits.audio_format == "DD+"
    assert traits.audio_channels == 2.0
    assert traits.codec == "AVC"
    assert traits.season == 3
    assert traits.episode is None
    assert traits.season_pack


def test_release_traits_parse_hdr10plus_and_truehd_audio():
    traits = parse_release_traits("Heretic.2024.2160p.BluRay.TrueHD.Atmos.7.1.DV.HDR10Plus.x265-MainFrame")

    assert traits.resolution == "2160p"
    assert traits.source == "bluray_encode"
    assert traits.hdr_rank == 4
    assert traits.hdr_label == "DV/HDR fallback"
    assert traits.audio_format == "TrueHD Atmos"
    assert traits.audio_channels == 7.1
    assert traits.codec == "HEVC"


def test_release_traits_parse_1080i_bluray_remux_dts_hd_ma():
    traits = parse_release_traits("Maxine.S01.BluRay.1080i.DTS-HD.MA.5.1.AVC.REMUX")

    assert traits.resolution == "1080i"
    assert traits.scan_type == "interlaced"
    assert traits.source == "bluray_remux"
    assert traits.audio_format == "DTS-HD MA"
    assert traits.audio_format_rank > 0
    assert traits.audio_channels == 5.1
    assert traits.codec == "AVC"
    assert traits.season == 1
    assert traits.episode is None
    assert traits.season_pack


def test_audio_format_ranking_uses_trash_style_order():
    truehd_atmos = parse_release_traits("Movie.2024.1080p.BluRay.TrueHD.Atmos.7.1.H.264-GRP")
    dtsx = parse_release_traits("Movie.2024.1080p.BluRay.DTS-X.7.1.H.264-GRP")
    ddp_atmos = parse_release_traits("Movie.2024.1080p.WEB-DL.DDP.Atmos.5.1.H.264-GRP")
    dts_hd_ma = parse_release_traits("Movie.2024.1080p.BluRay.DTS-HD.MA.5.1.H.264-GRP")
    aac = parse_release_traits("Movie.2024.1080p.WEB-DL.AAC.2.0.H.264-GRP")
    dd = parse_release_traits("Movie.2024.1080p.WEB-DL.DD.2.0.H.264-GRP")
    opus = parse_release_traits("Movie.2024.1080p.WEB-DL.Opus.2.0.H.264-GRP")

    assert truehd_atmos.audio_format_rank > dtsx.audio_format_rank
    assert dtsx.audio_format_rank > ddp_atmos.audio_format_rank
    assert ddp_atmos.audio_format_rank > dts_hd_ma.audio_format_rank
    assert dts_hd_ma.audio_format_rank > aac.audio_format_rank
    assert aac.audio_format_rank > dd.audio_format_rank
    assert dd.audio_format_rank > opus.audio_format_rank


def test_hdr_formats_parse_distinct_labels():
    fallback = parse_release_traits("Movie.2024.2160p.WEB-DL.DV.HDR10Plus.DDP5.1.H.265-GRP")
    dv_only = parse_release_traits("Movie.2024.2160p.WEB-DL.DV.DDP5.1.H.265-GRP")
    hdr10plus = parse_release_traits("Movie.2024.2160p.WEB-DL.HDR10P.DDP5.1.H.265-GRP")
    hdr = parse_release_traits("Movie.2024.2160p.WEB-DL.HDR.DDP5.1.H.265-GRP")
    sdr = parse_release_traits("Movie.2024.2160p.WEB-DL.DDP5.1.H.265-GRP")

    assert [fallback.hdr_label, dv_only.hdr_label, hdr10plus.hdr_label, hdr.hdr_label, sdr.hdr_label] == [
        "DV/HDR fallback",
        "DV only",
        "HDR10+",
        "HDR",
        "SDR",
    ]
    assert fallback.hdr_rank > dv_only.hdr_rank > hdr10plus.hdr_rank > hdr.hdr_rank > sdr.hdr_rank


def test_movie_versions_parse_stable_variety_set():
    traits = parse_release_traits(
        "Movie.2024.2160p.BluRay.4K.Remaster.IMAX.Enhanced.Open.Matte.TrueHD.7.1.H.265-GRP"
    )

    assert traits.movie_versions == ("4K Remaster", "IMAX Enhanced", "Open Matte")


def test_release_comparison_keeps_resolution_lanes_separate():
    local = parse_release_traits("Movie.2024.1080p.WEB-DL.DDP5.1.H.264-GRP")
    remote = parse_release_traits("Movie.2024.2160p.WEB-DL.DDP5.1.HDR.H.265-GRP")

    assert not release_is_equal_or_better(local, remote)


def test_release_comparison_treats_hdr_and_audio_as_upgrades():
    local = parse_release_traits("Movie.2024.1080p.WEB-DL.DDP5.1.HDR.H.265-GRP")
    remote_sdr = parse_release_traits("Movie.2024.1080p.WEB-DL.DDP5.1.H.264-GRP")
    remote_stereo = parse_release_traits("Movie.2024.1080p.WEB-DL.DDP2.0.HDR.H.265-GRP")
    remote_better = parse_release_traits("Movie.2024.1080p.WEB-DL.DDP7.1.DV.H.265-GRP")

    assert not release_is_equal_or_better(local, remote_sdr)
    assert not release_is_equal_or_better(local, remote_stereo)
    assert release_is_equal_or_better(local, remote_better)


def test_release_comparison_treats_1080p_as_better_than_1080i():
    local_1080i = parse_release_traits("Movie.2024.1080i.BluRay.DTS-HD.MA.5.1.AVC.REMUX-GRP")
    remote_1080p = parse_release_traits("Movie.2024.1080p.BluRay.DTS-HD.MA.5.1.AVC.REMUX-GRP")

    assert release_is_equal_or_better(local_1080i, remote_1080p)
    assert not release_is_equal_or_better(remote_1080p, local_1080i)


def test_release_comparison_treats_audio_format_as_upgrade():
    local = parse_release_traits("Movie.2024.1080p.BluRay.DD.5.1.H.264-GRP")
    remote_better_format = parse_release_traits("Movie.2024.1080p.BluRay.DTS-HD.MA.5.1.H.264-GRP")
    remote_worse_format = parse_release_traits("Movie.2024.1080p.BluRay.AAC.5.1.H.264-GRP")

    assert release_is_equal_or_better(local, remote_better_format)
    assert not release_is_equal_or_better(remote_better_format, remote_worse_format)


def test_release_comparison_treats_movie_versions_as_variety_lanes():
    local_theatrical = parse_release_traits("Movie.2024.1080p.BluRay.Theatrical.Cut.TrueHD.5.1.H.264-GRP")
    remote_special = parse_release_traits("Movie.2024.1080p.BluRay.Special.Edition.TrueHD.5.1.H.264-GRP")
    remote_theatrical = parse_release_traits("Movie.2024.1080p.BluRay.Theatrical.Cut.TrueHD.5.1.H.264-OTHER")

    assert not release_is_equal_or_better(local_theatrical, remote_special)
    assert release_is_equal_or_better(local_theatrical, remote_theatrical)


def test_release_comparison_treats_hybrid_as_same_lane_and_shared_hdr10plus_as_same_class():
    local = parse_release_traits("Tom.Clancys.Jack.Ryan.S04.2160p.AMZN.WEB-DL.Hybrid.H265.DV.HDR10Plus.DDP.Atmos.5.1-HONE")
    remote = parse_release_traits("Tom.Clancys.Jack.Ryan.S04.2160p.AMZN.WEB-DL.DDP5.1.Atmos.HDR10Plus.H.265-NTb")

    assert release_is_equal_or_better(local, remote)


def test_tracker_decisions_choose_better_matching_audio_when_remote_hdr_is_unknown():
    local = parse_release_traits("Greenland.2.Migration.2026.2160p.WebRip.Atmos.EAC3.5.1.HDR10Plus.x265-Lootera")
    releases = [
        {
            "protocol": "torrent",
            "indexer": "Darkpeers (API) (Prowlarr)",
            "title": "Greenland.2.Migration.2026.HDR.2160p.WEB.h265-ETHEL.mkv",
            "seeders": 30,
        },
        {
            "protocol": "torrent",
            "indexer": "Darkpeers (API) (Prowlarr)",
            "title": "Greenland.2.Migration.2026.REPACK.2160p.AMZN.WEB-DL.DDP5.1.Atmos.H.265-BYNDR.mkv",
            "seeders": 32,
        },
    ]

    decisions = evaluate_tracker_decisions(
        passed_trackers=["DP"],
        local_traits=local,
        releases=releases,
        configured_indexers=[{"name": "Darkpeers (API) (Prowlarr)", "protocol": "torrent"}],
    )

    assert decisions[0]["status"] == "blocked"
    assert decisions[0]["best_release"]["title"].endswith("BYNDR.mkv")


def test_season_pack_and_episode_results_stay_in_separate_scopes():
    local_pack = parse_release_traits("Show.S03.1080p.WEB-DL.DDP2.0.H.264-GRP")
    remote_episode = parse_release_traits("Show.S03E01.1080p.WEB-DL.DDP2.0.H.264-GRP")
    local_episode = parse_release_traits("Show.S03E01.1080p.WEB-DL.DDP2.0.H.264-GRP")
    remote_pack = parse_release_traits("Show.S03.1080p.WEB-DL.DDP2.0.H.264-GRP")

    assert not release_is_equal_or_better(local_pack, remote_episode)
    assert not release_is_equal_or_better(local_episode, remote_pack)


def test_tracker_decisions_only_show_matching_episode_results():
    local = parse_release_traits("Love.Overboard.S01E01.Walk.the.Plank.1080p.DSNP.WEB-DL.DD+5.1.H.264-GRP")
    releases = [
        {
            "protocol": "torrent",
            "indexer": "upload.cx (API) (Prowlarr)",
            "title": "Love.Overboard.S01E01.Walk.the.Plank.1080p.DSNP.WEB-DL.DD+5.1.H.264-playWEB.mkv",
        },
        {
            "protocol": "torrent",
            "indexer": "upload.cx (API) (Prowlarr)",
            "title": "Love.Overboard.S01E02.1080p.DSNP.WEB-DL.DD+5.1.H.264-playWEB.mkv",
        },
        {
            "protocol": "torrent",
            "indexer": "upload.cx (API) (Prowlarr)",
            "title": "Love.Overboard.S01.1080p.DSNP.WEB-DL.DD+5.1.H.264-playWEB",
        },
    ]

    decisions = evaluate_tracker_decisions(
        passed_trackers=["ULCX"],
        local_traits=local,
        releases=releases,
        configured_indexers=[{"name": "upload.cx (API) (Prowlarr)", "protocol": "torrent"}],
    )

    assert decisions[0]["status"] == "blocked"
    assert decisions[0]["same_lane_count"] == 1
    assert [item["title"] for item in decisions[0]["results"]] == [releases[0]["title"]]


def test_tracker_decisions_only_show_matching_season_pack_results():
    local = parse_release_traits("The.Last.Frontier.S01.2160p.ATV.WEB-DL.Hybrid.H265.DV.HDR10Plus.DDP.Atmos.5.1-HONE")
    releases = [
        {
            "protocol": "torrent",
            "indexer": "Darkpeers (API) (Prowlarr)",
            "title": "The.Last.Frontier.S01E01.2160p.ATV.WEB-DL.H265.DV.HDR10Plus.DDP.Atmos.5.1-GRP",
        },
        {
            "protocol": "torrent",
            "indexer": "Darkpeers (API) (Prowlarr)",
            "title": "The.Last.Frontier.S01.2160p.ATV.WEB-DL.H265.DV.HDR10Plus.DDP.Atmos.5.1-GRP",
        },
    ]

    decisions = evaluate_tracker_decisions(
        passed_trackers=["DP"],
        local_traits=local,
        releases=releases,
        configured_indexers=[{"name": "Darkpeers (API) (Prowlarr)", "protocol": "torrent"}],
    )

    assert decisions[0]["status"] == "blocked"
    assert [item["title"] for item in decisions[0]["results"]] == [releases[1]["title"]]


def test_season_appears_fully_released_when_all_monitored_episodes_are_out():
    episodes = [
        {"seasonNumber": 1, "episodeNumber": 1, "monitored": True, "airDateUtc": "2026-01-01T00:00:00Z"},
        {"seasonNumber": 1, "episodeNumber": 2, "monitored": True, "hasFile": True},
        {"seasonNumber": 1, "episodeNumber": 3, "monitored": False, "airDateUtc": "2099-01-01T00:00:00Z"},
    ]

    assert _season_appears_fully_released(episodes, 1)


def test_season_does_not_appear_fully_released_when_a_monitored_episode_is_future():
    episodes = [
        {"seasonNumber": 1, "episodeNumber": 1, "monitored": True, "airDateUtc": "2026-01-01T00:00:00Z"},
        {"seasonNumber": 1, "episodeNumber": 2, "monitored": True, "airDateUtc": "2099-01-01T00:00:00Z"},
    ]

    assert not _season_appears_fully_released(episodes, 1)


def test_tracker_decisions_filter_usenet_and_block_equal_torrent():
    local = parse_release_traits("Heretic.2024.2160p.BluRay.TrueHD.Atmos.7.1.DV.HDR10Plus.x265-MainFrame")
    releases = [
        {
            "protocol": "usenet",
            "indexer": "NinjaCentral (Prowlarr)",
            "title": "Heretic.2024.2160p.BluRay.TrueHD.Atmos.7.1.DV.HDR10Plus.x265-MainFrame",
        },
        {
            "protocol": "torrent",
            "indexer": "upload.cx (API) (Prowlarr)",
            "title": "Heretic.2024.2160p.BluRay.TrueHD.Atmos.7.1.DV.HDR10Plus.x265-MainFrame.mkv",
            "quality": {"quality": {"name": "Bluray-2160p"}},
            "seeders": 4,
        },
    ]

    decisions = evaluate_tracker_decisions(
        passed_trackers=["ULCX"],
        local_traits=local,
        releases=releases,
        configured_indexers=[{"name": "upload.cx (API) (Prowlarr)", "protocol": "torrent"}],
    )

    assert decisions[0]["status"] == "blocked"
    assert decisions[0]["best_release"]["title"].startswith("Heretic")


def test_tracker_decisions_candidate_when_only_lower_audio_exists():
    local = parse_release_traits("Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP")
    releases = [
        {
            "protocol": "torrent",
            "indexer": "Darkpeers (API) (Prowlarr)",
            "title": "Show.S01E01.1080p.WEB-DL.DDP2.0.H.264-OTHER",
        }
    ]

    decisions = evaluate_tracker_decisions(
        passed_trackers=["DP"],
        local_traits=local,
        releases=releases,
        configured_indexers=[{"name": "Darkpeers (API) (Prowlarr)", "protocol": "torrent"}],
    )

    assert decisions[0]["status"] == "candidate"
    assert summarize_decisions(decisions)[0] == "candidate"


def test_tracker_decisions_block_near_identical_alias_match_and_keep_results():
    local = parse_release_traits("Movie.2024.2160p.WEB-DL.DD+5.1.DoVi.H265-GRP")
    releases = [
        {
            "protocol": "torrent",
            "indexer": "upload.cx (API) (Prowlarr)",
            "title": "Movie.2024.2160p.WEB-DL.DDP5.1.DV.H.265-OTHER",
            "seeders": 8,
        }
    ]

    decisions = evaluate_tracker_decisions(
        passed_trackers=["ULCX"],
        local_traits=local,
        releases=releases,
        configured_indexers=[{"name": "upload.cx (API) (Prowlarr)", "protocol": "torrent"}],
    )

    assert decisions[0]["status"] == "blocked"
    assert decisions[0]["same_lane_count"] == 1
    assert decisions[0]["results"][0]["traits"]["audio_format"] == "DD+"
    assert decisions[0]["results"][0]["traits"]["codec"] == "HEVC"


def test_tracker_decisions_block_truehd_dovi_h265_alias_match():
    local = parse_release_traits("Movie.2024.2160p.BluRay.REMUX.TruHD.7.1.DoVi.H265-GRP")
    releases = [
        {
            "protocol": "torrent",
            "indexer": "Darkpeers (API) (Prowlarr)",
            "title": "Movie.2024.2160p.BluRay.REMUX.TrueHD.7.1.DV.HEVC-OTHER",
            "seeders": 2,
        }
    ]

    decisions = evaluate_tracker_decisions(
        passed_trackers=["DP"],
        local_traits=local,
        releases=releases,
        configured_indexers=[{"name": "Darkpeers (API) (Prowlarr)", "protocol": "torrent"}],
    )

    assert decisions[0]["status"] == "blocked"
    assert decisions[0]["best_release"]["traits"]["audio_format"] == "TrueHD"


def test_tracker_decisions_unknown_alias_manual_review():
    local = parse_release_traits("Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP")

    decisions = evaluate_tracker_decisions(
        passed_trackers=["MYSTERY"],
        local_traits=local,
        releases=[],
        configured_indexers=[],
    )

    assert decisions[0]["status"] == "manual_review"
    assert summarize_decisions(decisions)[0] == "manual_review"


def test_parse_media_identity_uses_ua_ids():
    log = """
    Title: Love Island: All Stars (2024)
    Category: TV
    TMDB: https://www.themoviedb.org/tv/243754
    TVDB: https://www.thetvdb.com/?id=444348&tab=series
    IMDB: https://www.imdb.com/title/tt28959685
    """

    identity = parse_media_identity(log, "Love.Island.All.Stars.S03.1080p.AMZN.WEB-DL.DDP2.0.H.264-Kitsune")

    assert identity.kind == "sonarr"
    assert identity.tvdb_id == 444348
    assert identity.tmdb_id == 243754
    assert identity.season == 3
    assert identity.episode is None


def test_compare_item_with_arr_uses_precomputed_local_traits():
    local_traits = parse_release_traits("Show.Name.S03E04.1080p.WEB-DL.DDP5.1.H.264-GRP")

    result = asyncio.run(
        compare_item_with_arr(
            item_name="Unparseable.Release",
            ua_log="Category: TV",
            passed_trackers=[],
            cfg=None,
            secrets=None,
            local_traits=local_traits,
        )
    )

    assert result["status"] == "skipped"
    assert result["local_traits"]["season"] == 3
    assert result["local_traits"]["episode"] == 4
    assert result["local_traits"]["audio_format"] == "DD+"
