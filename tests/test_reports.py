from app.database import Database


def test_report_database_tracks_attempted_state_and_counts(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    report_id = db.create_report(1, "Example.Item", "MediaInfo", "Audio tags look wrong")

    active = db.list_reports()
    counts = db.report_counts()

    assert active[0]["id"] == report_id
    assert active[0]["state"] == "active"
    assert counts["active"] == 1
    assert counts["attempted"] == 0
    assert counts["open"] == 1

    assert db.mark_report_attempted(report_id) is True
    attempted = db.list_reports(state="attempted")
    counts = db.report_counts()

    assert attempted[0]["id"] == report_id
    assert attempted[0]["state"] == "attempted"
    assert counts["active"] == 0
    assert counts["attempted"] == 1
    assert counts["open"] == 1

    assert db.resolve_report(report_id) is True
    resolved = db.list_reports(state="resolved")
    counts = db.report_counts()

    assert resolved[0]["id"] == report_id
    assert resolved[0]["state"] == "resolved"
    assert counts["resolved"] == 1
    assert counts["open"] == 0


def test_report_database_marks_duplicate_batch_attempted(tmp_path):
    db = Database(str(tmp_path / "whackamole.db"))
    first = db.create_report(1, "Example.One", "MediaInfo", "Audio tags look wrong")
    second = db.create_report(2, "Example.Two", "MediaInfo", "Audio tags look wrong")
    deleted = db.create_report(3, "Example.Three", "MediaInfo", "Audio tags look wrong")
    db.delete_report(deleted)

    assert db.mark_reports_attempted([first, second, deleted]) == 2
    attempted_ids = [row["id"] for row in db.list_reports(state="attempted")]

    assert attempted_ids == [second, first]
    assert db.report_counts()["attempted"] == 2
