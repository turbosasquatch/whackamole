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

SHELTER_DUPE_LOG = """
Error during terminal reset: I/O operation on closed file
Gathering info for Shelter.2026.2160p.AMZN.WEB-DL.DDP5.1.HDR10P.H.265-STATHMAN.mkv
Removing trackers already in your client: IHD
Searching for existing torrents on: DP, ULCX...
Found potential dupes on: DP, ULCX.
Not enough successful trackers (0/1). No uploads being processed.
"""

WILD_AT_HEART_NO_VIDEO_LOG = """
Execute request - Path: /media/torrents/tv/Wild.At.Heart.S06.1080p.AMZN.WEB-DL.DD2.0.x264-NTb, Args: --site-check -ua -sda
Running in-process (rich-captured) mode
Gathering info for Wild.At.Heart.S06.1080p.AMZN.WEB-DL.DD2.0.x264-NTb
No Video files found
"""


def test_reducer_marks_passed_trackers_as_candidate():
    result = reduce_ua_log("Trackers passed all checks: ABC, XYZ")

    assert result.status == "candidate"
    assert result.verdict == "candidate"
    assert result.trackers == ["ABC", "XYZ"]
    assert result.tracker_results["passed"] == ["ABC", "XYZ"]


def test_reducer_keeps_clear_passed_decision_from_ua_log():
    result = reduce_ua_log("Trackers passed all checks: DP")

    assert result.status == "candidate"
    assert result.verdict == "candidate"
    assert result.tracker_results["passed"] == ["DP"]


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


def test_reducer_keeps_dupe_decision_with_terminal_reset_noise():
    result = reduce_ua_log(SHELTER_DUPE_LOG)

    assert result.status == "blocked"
    assert result.verdict == "dupe"
    assert result.tracker_results["dupe"] == ["DP", "ULCX"]
    assert result.tracker_results["error"] == []


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


def test_reducer_marks_no_video_files_as_error():
    result = reduce_ua_log(WILD_AT_HEART_NO_VIDEO_LOG)

    assert result.status == "error"
    assert result.verdict == "no_video_files"
    assert result.reason == (
        "UA could not find video files at the mapped path. Check the torrent path/mount or rerun after mover maintenance."
    )
    assert result.tracker_results["error"] == []


def test_reducer_marks_interrupted_ua_without_decision_as_retryable_error():
    result = reduce_ua_log(
        """
        Running in-process (rich-captured) mode
        Received SIGTERM, shutting down gracefully...
        Web UI server stopped
        Shutdown complete
        """
    )

    assert result.status == "error"
    assert result.verdict == "ua_interrupted"
    assert result.tracker_results["error"] == ["UA"]


def test_ua_log_normalization_strips_sse_json_and_html():
    log = normalize_ua_log("data: " + LOVE_ISLAND_HTML_LINE + "\n" + '{"type":"keepalive"}')

    assert log == "Trackers passed all checks: IHD, DP, ULCX"
