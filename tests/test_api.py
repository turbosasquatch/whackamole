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
        assert API_TOKEN not in response.text


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
