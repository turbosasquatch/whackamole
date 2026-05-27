from app.main import _tracker_result_groups, _tracker_summary
from app.tracker_links import tracker_links


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


def test_known_tracker_links_are_safe_convenience_links():
    links = tracker_links("IHD")

    assert links[0]["url"] == "https://infinityhd.net"
    assert {link["label"] for link in links} == {"Open", "Browse", "Upload"}
