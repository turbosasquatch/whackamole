import httpx

from tools.rule_replay import fetch_reported_items, replay_item_payload


def test_replay_item_payload_reports_status_change_for_stored_miss():
    item = {
        "id": 3284,
        "name": "Blocked.Show.S01E01.1080p.WEB-DL-GRP",
        "hash": "hash-3284",
        "status": "blocked",
        "verdict": "no_tracker_passed",
        "reason": "No tracker passed UA checks.",
        "tracker_results": {"passed": [], "covered": [], "dupe": [], "skipped": [], "error": []},
        "checks": {
            "ua": {
                "status": "blocked",
                "verdict": "no_tracker_passed",
                "reason": "No tracker passed UA checks.",
            }
        },
    }
    reports = [{"id": 52, "stage": "Tracker Validation", "notes": "Should be skipped", "state": "active"}]

    replayed = replay_item_payload(item, reports)

    assert replayed["outcome"] == "status_changed"
    assert replayed["status_movement"] == "blocked -> skipped"
    assert replayed["replayed"]["rule"] == "ua.no_uploadable_trackers"
    assert replayed["reports"][0]["notes"] == "Should be skipped"


def test_replay_item_payload_reports_metadata_change_when_decision_payload_is_missing():
    item = {
        "id": 1054,
        "name": "Good.Boy.2025.BluRay.1080p.REMUX-FraMeSToR",
        "hash": "hash-1054",
        "status": "candidate",
        "verdict": "candidate",
        "reason": "Valid upload candidate on: IHD",
        "tracker_results": {"passed": ["IHD"], "covered": [], "dupe": [], "skipped": [], "error": []},
        "arr": {"status": "candidate", "decisions": [{"tracker": "IHD", "status": "candidate"}]},
        "checks": {
            "ua": {"status": "candidate", "verdict": "candidate", "reason": "UA says this is missing/upload-worthy on: IHD"},
            "arr": {"status": "candidate", "decisions": [{"tracker": "IHD", "status": "candidate"}]},
        },
    }

    replayed = replay_item_payload(item, [])

    assert replayed["outcome"] == "metadata_changed"
    assert replayed["metadata_changed"] is True
    assert replayed["replayed"]["status"] == "candidate"
    assert replayed["replayed"]["rule"] == "final.candidate"


def test_replay_item_payload_separates_decision_change_from_status_change():
    item = {
        "id": 1055,
        "name": "Blocked.Movie.2026.1080p.BluRay-GRP",
        "status": "blocked",
        "verdict": "old_policy",
        "reason": "Old block wording",
        "tracker_results": {"passed": ["IHD"], "covered": [], "dupe": [], "skipped": [], "error": []},
        "checks": {"flags": [{"key": "bloated_audio", "detail": "Audio is too large."}]},
    }

    replayed = replay_item_payload(item, [])

    assert replayed["outcome"] == "decision_changed"
    assert replayed["status_movement"] == ""
    assert replayed["replayed"]["rule"] == "media.hard_block"


def test_fetch_reported_items_pulls_open_reports_and_items():
    base_url = "http://whackamole.test"
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers.get("authorization")))
        if str(request.url) == f"{base_url}/api/reports?state=active&limit=500":
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {"id": 1, "item_id": 10, "stage": "UI", "notes": "Wrong lane", "state": "active"},
                        {"id": 2, "item_id": 10, "stage": "Rules", "notes": "Same item", "state": "active"},
                    ],
                    "state": "active",
                    "count": 2,
                },
            )
        if str(request.url) == f"{base_url}/api/reports?state=attempted&limit=500":
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {"id": 3, "item_id": 11, "stage": "Rules", "notes": "Attempted item", "state": "attempted"},
                    ],
                    "state": "attempted",
                    "count": 1,
                },
            )
        if str(request.url) == f"{base_url}/api/items/10":
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "name": "Example.Movie.2026.1080p.WEB-DL-GRP",
                    "status": "candidate",
                    "verdict": "candidate",
                    "reason": "Valid upload candidate on: DP",
                    "tracker_results": {"passed": ["DP"], "covered": [], "dupe": [], "skipped": [], "error": []},
                    "checks": {"arr": {"status": "candidate", "decisions": [{"tracker": "DP", "status": "candidate"}]}},
                },
            )
        if str(request.url) == f"{base_url}/api/items/11":
            return httpx.Response(
                200,
                json={
                    "id": 11,
                    "name": "Attempted.Movie.2026.1080p.WEB-DL-GRP",
                    "status": "candidate",
                    "verdict": "candidate",
                    "reason": "Valid upload candidate on: DP",
                    "tracker_results": {"passed": ["DP"], "covered": [], "dupe": [], "skipped": [], "error": []},
                    "checks": {"arr": {"status": "candidate", "decisions": [{"tracker": "DP", "status": "candidate"}]}},
                },
            )
        return httpx.Response(404, json={"detail": "not mocked"})

    transport = httpx.MockTransport(handler)
    report = fetch_reported_items(
        base_url=base_url,
        token="token",
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )

    assert seen == [
        ("GET", f"{base_url}/api/reports?state=active&limit=500", "Bearer token"),
        ("GET", f"{base_url}/api/reports?state=attempted&limit=500", "Bearer token"),
        ("GET", f"{base_url}/api/items/10", "Bearer token"),
        ("GET", f"{base_url}/api/items/11", "Bearer token"),
    ]
    assert report["report_state"] == "open"
    assert report["report_count"] == 3
    assert report["item_count"] == 2
    assert report["items"][0]["item_id"] == 10
    assert [item["id"] for item in report["items"][0]["reports"]] == [1, 2]
    assert report["items"][1]["item_id"] == 11
    assert [item["id"] for item in report["items"][1]["reports"]] == [3]
    assert report["summary"]["fetch_errors"] == 0
