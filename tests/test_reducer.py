from app.reducer import reduce_ua_log
from app.ua_logs import normalize_ua_log


LOVE_ISLAND_HTML_LINE = (
    '{"type": "html_full", "data": "<pre style=\\"font-family:Menlo,consolas\\">'
    '<code style=\\"font-family:inherit\\">'
    '<span style=\\"color: #008000; text-decoration-color: #008000; font-weight: bold\\">'
    'Trackers passed all checks: </span>'
    '<span style=\\"color: #808000; text-decoration-color: #808000; font-weight: bold\\">'
    'IHD, DP, ULCX</span>\\n</code></pre>"}'
)


def test_reducer_marks_passed_trackers_as_candidate():
    result = reduce_ua_log("Trackers passed all checks: ABC, XYZ")

    assert result.status == "candidate"
    assert result.verdict == "candidate"
    assert result.trackers == ["ABC", "XYZ"]
    assert result.tracker_results["passed"] == ["ABC", "XYZ"]


def test_reducer_extracts_all_trackers_from_ua_html_output():
    result = reduce_ua_log(LOVE_ISLAND_HTML_LINE)

    assert result.status == "candidate"
    assert result.tracker_results["passed"] == ["IHD", "DP", "ULCX"]
    assert result.trackers == ["IHD", "DP", "ULCX"]


def test_reducer_blocks_potential_dupes():
    result = reduce_ua_log("Found potential dupes on: ABC, XYZ")

    assert result.status == "blocked"
    assert result.verdict == "dupe"
    assert result.trackers == ["ABC", "XYZ"]
    assert result.tracker_results["dupe"] == ["ABC", "XYZ"]


def test_reducer_extracts_dupes_from_ua_html_output():
    result = reduce_ua_log(
        '{"type":"html_full","data":"<span>Found potential dupes on: </span><span>IHD, ULCX.</span>"}'
    )

    assert result.status == "blocked"
    assert result.verdict == "dupe"
    assert result.tracker_results["dupe"] == ["IHD", "ULCX"]


def test_reducer_extracts_skipped_trackers_from_ua_html_output():
    result = reduce_ua_log(
        '{"type":"html_full","data":"<span>Skipped due to specific tracker conditions: </span><span>IHD, DP.</span>"}'
    )

    assert result.status == "blocked"
    assert result.verdict == "skipped"
    assert result.tracker_results["skipped"] == ["IHD", "DP"]


def test_reducer_marks_error_logs():
    result = reduce_ua_log("Traceback: unauthorized")

    assert result.status == "error"
    assert result.verdict == "error"
    assert result.tracker_results["error"] == ["UA"]


def test_ua_log_normalization_strips_sse_json_and_html():
    log = normalize_ua_log("data: " + LOVE_ISLAND_HTML_LINE + "\n" + '{"type":"keepalive"}')

    assert log == "Trackers passed all checks: IHD, DP, ULCX"
