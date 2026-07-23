from app.rename_detection import analyze_rename_detection


def test_rename_detection_reviews_empty_episode_title_token_without_srrdb():
    root = "Trophy.Wife.Murder.on.Safari.S01.1080p.HULU.WEB-DL.DD+5.1.H.264-playWEB"
    files = [
        {"index": 0, "name": f"{root}/Trophy.Wife.Murder.on.Safari.S01E01.Jekyll..Hyde.1080p.HULU.WEB-DL.DD+5.1.H.264-playWEB.mkv"},
        {"index": 1, "name": f"{root}/Trophy.Wife.Murder.on.Safari.S01E02.Something.Fishy.1080p.HULU.WEB-DL.DD+5.1.H.264-playWEB.mkv"},
        {"index": 2, "name": f"{root}/Trophy.Wife.Murder.on.Safari.S01E03.Crocodile.Tears.1080p.HULU.WEB-DL.DD+5.1.H.264-playWEB.mkv"},
    ]

    result = analyze_rename_detection(
        item_name=root,
        media_result={"torrent_root": root, "video_files": files},
    )

    assert result["status"] == "manual_review"
    assert result["confidence"] == "high"
    assert any(item["kind"] == "empty_title_token" for item in result["evidence"])


def test_rename_detection_keeps_normal_episode_title_variation_as_pass():
    root = "Trophy.Wife.Murder.on.Safari.S01.1080p.HULU.WEB-DL.DD+5.1.H.264-playWEB"
    files = [
        {"index": 0, "name": f"{root}/Trophy.Wife.Murder.on.Safari.S01E01.Jekyll.and.Hyde.1080p.HULU.WEB-DL.DD+5.1.H.264-playWEB.mkv"},
        {"index": 1, "name": f"{root}/Trophy.Wife.Murder.on.Safari.S01E02.Something.Fishy.1080p.HULU.WEB-DL.DD+5.1.H.264-playWEB.mkv"},
        {"index": 2, "name": f"{root}/Trophy.Wife.Murder.on.Safari.S01E03.Crocodile.Tears.1080p.HULU.WEB-DL.DD+5.1.H.264-playWEB.mkv"},
    ]

    result = analyze_rename_detection(
        item_name=root,
        media_result={"torrent_root": root, "video_files": files},
    )

    assert result["status"] == "pass"


def test_rename_detection_keeps_folder_scene_normalization_as_low_confidence_pass():
    root = "American Crime Story S03 1080p AMZN WEB-DL DDP5 1 H 264-NTb"
    files = [
        {"index": 0, "name": f"{root}/American.Crime.Story.S03E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv"},
        {"index": 1, "name": f"{root}/American.Crime.Story.S03E02.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv"},
    ]

    result = analyze_rename_detection(item_name=root, media_result={"torrent_root": root, "video_files": files})

    assert result["status"] == "pass"
    assert result["confidence"] == "low"
    assert any(
        item["kind"] == "folder_scene_normalization" and item["confidence"] == "low"
        for item in result["evidence"]
    )


def test_rename_detection_reviews_random_basename_inside_structured_folder():
    root = "Example.Movie.2026.1080p.WEB-DL.DDP5.1.H.264-GRP"

    result = analyze_rename_detection(
        item_name=root,
        media_result={"torrent_root": root, "video_files": [{"index": 0, "name": f"{root}/3uz7j4imwRaC.mkv"}]},
    )

    assert result["status"] == "manual_review"
    assert any(item["kind"] == "random_video_basename" for item in result["evidence"])


def test_rename_detection_reviews_mixed_release_groups():
    root = "Example.Show.S01.1080p.WEB-DL.DDP5.1.H.264-GRP"
    files = [
        {"index": 0, "name": f"{root}/Example.Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP.mkv"},
        {"index": 1, "name": f"{root}/Example.Show.S01E02.1080p.WEB-DL.DDP5.1.H.264-OTHER.mkv"},
    ]

    result = analyze_rename_detection(item_name=root, media_result={"torrent_root": root, "video_files": files})

    assert result["status"] == "manual_review"
    assert any(item["kind"] in {"file_group_mismatch", "mixed_release_groups"} for item in result["evidence"])


def test_rename_detection_reviews_high_confidence_file_evidence_even_with_folder_normalization():
    root = "American Crime Story S03 1080p AMZN WEB-DL DDP5 1 H 264-NTb"
    files = [
        {"index": 0, "name": f"{root}/American.Crime.Story.S03E01.The..Episode.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv"},
        {"index": 1, "name": f"{root}/American.Crime.Story.S03E02.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv"},
    ]

    result = analyze_rename_detection(item_name=root, media_result={"torrent_root": root, "video_files": files})

    assert result["status"] == "manual_review"
    assert any(item["kind"] == "folder_scene_normalization" and item["confidence"] == "low" for item in result["evidence"])
    assert any(item["kind"] == "empty_title_token" and item["confidence"] == "high" for item in result["evidence"])


def test_rename_detection_suppresses_weak_local_warning_when_srrdb_verifies():
    root = "American Crime Story S03 1080p AMZN WEB-DL DDP5 1 H 264-NTb"
    file_name = "American.Crime.Story.S03E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv"

    result = analyze_rename_detection(
        item_name=root,
        media_result={"torrent_root": root, "video_files": [{"index": 0, "name": f"{root}/{file_name}"}]},
        srrdb_result={"status": "verified", "local_video_files": [file_name], "proper_filenames": [file_name]},
    )

    assert result["status"] == "pass"
    assert [item["kind"] for item in result["evidence"]] == ["srrdb_verified"]


def test_rename_detection_explains_same_group_arr_title_mismatch():
    local = "Example.Show.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-HONE"
    remote = "Example.Series.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-HONE"

    result = analyze_rename_detection(
        item_name=local,
        media_result={"torrent_root": local, "video_files": [{"index": 0, "name": f"{local}/{local}.mkv"}]},
        arr_results={
            "decisions": [
                {
                    "tracker": "IHD",
                    "status": "candidate",
                    "best_release": {"title": remote, "quality": "1080p WEB-DL"},
                }
            ]
        },
    )

    assert result["status"] == "manual_review"
    evidence = next(item for item in result["evidence"] if item["kind"] == "same_group_arr_title_mismatch")
    assert evidence["tracker"] == "IHD"
    assert evidence["local_title"] == local
    assert evidence["remote_title"] == remote
    assert evidence["release_group"] == "HONE"
    assert evidence["local_key"] != evidence["remote_key"]
    assert evidence["local_scope"]["season"] == 1
    assert evidence["remote_scope"]["episode"] == 1


def test_rename_detection_ignores_arr_decision_without_best_release():
    local = "Example.Show.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-HONE"

    result = analyze_rename_detection(
        item_name=local,
        media_result={"torrent_root": local, "video_files": [{"index": 0, "name": f"{local}/{local}.mkv"}]},
        arr_results={"decisions": [{"tracker": "IHD", "status": "candidate"}]},
    )

    assert result["status"] == "pass"
    assert not any(item["kind"] == "same_group_arr_title_mismatch" for item in result["evidence"])


def test_rename_detection_reviews_placeholder_torrent_name_with_structured_mapped_content():
    content = "Im.Thinking.of.Ending.Things.2020.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.HDR10.H.265-BetterCallSaul"

    result = analyze_rename_detection(
        item_name="unpack",
        mapped_path=f"/media/torrents/movies/{content}",
        media_result={
            "torrent_root": "unpack",
            "video_files": [{"index": 0, "name": f"unpack/{content}.mkv"}],
        },
    )

    assert result["status"] == "manual_review"
    evidence = next(item for item in result["evidence"] if item["kind"] == "placeholder_torrent_name_mismatch")
    assert evidence["confidence"] == "high"
    assert evidence["value"] == "unpack"
    assert evidence["expected"] == content


def test_rename_detection_does_not_review_placeholder_without_structured_content_evidence():
    result = analyze_rename_detection(
        item_name="unpack",
        mapped_path="/media/torrents/movies/unpack",
        media_result={
            "torrent_root": "unpack",
            "video_files": [{"index": 0, "name": "unpack/unpack.mkv"}],
        },
    )

    assert result["status"] == "pass"
    assert not any(item["kind"] == "placeholder_torrent_name_mismatch" for item in result["evidence"])


def test_rename_detection_does_not_treat_legitimate_short_title_as_placeholder():
    release = "Up.2009.1080p.WEB-DL.H.264-GRP"

    result = analyze_rename_detection(
        item_name=release,
        mapped_path=f"/media/torrents/movies/{release}",
        media_result={
            "torrent_root": release,
            "video_files": [{"index": 0, "name": f"{release}/{release}.mkv"}],
        },
    )

    assert result["status"] == "pass"
    assert not any(item["kind"] == "placeholder_torrent_name_mismatch" for item in result["evidence"])


def test_rename_detection_reviews_repeated_whitespace_in_structured_name():
    release = "The Mummy 2026 2160p UHD BluRay HDR10  DoVi TrueHD 7 1 Atmos x265-SPHD.mkv"

    result = analyze_rename_detection(item_name=release)

    evidence = next(item for item in result["evidence"] if item["kind"] == "repeated_name_separator")
    assert result["status"] == "manual_review"
    assert evidence["confidence"] == "high"
    assert evidence["expected"] == "The Mummy 2026 2160p UHD BluRay HDR10 DoVi TrueHD 7 1 Atmos x265-SPHD"


def test_rename_detection_keeps_normal_single_spacing_as_pass():
    release = "The Mummy 2026 2160p UHD BluRay HDR10 DoVi TrueHD 7 1 Atmos x265-SPHD.mkv"

    result = analyze_rename_detection(item_name=release)

    assert result["status"] == "pass"
    assert not any(item["kind"] == "repeated_name_separator" for item in result["evidence"])


def test_rename_detection_reviews_release_group_without_hyphen_separator():
    release = "Apollo.11.2019.2160p.UHD.BluRay.TrueHD.7.1.Atmos.HDR10P.x265.RandomBytes.mkv"

    result = analyze_rename_detection(
        item_name=release,
        media_result={"torrent_root": release, "video_files": [{"index": 0, "name": release}]},
    )

    evidence = next(item for item in result["evidence"] if item["kind"] == "missing_release_group_separator")
    assert result["status"] == "manual_review"
    assert evidence["confidence"] == "high"
    assert evidence["release_group"] == "RandomBytes"


def test_rename_detection_accepts_supported_release_group_separators():
    releases = [
        "Falling.Down.1993.2160p.UHD.BluRay.DTS-HD.MA.4.0.DV.HDR.x265-RandomBytes.mkv",
        "Example.Movie.2026.2160p.WEB-DL.DDP5.1.H.265- HONE.mkv",
        "A Complete Unknown (2024) (2160p iT WEB-DL Hybrid H265 DV HDR10+ DDP Atmos 5.1 English - HONE).mkv",
    ]

    for release in releases:
        result = analyze_rename_detection(
            item_name=release,
            media_result={"torrent_root": release, "video_files": [{"index": 0, "name": release}]},
        )

        assert result["status"] == "pass", release
        assert not any(item["kind"] == "missing_release_group_separator" for item in result["evidence"])


def test_rename_detection_suppresses_new_root_name_evidence_when_srrdb_verifies():
    releases = [
        "The Mummy 2026 2160p UHD BluRay HDR10  DoVi TrueHD 7 1 Atmos x265-SPHD.mkv",
        "Apollo.11.2019.2160p.UHD.BluRay.TrueHD.7.1.Atmos.HDR10P.x265.RandomBytes.mkv",
    ]

    for release in releases:
        result = analyze_rename_detection(
            item_name=release,
            media_result={"torrent_root": release, "video_files": [{"index": 0, "name": release}]},
            srrdb_result={"status": "verified", "local_video_files": [release], "proper_filenames": [release]},
        )

        assert result["status"] == "pass", release
        assert [item["kind"] for item in result["evidence"]] == ["srrdb_verified"]
