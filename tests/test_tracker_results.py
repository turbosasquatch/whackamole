from app.main import _arr_summary, _tracker_result_groups, _tracker_summary


def test_tracker_result_groups_support_new_shape():
    groups = _tracker_result_groups('{"passed":["IHD"],"dupe":["DP"],"skipped":["ULCX"],"error":[]}')

    assert groups["passed"] == ["IHD"]
    assert groups["dupe"] == ["DP"]
    assert groups["skipped"] == ["ULCX"]
    assert groups["error"] == []


def test_tracker_result_groups_support_legacy_list():
    groups = _tracker_result_groups('["IHD", "DP"]', "dupe")

    assert groups["passed"] == []
    assert groups["dupe"] == ["IHD", "DP"]


def test_tracker_summary_labels_passed_as_upload_worthy():
    summary = _tracker_summary({"passed": ["IHD", "DP"], "dupe": [], "skipped": [], "error": []})

    assert summary == "Missing/upload-worthy: IHD, DP"


def test_tracker_summary_labels_covered_trackers():
    summary = _tracker_summary({"passed": [], "covered": ["IHD"], "dupe": [], "skipped": [], "error": []})

    assert summary == "Covered in QUI: IHD"


def test_arr_summary_labels_policy_blocked_decisions():
    summary = _arr_summary(
        {
            "decisions": [
                {"tracker": "DP", "status": "blocked", "reason": "GRP is banned on DP.", "banned_match": "GRP"},
                {"tracker": "IHD", "status": "candidate", "reason": "ok"},
            ]
        }
    )

    assert summary == "Valid: IHD | Policy blocked: DP"
