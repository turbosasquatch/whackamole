from app.reducer import reduce_ua_log


def test_reducer_marks_passed_trackers_as_candidate():
    result = reduce_ua_log("Trackers passed all checks: ABC, XYZ")

    assert result.status == "candidate"
    assert result.verdict == "candidate"
    assert result.trackers == ["ABC", "XYZ"]


def test_reducer_blocks_potential_dupes():
    result = reduce_ua_log("Found potential dupes on: ABC, XYZ")

    assert result.status == "blocked"
    assert result.verdict == "dupe"
    assert result.trackers == ["ABC", "XYZ"]


def test_reducer_marks_error_logs():
    result = reduce_ua_log("Traceback: unauthorized")

    assert result.status == "error"
    assert result.verdict == "error"
