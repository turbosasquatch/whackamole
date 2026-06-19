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
