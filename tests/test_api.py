import time

from fastapi.testclient import TestClient

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


def test_candidate_dashboard_includes_filters_and_recheck_actions(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        page = client.get("/?view=candidates&media=episode&missing=DP")

        assert page.status_code == 200
        assert 'name="view" value="candidates"' in page.text
        assert "Missing tracker coverage" in page.text
        assert "/items/recheck-filtered" in page.text
        assert f'/items/{item_id}/recheck' in page.text
        assert "Run recheck" in page.text


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

        page = client.get("/?view=manual")

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
        assert response.headers["location"].startswith("/?view=candidates")
        assert row["status"] == "queued"
        assert row["reason"] == "Bulk recheck requested from candidate filtered set"


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

        active = client.get("/?view=active")
        errors = client.get("/?view=errors")

        assert active.status_code == 200
        assert "Due.Error.Show" in active.text
        assert "Future.Error.Show" not in active.text
        assert errors.status_code == 200
        assert "Due.Error.Show" in errors.text
        assert "Future.Error.Show" in errors.text
