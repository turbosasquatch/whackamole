from pathlib import Path

from app.rename_display import build_rename_check


def test_mobile_rename_css_stacks_cells_full_width():
    css = Path("app/static/style.css").read_text()
    mobile_block = css.split("@media (max-width: 860px)")[-1]

    assert ".rename-table td" in mobile_block
    assert "display: block;" in mobile_block
    assert "width: 100% !important;" in mobile_block
    assert ".rename-table td::before" in mobile_block


def test_rename_display_explains_arr_title_mismatch_with_diff_and_tracker():
    local = "Example.Show.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-HONE"
    remote = "Example.Series.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-HONE"

    result = build_rename_check(
        {
            "status": "manual_review",
            "confidence": "high",
            "reason": "Arr found a same-group release on IHD in the same scope with a different release title.",
            "evidence": [
                {
                    "kind": "same_group_arr_title_mismatch",
                    "scope": "arr_title",
                    "confidence": "high",
                    "source": "Discovarr",
                    "tracker": "IHD",
                    "local_title": local,
                    "remote_title": remote,
                    "release_group": "HONE",
                    "local_key": "exampleshows01e011080pamznwebdlddp51h264hone",
                    "remote_key": "exampleseriess01e011080pamznwebdlddp51h264hone",
                    "local_scope": {"season": 1, "episode": 1, "resolution": "1080p"},
                    "remote_scope": {"season": 1, "episode": 1, "resolution": "1080p"},
                    "reason": "Arr found a same-group release on IHD in the same scope with a different release title.",
                }
            ],
        }
    )

    row = result["rows"][0]
    assert result["summary_counts"]["high"] == 1
    assert row["tracker"] == "IHD"
    assert row["local_value"] == local
    assert row["remote_value"] == remote
    assert "on IHD" in row["difference_summary"]
    assert any(segment["type"] == "replace" for segment in row["diff"]["local"])
    assert any(chip["token"] == "Show" and chip["side"] == "local" for chip in row["token_chips"])
    assert any(item["label"] == "Tracker" and item["value"] == "IHD" for item in row["meta"])


def test_rename_display_expands_srrdb_pairs_with_sizes():
    result = build_rename_check(
        {
            "status": "manual_review",
            "confidence": "high",
            "reason": "srrDB archived filename mismatch.",
            "evidence": [
                {
                    "kind": "srrdb_mismatch",
                    "scope": "srrdb",
                    "confidence": "high",
                    "source": "srrDB",
                    "queried_name": "The.Panic.in.Needle.Park.1971.1080p.BluRay.X264-AMIABLE",
                    "comparison_pairs": [
                        {
                            "local_name": "The Panic in Needle Park 1971 1080p BluRay X264-AMIABLE.mkv",
                            "archived_name": "The.Panic.in.Needle.Park.1971.1080p.BluRay.X264-AMIABLE.mkv",
                            "local_size": 1000,
                            "archived_size": 2000,
                            "status": "size_mismatch",
                        }
                    ],
                    "reason": "srrDB archived file size mismatch.",
                }
            ],
        }
    )

    row = result["rows"][0]
    assert row["source"] == "srrDB"
    assert row["local_value"].startswith("The Panic")
    assert row["remote_value"].startswith("The.Panic")
    assert "size differs" in row["difference_summary"]
    assert any(item["label"] == "Local size" and item["value"] == "1000 B" for item in row["meta"])
    assert any(item["label"] == "Archived size" and item["value"] == "2.0 KiB" for item in row["meta"])


def test_rename_display_highlights_empty_title_token_separator():
    result = build_rename_check(
        {
            "status": "manual_review",
            "confidence": "high",
            "reason": "Empty token.",
            "evidence": [
                {
                    "kind": "empty_title_token",
                    "scope": "file",
                    "confidence": "high",
                    "source": "video_file",
                    "value": "Example.Show.S01E01.The..Episode.1080p-GRP.mkv",
                    "reason": "Filename contains an empty title token.",
                }
            ],
        }
    )

    row = result["rows"][0]
    assert row["difference_summary"] == "Filename has an empty title slot caused by adjacent separators."
    assert row["problem"]["summary"] == "Doubled or mixed separator"
    assert row["problem"]["suggested_value"] == "Example.Show.S01E01.The.Episode.1080p-GRP.mkv"
    assert row["problem"]["locations"][0]["found"] == ".."
    assert row["problem"]["locations"][0]["replacement"] == "."
    assert any(segment["type"] == "replace" and segment["text"] == ".." for segment in row["diff"]["local"])


def test_rename_display_explains_mixed_empty_title_separator():
    value = "Example Show - Episode 1080p-GRP.mkv"
    result = build_rename_check(
        {
            "status": "manual_review",
            "confidence": "high",
            "reason": "Empty token.",
            "evidence": [
                {
                    "kind": "empty_title_token",
                    "scope": "file",
                    "confidence": "high",
                    "source": "video_file",
                    "value": value,
                    "reason": "Filename contains an empty title token.",
                }
            ],
        }
    )

    row = result["rows"][0]
    assert row["problem"]["locations"][0]["found"] == " - "
    assert row["problem"]["locations"][0]["found_label"] == "space + hyphen + space"
    assert row["problem"]["locations"][0]["before"] == "Show"
    assert row["problem"]["locations"][0]["after"] == "Episode"
    assert row["problem"]["suggested_value"] == "Example Show-Episode 1080p-GRP.mkv"
    assert row["files_open"] is True
    assert any(segment["type"] == "replace" and segment["text"] == " - " for segment in row["diff"]["local"])


def test_rename_display_lists_mixed_release_group_files():
    result = build_rename_check(
        {
            "status": "manual_review",
            "confidence": "high",
            "reason": "Mixed groups.",
            "evidence": [
                {
                    "kind": "mixed_release_groups",
                    "scope": "siblings",
                    "confidence": "high",
                    "source": "video_files",
                    "value": "grp, other",
                    "expected": "GRP",
                    "groups": {"grp": ["Episode.1-GRP.mkv"], "other": ["Episode.2-OTHER.mkv"]},
                    "reason": "Video files in the same folder use mixed release groups.",
                }
            ],
        }
    )

    row = result["rows"][0]
    assert row["files"] == [
        {"label": "grp", "items": ["Episode.1-GRP.mkv"]},
        {"label": "other", "items": ["Episode.2-OTHER.mkv"]},
    ]


def test_rename_display_compares_release_groups_not_whole_filename():
    result = build_rename_check(
        {
            "status": "manual_review",
            "confidence": "high",
            "reason": "Release group mismatch.",
            "evidence": [
                {
                    "kind": "file_group_mismatch",
                    "scope": "file",
                    "confidence": "high",
                    "source": "video_file",
                    "value": "Example.Show.S01E01.1080p.WEB-DL-GRP.mkv",
                    "expected": "ROOT",
                    "filename": "Example.Show.S01E01.1080p.WEB-DL-GRP.mkv",
                    "file_group": "GRP",
                    "root_group": "ROOT",
                    "files": ["Example.Show.S01E01.1080p.WEB-DL-GRP.mkv"],
                    "reason": "Video filename release group differs from the folder/root.",
                }
            ],
        }
    )

    row = result["rows"][0]
    assert row["local_label"] == "Video release group"
    assert row["remote_label"] == "Folder/root release group"
    assert row["local_value"] == "GRP"
    assert row["remote_value"] == "ROOT"
    assert row["files"] == [{"label": "Files", "items": ["Example.Show.S01E01.1080p.WEB-DL-GRP.mkv"]}]
    assert any(item["label"] == "Filename" for item in row["meta"])


def test_rename_display_renders_legacy_evidence_best_effort():
    result = build_rename_check(
        {
            "status": "warning",
            "confidence": "medium",
            "reason": "Legacy flag.",
            "evidence": [
                {
                    "kind": "possible_renamed_release",
                    "scope": "legacy",
                    "confidence": "medium",
                    "source": "legacy",
                    "reason": "Arr found a same-group release with a different release title.",
                }
            ],
        }
    )

    row = result["rows"][0]
    assert row["kind_label"] == "Possible renamed release"
    assert row["source"] == "legacy"
    assert row["difference_summary"] == "Arr found a same-group release with a different release title."
