import json
import time

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


API_TOKEN = "whackamole-test-token"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("WHACKAMOLE_CONFIG_DIR", str(tmp_path))
    return TestClient(app)


def _auth_headers():
    return {"Authorization": f"Bearer {API_TOKEN}"}


def _seed_item(client: TestClient) -> int:
    db = client.app.state.db
    torrent = {
        "hash": "abc123",
        "name": "Example.Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP",
        "category": "tv",
        "tags": "upload",
        "content_path": "/media/torrents/tv/example.mkv",
        "size": 123456789,
        "added_on": 1779894904,
        "completion_on": 1779894928,
    }
    db.insert_discovered(1, torrent, status="queued", baseline=False)
    item_id = int(db.list_items([], limit=1)[0]["id"])
    db.update_status(
        item_id,
        "candidate",
        "candidate",
        "Valid upload candidate on: IHD",
        mapped_path="/media/torrents/tv/example.mkv",
        ua_args="--site-check -ua -sda",
        ua_log="Trackers passed all checks: IHD",
        tracker_results={"passed": ["IHD"], "dupe": [], "skipped": [], "error": []},
        arr_results={
            "version": 1,
            "status": "candidate",
            "reason": "Valid upload candidate on: IHD",
            "decisions": [
                {
                    "tracker": "IHD",
                    "status": "candidate",
                    "reason": "No equal-or-better torrent result found in the same lane.",
                    "matched_count": 0,
                    "best_release": None,
                }
            ],
        },
        increment_attempt=True,
    )
    return item_id


def test_detailed_api_requires_bearer_token(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)

        assert client.get("/api/items").status_code == 401
        assert client.get("/api/items", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_status_api_is_lightweight_and_does_not_expose_token(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)

        response = client.get("/api/status")

        assert response.status_code == 200
        assert response.json()["configured"]["whackamole_api_token"] is True
        assert response.json()["service"]["maintenance"]["start_time"] == "05:00"
        assert response.json()["service"]["maintenance"]["dependency"] == "QUI"
        assert API_TOKEN not in response.text


def test_config_page_saves_maintenance_guard_settings(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/config",
            data={
                "maintenance_enabled": "on",
                "maintenance_timezone": "Europe/London",
                "maintenance_start_time": "04:45",
                "maintenance_lead_minutes": "45",
            },
        )

        cfg = client.app.state.config_manager.load()

        assert response.status_code == 200
        assert cfg.maintenance.enabled is True
        assert cfg.maintenance.timezone == "Europe/London"
        assert cfg.maintenance.start_time == "04:45"
        assert cfg.maintenance.lead_minutes == 45
        assert cfg.maintenance.resume_signal == "qui_down_up"


def test_config_save_reapplies_release_group_policy_to_candidates(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)

        response = client.post(
            "/config",
            data={
                "policy_ihd_banned": "GRP",
            },
        )
        row = client.app.state.db.get_item(item_id)

        assert response.status_code == 200
        assert "Reapplied release group policy to 1 candidate" in response.text
        assert row["status"] == "blocked"
        assert row["verdict"] == "banned_release_group"


def test_items_api_returns_paginated_summaries_without_logs(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        response = client.get("/api/items?status=candidate&limit=10&offset=0", headers=_auth_headers())

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["count"] == 1
        assert payload["items"][0]["id"] == item_id
        assert payload["items"][0]["tracker_results"]["passed"] == ["IHD"]
        assert payload["items"][0]["arr_summary"] == "Valid: IHD"
        assert "ua" not in payload["items"][0]
        assert "ua_log" not in payload["items"][0]


def test_items_api_can_include_full_details(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        _seed_item(client)

        response = client.get("/api/items?include_details=true", headers=_auth_headers())

        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["ua"]["log"] == "Trackers passed all checks: IHD"
        assert item["checks"]["arr"]["status"] == "candidate"
        assert item["raw_torrent"]["hash"] == "abc123"
        assert API_TOKEN not in response.text


def test_covered_items_api_and_dashboard_widget(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        db = client.app.state.db
        db.update_status(
            item_id,
            "candidate",
            "candidate",
            "Valid upload candidate on: IHD",
            tracker_results={"passed": ["IHD"], "dupe": [], "skipped": [], "error": []},
            arr_results={
                "status": "candidate",
                "reason": "Valid upload candidate on: IHD",
                "decisions": [{"tracker": "IHD", "status": "candidate", "reason": "ok"}],
            },
            check_results={
                "version": 1,
                "release_group_policy": {"candidate_trackers": ["IHD"], "blocked_trackers": []},
                "flags": [{"key": "note", "label": "ULCX note", "severity": "warning", "detail": "ULCX appears in diagnostics only."}],
            },
        )
        db.insert_discovered(
            1,
            {
                "hash": "ihd-upload",
                "name": "Example.Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP",
                "category": "uploads",
                "tags": "upload",
                "save_path": "/media/torrents/uploads/IHD",
                "content_path": "/media/torrents/uploads/IHD/Example.Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP",
                "progress": 1,
            },
            status="inventory",
            baseline=True,
        )

        resolved = db.resolve_covered_candidates()
        response = client.get("/api/items?status=covered&include_details=true", headers=_auth_headers())
        home = client.get("/")
        page = client.get("/dashboard?view=covered")

        assert resolved == {"items": 1, "trackers": 1}
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["id"] == item_id
        assert item["status"] == "covered"
        assert item["tracker_results"]["covered"] == ["IHD"]
        assert item["tracker_summary"] == "Covered in QUI: IHD"
        assert item["arr_summary"] == "Covered: IHD"
        assert item["checks"]["coverage_resolution"]["resolved_trackers"] == ["IHD"]
        assert home.status_code == 200
        assert "Whacked" in home.text
        assert "1 hole" in home.text
        assert "1 uploads" in home.text
        assert page.status_code == 200
        assert "Example.Show" in page.text


def test_items_api_filters_by_inventory_coverage(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "source",
                "name": "Example.Show.S01E01.1080p.WEB-DL-GRP",
                "category": "tv",
                "tags": "",
                "content_path": "/media/torrents/tv/Example.Show.S01E01.1080p.WEB-DL-GRP.mkv",
                "progress": 1,
            },
            status="baseline",
            baseline=True,
        )
        db.insert_discovered(
            1,
            {
                "hash": "dp-cross",
                "name": "Example.Show.S01E01.1080p.WEB-DL-GRP",
                "category": "tv.cross",
                "tags": "cross-seed",
                "save_path": "/media/torrents/cross-seeds/DarkPeers",
                "content_path": "/media/torrents/cross-seeds/DarkPeers/Example.Show.S01E01.1080p.WEB-DL-GRP",
                "progress": 1,
            },
            status="inventory",
            baseline=True,
        )

        hidden = client.get("/api/items?status=baseline&missing=DP", headers=_auth_headers())
        visible = client.get("/api/items?status=baseline&missing=IHD&media=episode", headers=_auth_headers())

        assert hidden.status_code == 200
        assert hidden.json()["total"] == 0
        assert visible.status_code == 200
        item = visible.json()["items"][0]
        assert visible.json()["total"] == 1
        assert item["coverage"][0]["key"] == "DP"
        assert item["missing_primary_trackers"] == ["ULCX", "IHD"]


def test_items_api_search_filters_results(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "other123",
                "name": "Different.Movie.2026.1080p.WEB-DL-GRP",
                "category": "movies",
                "tags": "",
                "content_path": "/media/torrents/movies/different.mkv",
                "progress": 1,
            },
            status="candidate",
            baseline=False,
        )

        response = client.get("/api/items?q=Example.Show", headers=_auth_headers())

        assert response.status_code == 200
        payload = response.json()
        assert payload["q"] == "Example.Show"
        assert payload["total"] == 1
        assert payload["items"][0]["id"] == item_id
        assert payload["items"][0]["display_status"]["label"] == "Ready"


def test_dashboard_search_and_filtered_recheck_preserve_query(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "not-matching",
                "name": "Different.Movie.2026.1080p.WEB-DL-GRP",
                "category": "movies",
                "tags": "",
                "content_path": "/media/torrents/movies/different.mkv",
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        other_id = int(next(row["id"] for row in db.list_items([], limit=10) if row["hash"] == "not-matching"))
        db.update_status(
            other_id,
            "candidate",
            "candidate",
            "Valid upload candidate on: DP",
            tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
            arr_results={},
            increment_attempt=True,
        )

        page = client.get("/dashboard?view=candidates&q=Example.Show")
        response = client.post(
            "/items/recheck-filtered",
            data={"view": "candidates", "q": "Example.Show"},
            follow_redirects=False,
        )

        assert page.status_code == 200
        assert 'name="q" value="Example.Show"' in page.text
        assert "Different.Movie" not in page.text
        assert response.status_code == 303
        assert "q=Example.Show" in response.headers["location"]
        assert db.get_item(item_id)["status"] == "queued"
        assert db.get_item(other_id)["status"] == "candidate"


def test_candidate_dashboard_includes_filters_without_row_recheck_actions(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        page = client.get("/dashboard?view=candidates&media=episode&missing=DP&valid_for=IHD")

        assert page.status_code == 200
        assert 'name="view" value="candidates"' in page.text
        assert "Missing tracker coverage" in page.text
        assert "Decision valid for" in page.text
        assert "Blocked reason" in page.text
        assert "Review reason" in page.text
        assert "/items/recheck-filtered" in page.text
        assert f'/items/{item_id}/recheck' not in page.text
        assert "Run recheck" not in page.text
        assert "mobile-bottom-nav" in page.text
        assert "data-search-open" in page.text
        assert "data-search-modal" in page.text
        assert f'/items/{item_id}/upload-assistant/queue' in page.text
        assert 'data-submit-tick="Upload queued"' in page.text
        assert "data-submit-tick-button" in page.text
        assert "Upload" in page.text
        assert "filter-view-list" not in page.text


def test_dashboard_valid_for_filter_excludes_other_tracker_candidates(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_item(client)

        page = client.get("/dashboard?view=candidates&valid_for=ULCX")

        assert page.status_code == 200
        assert "Example.Show.S01E01" not in page.text
        assert "No items in this view." in page.text


def test_candidate_dashboard_suppresses_non_final_dupe_flags(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        client.app.state.db.update_status(
            item_id,
            "candidate",
            "candidate",
            "Valid upload candidate on: IHD",
            tracker_results={"passed": ["IHD"], "dupe": ["DP"], "skipped": [], "error": []},
            arr_results={
                "decisions": [{"tracker": "IHD", "status": "candidate", "reason": "ok"}],
            },
        )

        response = client.get("/api/items?status=candidate", headers=_auth_headers())

        assert response.status_code == 200
        tags = response.json()["items"][0]["alert_tags"]
        assert "Dupe" not in {tag["label"] for tag in tags}


def test_dashboard_list_does_not_build_detail_release_views(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_item(client)

        def fail_detail_builder(*args, **kwargs):
            raise AssertionError("detail release views should not be built for dashboard rows")

        monkeypatch.setattr(main_module, "_arr_release_views", fail_detail_builder)

        page = client.get("/dashboard?view=candidates")

        assert page.status_code == 200
        assert "Example.Show.S01E01" in page.text


def test_manual_review_dashboard_includes_filters(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "manual-filter",
                "name": "Example.Show.S01E01.1080p.WEB-DL-GRP",
                "category": "tv",
                "tags": "",
                "content_path": "/media/torrents/tv/Example.Show.S01E01.1080p.WEB-DL-GRP",
                "progress": 1,
            },
            status="manual_review",
            baseline=False,
        )

        page = client.get("/dashboard?view=manual")

        assert page.status_code == 200
        assert 'name="view" value="manual"' in page.text
        assert "Missing tracker coverage" in page.text
        assert "/items/recheck-filtered" in page.text


def test_filtered_recheck_endpoint_requeues_candidate_view(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        response = client.post(
            "/items/recheck-filtered",
            data={"view": "candidates", "media": "episode"},
            follow_redirects=False,
        )
        row = client.app.state.db.get_item(item_id)

        assert response.status_code == 303
        assert response.headers["location"].startswith("/dashboard?view=candidates")
        assert row["status"] == "queued"
        assert row["reason"] == "Bulk recheck requested from candidate filtered set"


def test_items_api_filters_by_multi_media_missing_and_valid_for(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "movie-candidate",
                "name": "Different.Movie.2026.1080p.WEB-DL-GRP",
                "category": "movies",
                "tags": "",
                "content_path": "/media/torrents/movies/different.mkv",
                "progress": 1,
            },
            status="candidate",
            baseline=False,
        )

        response = client.get(
            "/api/items?status=candidate&media=episode&media=movie&missing=IHD&valid_for=IHD",
            headers=_auth_headers(),
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["media"] == ["episode", "movie"]
        assert payload["valid_for"] == ["IHD"]
        assert payload["total"] == 1
        assert payload["items"][0]["id"] == item_id
        assert payload["items"][0]["valid_for_trackers"] == ["IHD"]

        wrong_tracker = client.get("/api/items?status=candidate&valid_for=ULCX", headers=_auth_headers())
        assert wrong_tracker.status_code == 200
        assert wrong_tracker.json()["total"] == 0


def test_dashboard_reason_filter_and_table_shape(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "blocked-arr",
                "name": "Blocked.Show.S01E01.1080p.WEB-DL-GRP",
                "category": "tv",
                "tags": "ready,mediainfo-warning",
                "content_path": "/media/torrents/tv/Blocked.Show.S01E01.1080p.WEB-DL-GRP",
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])
        db.update_status(
            item_id,
            "blocked",
            "not_upgrade",
            "UA passed, but Arr found equal-or-better torrent results.",
            tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
            arr_results={
                "status": "blocked",
                "reason": "UA passed, but Arr found equal-or-better torrent results.",
                "decisions": [{"tracker": "DP", "status": "blocked", "reason": "equal-or-better"}],
            },
        )

        page = client.get("/dashboard?view=blocked&reason=arr_equal_or_better")

        assert page.status_code == 200
        assert "Blocked.Show" in page.text
        assert "Title" in page.text
        assert "Decision" in page.text
        assert "Decision Notice" in page.text
        assert "/media/torrents/tv/Blocked.Show" not in page.text
        assert "coverage-badge missing-default" in page.text


def test_blocked_dashboard_tags_final_verdict_without_duplicate_mobile_verdict_text(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "no-tracker-passed",
                "name": "Blocked.Show.S01E01.1080p.WEB-DL-GRP",
                "category": "tv",
                "tags": "",
                "content_path": "/media/torrents/tv/Blocked.Show.S01E01.1080p.WEB-DL-GRP",
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])
        db.update_status(
            item_id,
            "blocked",
            "no_tracker_passed",
            "No tracker passed UA checks.",
            tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
        )

        api_response = client.get("/api/items?status=blocked", headers=_auth_headers())
        page = client.get("/dashboard?view=blocked")

        assert api_response.status_code == 200
        assert "No Tracker Passed" in {tag["label"] for tag in api_response.json()["items"][0]["alert_tags"]}
        assert page.status_code == 200
        assert "No tracker passed UA checks." in page.text
        assert '<p class="muted">no_tracker_passed</p>' not in page.text


def test_discovarr_ranking_tags_mark_equal_values_as_same():
    tags = main_module._ranking_tags(
        {
            "scan_type": "progressive",
            "hdr_rank": 2,
            "hdr_label": "HDR10",
            "audio_format_rank": 7,
            "audio_format": "DD+",
            "audio_channels": 5.1,
            "codec": "AVC",
        },
        {
            "scan_type": "progressive",
            "hdr_rank": 2,
            "hdr_label": "HDR10",
            "audio_format_rank": 4,
            "audio_format": "AAC",
            "audio_channels": 7.1,
            "codec": "HEVC",
        },
    )

    groups = {tag["label"]: tag["group"] for tag in tags}
    assert groups["Scan"] == "same"
    assert groups["HDR"] == "same"
    assert groups["Audio"] == "better"
    assert groups["Channels"] == "worse"
    assert groups["Codec"] == "worse"


def test_discovarr_ranking_tags_treat_shared_hdr10plus_as_same():
    tags = main_module._ranking_tags(
        {"hdr_rank": 4, "hdr_label": "DV/HDR fallback", "hdr_formats": ["Dolby Vision", "HDR10+", "HDR10"]},
        {"hdr_rank": 2, "hdr_label": "HDR10+", "hdr_formats": ["HDR10+", "HDR10"]},
    )

    assert {tag["label"]: tag["group"] for tag in tags}["HDR"] == "same"


def test_service_error_history_popout_and_clear(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.append_service_error("QUI timeout", occurred_at=1779894904)
        db.append_service_error("QUI timeout", occurred_at=1779894964)

        page = client.get("/")
        status_response = client.get("/api/status")
        clear = client.post("/service-errors/clear", data={"return_to": "/"}, follow_redirects=False)

        assert page.status_code == 200
        assert "Service errors" in page.text
        assert "QUI timeout" in page.text
        assert "x2" in page.text
        assert status_response.json()["service"]["service_errors"][0]["count"] == 2
        assert clear.status_code == 303
        assert db.service_error_history() == []


def test_item_detail_and_log_endpoints_return_full_check_data(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        detail = client.get(f"/api/items/{item_id}", headers=_auth_headers())
        log = client.get(f"/api/items/{item_id}/log", headers=_auth_headers())
        missing = client.get("/api/items/999999", headers=_auth_headers())

        assert detail.status_code == 200
        assert detail.json()["ua"]["args"] == "--site-check -ua -sda"
        assert detail.json()["arr"]["decisions"][0]["tracker"] == "IHD"
        assert log.status_code == 200
        assert log.text == "Trackers passed all checks: IHD"
        assert log.headers["content-type"].startswith("text/plain")
        assert missing.status_code == 404


def test_reporting_api_tracks_active_resolved_and_deleted_reports(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        created = client.post(
            f"/api/items/{item_id}/reports",
            json={"stage": "MediaInfo", "notes": "Audio tags look wrong"},
            headers=_auth_headers(),
        )
        active = client.get("/api/reports", headers=_auth_headers())
        report_id = created.json()["report"]["id"]
        detail = client.get(f"/api/reports/{report_id}", headers=_auth_headers())
        resolved = client.post(f"/api/reports/{report_id}/resolve", headers=_auth_headers())
        active_after_resolve = client.get("/api/reports", headers=_auth_headers())
        resolved_list = client.get("/api/reports?state=resolved", headers=_auth_headers())
        deleted = client.delete(f"/api/reports/{report_id}", headers=_auth_headers())
        missing = client.get(f"/api/reports/{report_id}", headers=_auth_headers())

        assert created.status_code == 201
        assert created.json()["report"]["stage"] == "MediaInfo"
        assert active.json()["count"] == 1
        assert detail.json()["report"]["notes"] == "Audio tags look wrong"
        assert resolved.status_code == 200
        assert active_after_resolve.json()["count"] == 0
        assert resolved_list.json()["count"] == 1
        assert deleted.status_code == 200
        assert missing.status_code == 404


def test_item_page_renders_reporting_tab_actions_and_removed_tabs(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert 'data-tab-target="reporting"' in page.text
        assert "Flag Error" in page.text
        assert "Processing Stage" in page.text
        assert 'data-tab-target="checks"' not in page.text
        assert 'data-tab-target="trackers"' not in page.text
        assert ">Checks<" not in page.text
        assert ">Trackers<" not in page.text
        assert "Queue Upload" in page.text
        assert 'data-submit-tick="Recheck triggered"' in page.text
        assert 'data-submit-tick="Upload queued"' in page.text
        assert "Next Item" in page.text
        assert ">Size<" not in page.text


def test_high_quality_trackers_default_empty_and_cross_check_setting(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        default_response = client.get(f"/api/items/{item_id}", headers=_auth_headers())
        save = client.post("/config", data={"high_quality_trackers": "IHD"})
        configured_response = client.get(f"/api/items/{item_id}", headers=_auth_headers())

        assert default_response.json()["cross_check"]["selected"] == []
        assert default_response.json()["cross_check"]["label"] == "Not Validated"
        assert save.status_code == 200
        assert "High Quality Trackers" in save.text
        assert configured_response.json()["cross_check"]["selected"] == ["IHD"]
        assert configured_response.json()["cross_check"]["label"] == "Validated On High Quality Tracker"


def test_item_detail_includes_video_files_in_paths_section(tmp_path, monkeypatch):
    media_dir = tmp_path / "media" / "Example.Show.S01E01.1080p.WEB-DL-GRP"
    sample = media_dir / "Sample.txt"
    episode = media_dir / "Example.Show.S01E01.1080p.WEB-DL-GRP.mkv"
    extra = media_dir / "Extras" / "Behind.The.Scenes.mp4"
    extra.parent.mkdir(parents=True)
    sample.write_text("not video", encoding="utf-8")
    episode.write_bytes(b"episode")
    extra.write_bytes(b"extra")

    with _client(tmp_path / "config", monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "video-files",
                "name": "Example.Show.S01E01.1080p.WEB-DL-GRP",
                "category": "tv",
                "tags": "",
                "content_path": str(media_dir),
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])
        db.update_status(
            item_id,
            "manual_review",
            "no_video_files",
            "Needs inspection",
            mapped_path=str(media_dir),
            ua_log="No Video files found",
            tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
            arr_results={},
            increment_attempt=True,
        )

        detail = client.get(f"/api/items/{item_id}", headers=_auth_headers())
        page = client.get(f"/items/{item_id}")

        assert detail.status_code == 200
        files = detail.json()["video_files"]["files"]
        assert [item["relative_path"] for item in files] == [
            "Example.Show.S01E01.1080p.WEB-DL-GRP.mkv",
            "Extras/Behind.The.Scenes.mp4",
        ]
        assert detail.json()["video_files"]["message"] == ""
        assert page.status_code == 200
        assert "Video files" in page.text
        assert "Example.Show.S01E01.1080p.WEB-DL-GRP.mkv" in page.text
        assert "Behind.The.Scenes.mp4" in page.text
        assert "Sample.txt" not in page.text


def test_no_video_manual_review_item_renders_and_serializes(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "no-video",
                "name": "Wild.At.Heart.S06.1080p.AMZN.WEB-DL.DD2.0.x264-NTb",
                "category": "tv",
                "tags": "",
                "content_path": "/media/torrents/tv/Wild.At.Heart.S06.1080p.AMZN.WEB-DL.DD2.0.x264-NTb",
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])
        reason = "UA could not find video files at the mapped path. Check the torrent path/mount or rerun after mover maintenance."
        db.update_status(
            item_id,
            "manual_review",
            "no_video_files",
            reason,
            ua_log="No Video files found",
            tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
            arr_results={},
            increment_attempt=True,
        )

        api_response = client.get(f"/api/items/{item_id}", headers=_auth_headers())
        page_response = client.get(f"/items/{item_id}")

        assert api_response.status_code == 200
        assert api_response.json()["status"] == "manual_review"
        assert api_response.json()["verdict"] == "no_video_files"
        assert api_response.json()["reason"] == reason
        assert page_response.status_code == 200
        assert "no_video_files" in page_response.text
        assert reason in page_response.text


def test_grab_nfo_updates_source_without_rechecking_item(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        media_dir = tmp_path / "media" / "Example.Show.S01E01.1080p.WEB-DL-GRP"
        media_dir.mkdir(parents=True)
        (media_dir / "Example.Show.S01E01.nfo").write_text("Site: Netflix\nNetwork: Netflix\n", encoding="utf-8")
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "nfo-source",
                "name": "Example.Show.S01E01.1080p.WEB-DL-GRP",
                "category": "tv",
                "tags": "",
                "content_path": str(media_dir),
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])
        db.update_status(
            item_id,
            "candidate",
            "candidate",
            "Valid upload candidate on: DP",
            tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
            arr_results={
                "status": "candidate",
                "local_traits": {
                    "resolution": "1080p",
                    "source": "web",
                    "source_label": "WEB",
                    "source_tag": "WEB-DL",
                    "rip_type": "web-dl",
                    "audio_format": "",
                    "audio_channels": 0,
                    "codec": "AVC",
                },
                "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
            },
            check_results={
                "media": {
                    "mediainfo_files": [
                        {
                            "traits": {
                                "audio_format": "DD+",
                                "audio_format_rank": 7,
                                "audio_channels": 5.1,
                                "codec": "AVC",
                            }
                        }
                    ]
                }
            },
        )

        response = client.post(f"/items/{item_id}/grab-nfo", data={"return_to": f"/items/{item_id}"}, follow_redirects=False)
        row = db.get_item(item_id)
        checks = json.loads(row["check_results"])
        page = client.get(f"/items/{item_id}")

        assert response.status_code == 303
        assert row["status"] == "candidate"
        assert row["attempt_count"] == 0
        assert checks["nfo"]["provider_abbreviation"] == "NF"
        assert checks["nfo"]["content"].startswith("Site: Netflix")
        assert "Source: NF" in page.text
        assert "Source Missing" not in page.text
        assert "DD+" in page.text
        assert "5.1" in page.text


def test_mediainfo_hdr_max_luminance_does_not_become_max_source(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "max-luminance",
                "name": "Greenland.2.Migration.2026.2160p.WebRip.Atmos.EAC3.5.1.HDR.x265-Lootera",
                "category": "movies",
                "tags": "",
                "content_path": "/media/torrents/movies/greenland.mkv",
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])
        db.update_status(
            item_id,
            "candidate",
            "candidate",
            "Valid upload candidate on: DP",
            mapped_path="/media/torrents/movies/greenland.mkv",
            tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
            arr_results={
                "status": "candidate",
                "local_traits": {
                    "resolution": "2160p",
                    "source": "web",
                    "source_label": "WEB",
                    "source_tag": "WEBRip",
                    "source_provider": "",
                    "rip_type": "webrip",
                    "audio_format": "DD+ Atmos",
                    "audio_channels": 5.1,
                    "codec": "HEVC",
                },
                "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
            },
            check_results={
                "media": {
                    "status": "passed",
                    "reason": "MediaInfo confirmed with warning: max luminance is informational.",
                    "raw_mediainfo_payloads": [
                        {
                            "streams": [
                                {
                                    "kind": "video",
                                    "fields": [
                                        {"name": "MasteringDisplay_Luminance", "value": "min: 0.0001 cd/m2, max: 1000 cd/m2"}
                                    ],
                                }
                            ]
                        }
                    ],
                },
                "nfo": {"available": False, "message": "No NFO found at this path."},
            },
        )

        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert "Source Missing" in page.text
        assert "Source: MAX" not in page.text


def test_dashboard_active_view_hides_waiting_errors_but_errors_view_keeps_them(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        now = int(time.time())
        for torrent_hash, name in [
            ("due-error", "Due.Error.Show.S01E01.1080p.WEB-DL-GRP"),
            ("future-error", "Future.Error.Show.S01E01.1080p.WEB-DL-GRP"),
        ]:
            db.insert_discovered(
                1,
                {
                    "hash": torrent_hash,
                    "name": name,
                    "category": "tv",
                    "tags": "",
                    "content_path": f"/media/torrents/tv/{name}",
                    "progress": 1,
                },
                status="queued",
                baseline=False,
            )
        rows = {row["hash"]: row for row in db.list_items([], limit=20)}
        db.update_status(int(rows["due-error"]["id"]), "error", "ua_error", "Due now", next_check_at=now - 1)
        db.update_status(int(rows["future-error"]["id"]), "error", "ua_error", "Waiting", next_check_at=now + 3600)

        active = client.get("/dashboard?view=active")
        errors = client.get("/dashboard?view=errors")

        assert active.status_code == 200
        assert "Due.Error.Show" in active.text
        assert "Future.Error.Show" not in active.text
        assert errors.status_code == 200
        assert "Due.Error.Show" in errors.text
        assert "Future.Error.Show" in errors.text
