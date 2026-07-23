import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

import app.main as main_module
import app.upload_console as upload_console_module
from app.main import app


API_TOKEN = "whackamole-test-token-that-is-at-least-32-characters"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("WHACKAMOLE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("WHACKAMOLE_API_TOKEN", API_TOKEN)
    monkeypatch.setenv("WHACKAMOLE_ALLOWED_MEDIA_ROOTS", str(tmp_path.parent))
    return TestClient(app, headers=_auth_headers())


def _auth_headers():
    return {"Authorization": f"Bearer {API_TOKEN}"}


def test_raw_payloads_include_local_mediainfo_json():
    payloads = main_module._raw_payloads(
        {
            "raw_torrent": {},
            "media_raw_mediainfo_payloads": json.dumps([{"source": "qui-column"}]),
            "media_raw_local_mediainfo_payloads": json.dumps([{"source": "local-column"}]),
            "check_results": {
                "media": {
                    "raw_mediainfo_payloads": [{"source": "qui"}],
                    "raw_local_mediainfo_payloads": [{"source": "local"}],
                }
            },
            "ua_log": "",
            "arr_result": {},
        }
    )

    assert payloads["mediainfo"]["title"] == "Raw QUI MediaInfo"
    assert payloads["local-mediainfo"]["title"] == "Raw Local MediaInfo"
    assert payloads["mediainfo"]["content"] == [{"source": "qui-column"}]
    assert payloads["local-mediainfo"]["content"] == [{"source": "local-column"}]


def test_item_notices_extract_deduplicate_map_effects_and_order():
    item = {
        "verdict": "",
        "check_results": {
            "media": {
                "issues": [
                    {"key": "bloated_audio", "message": "Audio bitrate exceeds policy.", "severity": "error"},
                    {"key": "unknown_notice", "message": "Uncatalogued media warning.", "severity": "warning"},
                ]
            }
        },
        "check_flags": [
            {"key": "bloated_audio", "label": "Media Info", "detail": "Audio bitrate exceeds policy.", "severity": "error"},
            {
                "key": "no_uploadable_trackers",
                "label": "Upload Assistant",
                "detail": "No tracker accepted this release.",
                "severity": "warning",
            },
        ],
        "rename_check": {
            "status": "manual_review",
            "rows": [
                {
                    "kind": "renamed_release_warning",
                    "difference_summary": "Release group is missing from the filename.",
                    "severity": "warning",
                    "confidence": "high",
                }
            ],
        },
        "overview_checks": [],
    }

    notices = main_module._item_notices(item)

    assert [(notice["category"], notice["result"]) for notice in notices] == [
        ("Media Info", "block"),
        ("Rename", "review"),
        ("Upload Assistant", "skip"),
        ("Media Info", "info"),
    ]
    assert [notice["fault"] for notice in notices].count("Audio bitrate exceeds policy.") == 1
    assert [notice["severity"] for notice in notices] == ["error", "warning", "warning", "warning"]
    assert all(notice["key"] for notice in notices)


def test_item_notices_empty_when_only_checks_pass():
    item = {
        "check_results": {"media": {"issues": []}},
        "check_flags": [],
        "rename_check": {"rows": []},
        "overview_checks": [{"label": "MediaInfo", "group": "pass", "state": "Passed", "notes": "All good"}],
    }

    assert main_module._item_notices(item) == []


def test_local_nfo_for_file_uses_same_stem_sidecar(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch):
        media_path = tmp_path / "Movie.2026.mkv"
        sidecar = tmp_path / "Movie.2026.nfo"
        media_path.write_bytes(b"media")
        sidecar.write_text("Provider: Netflix", encoding="utf-8")

        result = main_module._local_nfo_info_for_item({"mapped_path": str(media_path)})

        assert result["available"] is True
        assert result["path"] == str(sidecar)
        assert result["content"] == "Provider: Netflix"


def test_local_nfo_for_file_ignores_unrelated_sibling(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch):
        media_path = tmp_path / "Movie.2026.mkv"
        media_path.write_bytes(b"media")
        (tmp_path / "Different.Release.nfo").write_text("unrelated", encoding="utf-8")

        result = main_module._local_nfo_info_for_item({"mapped_path": str(media_path)})

        assert result == {"available": False, "message": "No NFO found at this path."}


def test_local_nfo_for_file_never_globs_parent(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch):
        media_path = tmp_path / "Movie.2026.mkv"
        media_path.write_bytes(b"media")

        def fail_glob(*args, **kwargs):
            raise AssertionError("file-backed NFO lookup must not enumerate siblings")

        monkeypatch.setattr(Path, "glob", fail_glob)

        result = main_module._local_nfo_info_for_item({"mapped_path": str(media_path)})

        assert result["available"] is False


def test_discovarr_unavailable_summary_is_fail():
    state, group = main_module._arr_summary_state(
        {
            "status": "manual_review",
            "reason": "Arr comparison unavailable: No matching Sonarr series found",
        }
    )

    assert (state, group) == ("Fail", "error")


def test_app_enables_gzip_for_large_responses():
    assert any(middleware.cls is GZipMiddleware for middleware in app.user_middleware)


def test_dashboard_check_results_strip_raw_mediainfo_payloads():
    payload = {
        "media": {
            "status": "passed",
            "raw_mediainfo_payloads": [{"large": "payload", "ServiceName": "Netflix"}],
            "raw_local_mediainfo_payloads": [{"large": "local"}],
            "supplemental_mediainfo_files": [{"traits": {"audio_format": "DD+"}}],
            "mediainfo_files": [
                {
                    "name": "movie.mkv",
                    "traits": {"source_provider": "Netflix", "audio_format": "DD+"},
                    "video": {"huge": "track"},
                }
            ],
        }
    }

    slim = main_module._dashboard_check_results(json.dumps(payload))
    media = slim["media"]

    assert media["dashboard_source_provider"] == "NF"
    assert "raw_mediainfo_payloads" not in media
    assert "raw_local_mediainfo_payloads" not in media
    assert "supplemental_mediainfo_files" not in media
    assert media["mediainfo_files"] == [{"traits": {"source_provider": "Netflix", "audio_format": "DD+"}, "name": "movie.mkv"}]


def test_dashboard_check_results_does_not_scan_raw_mediainfo_payloads(monkeypatch):
    def fail_raw_scan(*args, **kwargs):
        raise AssertionError("dashboard check parsing should not recursively scan raw MediaInfo")

    monkeypatch.setattr(upload_console_module, "_collect_source_provider_fields", fail_raw_scan)
    payload = {
        "media": {
            "status": "passed",
            "raw_mediainfo_payloads": [{"ServiceName": "Netflix", "nested": [{"value": "NF"}]}],
            "raw_local_mediainfo_payloads": [{"ServiceName": "Netflix"}],
            "mediainfo_files": [{"name": "movie.mkv", "traits": {}}],
        }
    }

    slim = main_module._dashboard_check_results(json.dumps(payload))

    assert "dashboard_source_provider" not in slim["media"]
    assert "raw_mediainfo_payloads" not in slim["media"]
    assert "raw_local_mediainfo_payloads" not in slim["media"]


def test_dashboard_database_query_strips_raw_mediainfo_payloads(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        client.app.state.db.update_check_results(
            item_id,
            {
                "media": {
                    "status": "passed",
                    "raw_mediainfo_payloads": [{"large": "qui"}],
                    "raw_local_mediainfo_payloads": [{"large": "local"}],
                    "supplemental_mediainfo_files": [{"large": "supplemental"}],
                    "mediainfo_files": [{"name": "movie.mkv", "traits": {"source_provider": "Netflix"}}],
                }
            },
        )

        row = client.app.state.db.list_dashboard_items_filtered(["candidate"], limit=1)[0]
        checks = json.loads(row["check_results"])
        media = checks["media"]

        assert "raw_mediainfo_payloads" not in media
        assert "raw_local_mediainfo_payloads" not in media
        assert "supplemental_mediainfo_files" not in media
        assert media["mediainfo_files"][0]["traits"]["source_provider"] == "Netflix"
        full_row = client.app.state.db.get_item(item_id)
        assert json.loads(full_row["media_raw_mediainfo_payloads"]) == [{"large": "qui"}]
        assert json.loads(full_row["media_raw_local_mediainfo_payloads"]) == [{"large": "local"}]
        assert json.loads(full_row["media_supplemental_mediainfo_files"]) == [{"large": "supplemental"}]


def test_database_backfill_moves_existing_raw_mediainfo_payloads(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        payload = {
            "media": {
                "status": "passed",
                "raw_mediainfo_payloads": [{"large": "old-qui"}],
                "raw_local_mediainfo_payloads": [{"large": "old-local"}],
                "supplemental_mediainfo_files": [{"large": "old-supplemental"}],
            }
        }
        db = client.app.state.db
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE items
                SET check_results = ?,
                    media_raw_mediainfo_payloads = '[]',
                    media_raw_local_mediainfo_payloads = '[]',
                    media_supplemental_mediainfo_files = '[]'
                WHERE id = ?
                """,
                (json.dumps(payload), item_id),
            )
            conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("media_payload_columns_backfilled_v1", "false"),
            )

        db._init()
        row = db.get_item(item_id)
        checks = json.loads(row["check_results"])

        assert "raw_mediainfo_payloads" not in checks["media"]
        assert json.loads(row["media_raw_mediainfo_payloads"]) == [{"large": "old-qui"}]
        assert json.loads(row["media_raw_local_mediainfo_payloads"]) == [{"large": "old-local"}]
        assert json.loads(row["media_supplemental_mediainfo_files"]) == [{"large": "old-supplemental"}]


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


def test_sidebar_prioritizes_constant_triage_views(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_item(client)

        response = client.get("/dashboard?view=candidates")

        assert response.status_code == 200
        labels = ["Candidates", "Review", "Reports", "Import Queue", "Active"]
        positions = [response.text.index(f"<span>{label}</span>") for label in labels]
        assert positions == sorted(positions)
        assert 'href="/imports?view=queue&amp;page=1"' in response.text
        assert 'data-count-view="imports"' in response.text
        assert 'data-count-view="reports"' in response.text


def test_item_detail_tabs_expose_accessible_tab_markup(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)

        response = client.get(f"/items/{item_id}")

        assert response.status_code == 200
        assert 'role="tablist" aria-label="Item sections"' in response.text
        assert response.text.count('role="tab"') == 6
        assert response.text.count('role="tabpanel"') == 6
        for tab in ["overview", "rename", "mediainfo", "upload-assistant", "discovarr", "reporting"]:
            assert f'id="tab-{tab}"' in response.text
            assert f'aria-controls="panel-{tab}"' in response.text
            assert f'id="panel-{tab}"' in response.text
            assert f'aria-labelledby="tab-{tab}"' in response.text
            assert f'data-tab-target="{tab}"' in response.text
            assert f'data-tab-panel="{tab}"' in response.text
        assert 'id="tab-overview" class="active" type="button" role="tab" aria-selected="true" aria-controls="panel-overview" tabindex="0"' in response.text
        for tab in ["rename", "mediainfo", "upload-assistant", "discovarr", "reporting"]:
            tab_markup = response.text.split(f'id="tab-{tab}"', 1)[1].split(">", 1)[0]
            assert 'type="button"' in tab_markup
            assert 'role="tab"' in tab_markup
            assert 'aria-selected="false"' in tab_markup
            assert f'aria-controls="panel-{tab}"' in tab_markup
            assert 'tabindex="-1"' in tab_markup
        assert 'id="panel-overview" class="tab-panel active" role="tabpanel" aria-labelledby="tab-overview"' in response.text
        assert 'id="panel-reporting" class="tab-panel" role="tabpanel" aria-labelledby="tab-reporting" data-tab-panel="reporting" hidden' in response.text


def test_item_detail_exposes_mobile_summary_navigation_and_reporting_controls(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)

        response = client.get(f"/items/{item_id}")

        assert response.status_code == 200
        assert 'class="item-summary-card"' in response.text
        assert 'class="item-mobile-navigation mobile-item-only"' in response.text
        assert 'aria-label="Previous found item"' in response.text
        assert 'aria-label="Next found item"' in response.text
        assert "data-tab-more-toggle" not in response.text
        assert "mobile-check-detail" not in response.text
        assert "rename-mobile-accordions" in response.text
        assert "media-mobile-issues" in response.text
        assert 'data-copy-value-from="[data-upload-path]"' in response.text
        assert 'id="mobile-report-notes"' in response.text
        assert 'maxlength="1000"' in response.text
        assert 'data-character-count-for="mobile-report-notes"' in response.text
        assert "reporting-list-accordion" in response.text


def test_item_summary_reuses_list_action_icons_and_dividers(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)

        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert 'class="item-summary-actions row-actions"' in page.text
        assert 'class="row-actions-divider"' in page.text
        assert '<path d="M16 16l-4-4-4 4" />' in page.text
        assert '<circle cx="12" cy="12" r="10" />' in page.text
        assert '<polyline points="23 4 23 10 17 10" />' in page.text


def test_item_overview_renders_notice_table_widgets_short_tabs_and_empty_state(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        row = client.app.state.db.get_item(item_id)
        client.app.state.db.update_status(
            item_id,
            "manual_review",
            "media_warning",
            "Audio codec needs review.",
            check_results={
                "flags": [
                    {
                        "key": "bloated_audio",
                        "label": "Media Info",
                        "detail": "Audio codec needs review.",
                        "severity": "error",
                    }
                ]
            },
        )

        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert 'class="item-notices-table desktop-item-only"' in page.text
        assert 'class="item-notice-widgets mobile-item-only"' in page.text
        for heading in ("Category", "Fault", "Severity", "Result"):
            assert f"<th>{heading}</th>" in page.text
        assert "Audio codec needs review." in page.text
        assert 'class="notice-pill severity-error">Error</span>' in page.text
        assert 'class="notice-pill result-block">Block</span>' in page.text
        for label in ("Overview", "Rename", "Media", "Upload", "Compare", "Report"):
            assert f">{label}</button>" in page.text
        assert ">Media Info</button>" not in page.text
        assert ">Upload Assistant</button>" not in page.text

def test_item_navigation_uses_complete_filtered_found_set_and_preserves_context(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        ids = []
        for index in range(77):
            db.insert_discovered(
                1,
                {
                    "hash": f"foundset-{index}",
                    "name": f"Foundset Item {index:02d}",
                    "category": "movie",
                    "tags": "",
                    "content_path": f"/media/foundset-{index}.mkv",
                    "size": index + 1,
                },
                status="candidate",
                baseline=False,
            )
            with db.connect() as conn:
                ids.append(int(conn.execute("SELECT id FROM items WHERE hash = ?", (f"foundset-{index}",)).fetchone()["id"]))
        with db.connect() as conn:
            for index, item_id in enumerate(ids):
                conn.execute("UPDATE items SET updated_at = ? WHERE id = ?", (index + 1, item_id))

        ordered_ids = list(reversed(ids))
        return_url = "/dashboard?view=candidates&page=2&q=Foundset"
        middle_id = ordered_ids[75]
        page = client.get(f"/items/{middle_id}", params={"from": return_url})

        assert page.status_code == 200
        encoded_context = "%2Fdashboard%3Fview%3Dcandidates%26page%3D2%26q%3DFoundset"
        assert f'href="/items/{ordered_ids[74]}?from={encoded_context}" aria-label="Previous found item"' in page.text
        assert f'href="/items/{ordered_ids[76]}?from={encoded_context}" aria-label="Next found item"' in page.text
        assert f'value="/items/{middle_id}?from={encoded_context}"' in page.text

        first_page = client.get(f"/items/{ordered_ids[0]}", params={"from": return_url})
        last_page = client.get(f"/items/{ordered_ids[-1]}", params={"from": return_url})
        assert first_page.text.count('aria-label="Previous found item" disabled') == 2
        assert last_page.text.count('aria-label="Next found item" disabled') == 2

        dashboard_page_two = client.get(return_url)
        assert f'href="/items/{ordered_ids[75]}?from={encoded_context}"' in dashboard_page_two.text


def test_item_page_separates_title_and_confirmed_mediainfo_tags(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        release = "Movie.2024.2160p.BluRay.TrueHD.Atmos.7.1.HDR10.x265-GRP"
        client.app.state.db.update_status(
            item_id,
            "manual_review",
            "media_error",
            "Name says 7.1, but MediaInfo reports 5.1.",
            check_results={
                "media": {
                    "status": "manual_review",
                    "media_status": "error",
                    "release_title": release,
                    "confirmed_tags": ["2160p", "BluRay", "7.1"],
                    "custom_formats": ["HEVC/x265"],
                    "issues": [
                        {
                            "severity": "ERROR",
                            "key": "audio_channels_mismatch",
                            "message": "Name says 7.1, but MediaInfo reports 5.1.",
                        }
                    ],
                    "mediainfo_files": [
                        {
                            "name": f"{release}/{release}.mkv",
                            "tags": ["2160p", "progressive", "HEVC", "TrueHD", "5.1", "HDR10", "Subtitles"],
                            "custom_formats": ["HEVC/x265", "TrueHD", "HDR10"],
                            "traits": {
                                "resolution": "2160p",
                                "codec": "HEVC",
                                "audio_format": "TrueHD",
                                "audio_channels": 5.1,
                                "hdr_formats": ["HDR10"],
                                "subtitle_tags": ["Subtitles"],
                            },
                        }
                    ],
                }
            },
        )

        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert '<span class="desktop-item-only">Title</span>' in page.text
        assert '<span class="mobile-item-only">Title (Name / Release Tags)</span>' in page.text
        assert '<span class="desktop-item-only">MediaInfo</span>' in page.text
        assert '<span class="mobile-item-only">MediaInfo (Extracted)</span>' in page.text
        assert '<span class="title-tag mismatch" title="Not confirmed by MediaInfo.">7.1</span>' in page.text
        assert '<span class="title-tag neutral" title="Not directly verified by MediaInfo.">BluRay</span>' in page.text
        assert '<span class="media-confirmed-tag">5.1</span>' in page.text
        assert '<span class="media-confirmed-tag">7.1</span>' not in page.text


def test_detailed_api_requires_bearer_token(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)

        assert client.get("/api/items", headers={"Authorization": ""}).status_code == 401
        assert client.get("/api/items", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_status_api_is_lightweight_and_does_not_expose_token(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)

        response = client.get("/ui-api/status")

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
                "max_qui_poll_pages": "5",
                "max_mediainfo_files_per_check": "4",
                "arr_metadata_cache_seconds": "120",
            },
        )

        cfg = client.app.state.config_manager.load()

        assert response.status_code == 200
        assert cfg.maintenance.enabled is True
        assert cfg.maintenance.timezone == "Europe/London"
        assert cfg.maintenance.start_time == "04:45"
        assert cfg.maintenance.lead_minutes == 45
        assert cfg.maintenance.resume_signal == "qui_down_up"
        assert cfg.safety.max_qui_poll_pages == 5
        assert cfg.safety.max_mediainfo_files_per_check == 4
        assert cfg.safety.arr_metadata_cache_seconds == 120


def test_config_page_saves_lume_release_group_policy(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/config",
            data={
                "policy_lume_banned": "BADGRP",
                "policy_lume_moderation_queue": "on",
            },
        )

        cfg = client.app.state.config_manager.load()

        assert response.status_code == 200
        assert cfg.tracker_policies["LUME"]["banned_release_groups"] == ["BADGRP"]
        assert cfg.tracker_policies["LUME"]["moderation_queue"] is True
        assert "ranked_release_groups" not in cfg.tracker_policies["LUME"]


def test_settings_pages_use_scoped_navigation_and_cover_assigned_controls(tmp_path, monkeypatch):
    expected = {
        "/config": ["Settings overview", "Configuration at a glance"],
        "/config/connections": ["Probe connections", "qui_url", "profilarr_api_key"],
        "/config/processing": ["Local MediaInfo", "max_concurrent_ua_jobs", "Maintenance guard"],
        "/config/uploading": ["Upload Assistant", "path_source", "high_quality_trackers", "Auto Upload"],
        "/config/trackers": ["Tracker policies", "policy_dp_moderation_queue", "Edit comma-separated list"],
        "/config/notifications": ["Discord webhook", "Built-in events"],
        "/config/security": ["Rotate API token", "Update administrator"],
        "/config/rules": ["Stored Evidence Replay", "policy.moderation_queue_no_targets"],
    }
    with _client(tmp_path, monkeypatch) as client:
        for route, fragments in expected.items():
            response = client.get(route)
            assert response.status_code == 200
            assert 'aria-label="Settings sections"' in response.text
            for fragment in fragments:
                assert fragment in response.text


def test_page_scoped_connection_save_preserves_processing_config(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.safety.max_queue_size = 777
        client.app.state.config_manager.save(cfg)

        response = client.post(
            "/config/connections",
            data={
                "qui_url": "http://qui:7476",
                "qui_instance_id": "2",
                "qui_page_limit": "300",
                "sonarr_url": "",
                "radarr_url": "",
                "easycross_url": "",
                "profilarr_url": "",
            },
            follow_redirects=False,
        )
        saved = client.app.state.config_manager.load()

        assert response.status_code == 303
        assert saved.qui.url == "http://qui:7476"
        assert saved.qui.instance_id == 2
        assert saved.safety.max_queue_size == 777


def test_uploading_path_rows_require_complete_pairs_and_preserve_order(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        invalid = client.post(
            "/config/uploading",
            data={"path_source": ["/one"], "path_target": [""]},
        )
        assert invalid.status_code == 400
        assert "requires both a source and destination" in invalid.text

        valid = client.post(
            "/config/uploading",
            data={
                "path_source": ["/one", "/two"],
                "path_target": ["/target-one", "/target-two"],
            },
            follow_redirects=False,
        )
        mappings = client.app.state.config_manager.load().path_mappings

        assert valid.status_code == 303
        assert [(row.source, row.target) for row in mappings] == [
            ("/one", "/target-one"),
            ("/two", "/target-two"),
        ]


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


def test_rules_page_lists_checks_and_replays_stored_metadata(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)

        page = client.get("/config/rules")
        preview = client.post("/config/rules/replay", data={"mode": "preview"})
        row_after_preview = client.app.state.db.get_item(item_id)
        apply = client.post("/config/rules/replay", data={"mode": "apply"})
        row_after_apply = client.app.state.db.get_item(item_id)
        checks = json.loads(row_after_apply["check_results"])

        assert page.status_code == 200
        assert "Stored Evidence Replay" in page.text
        assert "arr.equal_or_better_no_targets" in page.text
        assert "Terminal failure; investigate rather than retry automatically." in page.text
        assert preview.status_code == 200
        assert "Preview found 1 stored decision update" in preview.text
        assert json.loads(row_after_preview["check_results"]) == {}
        assert apply.status_code == 200
        assert "Applied 1 stored decision update" in apply.text
        assert row_after_apply["status"] == "candidate"
        assert checks["decision"]["winning_rule_id"] == "final.candidate"


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
        assert item["missing_primary_trackers"] == ["ULCX", "IHD", "LUME"]


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


def test_dashboard_clear_search_preserves_filters_without_query(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_item(client)

        page = client.get("/dashboard?view=candidates&media=episode&missing=DP&valid_for=IHD&q=Example.Show&page=2")
        first_page = client.get("/dashboard?view=candidates&q=Example.Show")

        expected_href = "/dashboard?view=candidates&amp;page=1&amp;media=episode&amp;missing=DP&amp;valid_for=IHD"
        assert page.status_code == 200
        assert page.text.count(f'href="{expected_href}"') == 2
        assert "q=Example.Show" in page.text
        assert f'href="{expected_href}&amp;q=' not in page.text
        assert first_page.status_code == 200
        assert "Example.Show" in first_page.text


def test_candidate_dashboard_includes_filters_and_row_recheck_actions(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        page = client.get("/dashboard?view=candidates&media=episode&missing=DP&valid_for=IHD")

        assert page.status_code == 200
        assert 'name="view" value="candidates"' in page.text
        assert "Missing tracker coverage" in page.text
        assert "Decision valid for" in page.text
        assert 'value="LUME"' in page.text
        assert '<th data-column-key="valid-for">Valid For</th>' not in page.text
        assert "Blocked reason" in page.text
        assert "Review reason" in page.text
        assert "/items/recheck-filtered" in page.text
        assert f'/items/{item_id}/recheck' in page.text
        assert "mobile-bottom-nav" not in page.text
        assert "data-search-open" in page.text
        assert "data-search-modal" in page.text
        assert f'/items/{item_id}/upload-assistant/queue' in page.text
        assert f'data-queue-url="/ui-api/items/{item_id}/upload-assistant/queue"' in page.text
        assert "data-queue-upload-form" in page.text
        assert 'data-submit-tick="Upload queued"' in page.text
        assert "data-submit-tick-button" in page.text
        assert f'action="/items/{item_id}/ignore"' in page.text
        assert "data-ignore-item-form" in page.text
        assert 'data-submit-tick="Ignored"' in page.text
        assert 'data-submit-error-label="Retry"' in page.text
        assert 'name="return_to" value="/dashboard?view=candidates&amp;page=1&amp;media=episode&amp;missing=DP&amp;valid_for=IHD"' in page.text
        assert "Ignore" in page.text
        assert "Upload" in page.text
        assert "filter-view-list" not in page.text


def test_ignore_item_redirects_to_safe_return_target(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        response = client.post(
            f"/items/{item_id}/ignore",
            data={"return_to": "/dashboard?view=candidates&page=1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard?view=candidates&page=1"
        assert client.app.state.db.get_item(item_id)["status"] == "ignored"


def test_candidate_dashboard_uses_smaller_page_size(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        for index in range(80):
            db.insert_discovered(
                1,
                {
                    "hash": f"candidate-{index}",
                    "name": f"Candidate.Show.S01E{index:02d}.1080p.WEB-DL-GRP",
                    "category": "tv",
                    "tags": "",
                    "content_path": f"/media/torrents/tv/Candidate.Show.S01E{index:02d}.1080p.WEB-DL-GRP",
                    "progress": 1,
                },
                status="candidate",
                baseline=False,
            )

        original_row_builder = main_module._dashboard_row_dict
        row_builds = 0

        def counting_row_builder(*args, **kwargs):
            nonlocal row_builds
            row_builds += 1
            return original_row_builder(*args, **kwargs)

        monkeypatch.setattr(main_module, "_dashboard_row_dict", counting_row_builder)

        page = client.get("/dashboard?view=candidates")

        assert page.status_code == 200
        assert "Showing 1-75 of 80" in page.text
        assert row_builds == 75


def test_dashboard_row_prep_loads_high_quality_trackers_once_for_100_rows(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        for index in range(100):
            db.insert_discovered(
                1,
                {
                    "hash": f"inventory-{index}",
                    "name": f"Inventory.Movie.{index:03d}.1080p.BluRay-GRP",
                    "category": "movies",
                    "tags": "",
                    "content_path": f"/media/torrents/movies/Inventory.Movie.{index:03d}.mkv",
                    "progress": 1,
                },
                status="inventory",
                baseline=True,
            )

        original_load = client.app.state.config_manager.load
        load_count = 0

        def counting_load():
            nonlocal load_count
            load_count += 1
            return original_load()

        monkeypatch.setattr(client.app.state.config_manager, "load", counting_load)

        items, total = main_module._filtered_dashboard_items(db, ["inventory"], limit=100)

        assert total == 100
        assert len(items) == 100
        assert load_count == 1


def test_preloaded_high_quality_tracker_cross_check_output_parity():
    coverage = [{"key": "ihd", "label": "IHD", "primary": True}]

    empty = main_module._cross_check_status(coverage, [], ())
    matching = main_module._cross_check_status(coverage, [], ("ihd", "IHD", ""))
    nonmatching = main_module._cross_check_status(coverage, [], ("dp", "DP"))

    assert empty == {"state": "not_applicable", "label": "Not Validated", "trackers": [], "selected": []}
    assert matching == {
        "state": "pass",
        "label": "Validated On High Quality Tracker",
        "trackers": ["IHD"],
        "selected": ["IHD"],
    }
    assert nonmatching == {
        "state": "warning",
        "label": "Not Validated",
        "trackers": [],
        "selected": ["DP"],
    }

    item = {"name": "Example.Movie.2026.1080p.BluRay-GRP", "status": "inventory", "check_results": {}}
    tracker_groups = {"passed": [], "covered": [], "dupe": [], "skipped": [], "error": []}

    def alert_labels(trackers):
        tags = main_module._dashboard_alert_tags(item, {}, {}, tracker_groups, coverage, [], trackers)
        return {tag["label"] for tag in tags}

    assert "Cross Check" not in alert_labels(())
    assert "Cross Check" not in alert_labels(("IHD",))
    assert "Cross Check" in alert_labels(("DP",))


def test_folder_name_normalization_keeps_candidate_without_detail_scan(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        client.app.state.db.update_status(
            item_id,
            "candidate",
            "candidate",
            "Valid upload candidate on: IHD",
            mapped_path="/media/torrents/tv/American Crime Story S03 1080p AMZN WEB-DL DDP5 1 H 264-NTb",
        )

        def fail_video_scan(*args, **kwargs):
            raise AssertionError("dashboard rows should not scan media folders")

        monkeypatch.setattr(main_module, "_video_files_for_item", fail_video_scan)

        candidates_page = client.get("/dashboard?view=candidates")
        review_page = client.get("/dashboard?view=manual")
        candidate_api = client.get("/api/items?status=candidate", headers=_auth_headers())
        review_api = client.get("/api/items?status=manual_review", headers=_auth_headers())

        assert candidates_page.status_code == 200
        assert review_page.status_code == 200
        assert "Example.Show.S01E01" in candidates_page.text
        assert "Example.Show.S01E01" not in review_page.text

        assert candidate_api.status_code == 200
        assert review_api.status_code == 200
        candidate_items = candidate_api.json()["items"]
        assert len(candidate_items) == 1
        assert candidate_items[0]["status"] == "candidate"
        assert candidate_items[0]["effective_status"] == "candidate"
        assert candidate_items[0]["decision_label"] == "candidate"
        assert candidate_items[0]["display_status"]["label"] == "Ready"
        assert review_api.json()["items"] == []


def test_candidate_dashboard_marks_items_already_in_upload_queue(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        import_id = client.app.state.db.enqueue_import(
            item_id=item_id,
            item_name="Example.Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP",
            path="/media/torrents/tv/example.mkv",
            args="--trackers ihd --unattended",
        )

        page = client.get("/dashboard?view=candidates")

        assert page.status_code == 200
        assert '<th data-column-key="actions">Actions</th>' in page.text
        assert f'data-queued-import-id="{import_id}"' in page.text
        assert 'aria-label="Upload queued"' in page.text
        assert "disabled" in page.text


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


def test_candidate_dashboard_suppresses_partial_release_group_ban_badge(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        client.app.state.db.update_status(
            item_id,
            "candidate",
            "candidate",
            "Valid upload candidate on: DP, ULCX",
            tracker_results={"passed": ["IHD", "DP", "ULCX"], "dupe": [], "skipped": [], "error": []},
            check_results={
                "flags": [
                    {
                        "key": "banned_release_group",
                        "label": "Banned release group",
                        "severity": "blocker",
                        "detail": "GRACE is banned on: IHD",
                    }
                ],
                "release_group_policy": {
                    "candidate_trackers": ["DP", "ULCX"],
                    "blocked_trackers": ["IHD"],
                    "decisions": [
                        {"tracker": "IHD", "status": "blocked", "reason": "GRACE is banned on IHD."},
                        {"tracker": "DP", "status": "candidate", "reason": "Release group policy allows this tracker."},
                        {"tracker": "ULCX", "status": "candidate", "reason": "Release group policy allows this tracker."},
                    ],
                },
            },
        )

        response = client.get("/api/items?status=candidate", headers=_auth_headers())

        assert response.status_code == 200
        tags = response.json()["items"][0]["alert_tags"]
        assert "Banned" not in {tag["label"] for tag in tags}


def test_dashboard_deduplicates_source_missing_alerts(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        client.app.state.db.update_status(
            item_id,
            "manual_review",
            "source_missing",
            "WEB-DL/WEBRip source provider is missing; review before upload.",
            check_results={
                "flags": [
                    {"key": "source_missing", "label": "Source Missing", "severity": "warning"},
                    {"key": "web_source_missing", "label": "Web Source", "severity": "warning"},
                ],
            },
        )

        response = client.get("/api/items?status=manual_review", headers=_auth_headers())

        labels = [tag["label"] for tag in response.json()["items"][0]["alert_tags"]]
        assert labels.count("Source") == 1
        assert "Web Source" not in labels
        assert "Source Missing" not in labels
        assert "Manual Review" not in labels


def test_dashboard_alert_tags_follow_check_summary_not_raw_flags(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        client.app.state.db.update_status(
            item_id,
            "manual_review",
            "media_error",
            "Name says Atmos, but MediaInfo has no matching object/JOC metadata.",
            check_results={
                "media": {
                    "media_status": "error",
                    "status": "manual_review",
                    "reason": "Name says Atmos, but MediaInfo has no matching object/JOC metadata.",
                    "issues": [{"severity": "ERROR", "key": "audio_object_missing"}],
                },
                "flags": [{"key": "random_note", "label": "Random Old Flag", "severity": "warning"}],
            },
        )

        response = client.get("/api/items?status=manual_review", headers=_auth_headers())

        labels = [tag["label"] for tag in response.json()["items"][0]["alert_tags"]]
        assert "Media Info" in labels
        assert "Random Old Flag" not in labels


def test_dashboard_mediainfo_unavailable_appears_as_error(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        client.app.state.db.update_status(
            item_id,
            "error",
            "mediainfo_unavailable",
            "Whackamole could not read QUI MediaInfo.",
            check_results={
                "media": {
                    "media_status": "error",
                    "status": "error",
                    "verdict": "mediainfo_unavailable",
                    "reason": "Whackamole could not read QUI MediaInfo.",
                    "issues": [{"severity": "ERROR", "key": "mediainfo_unavailable"}],
                },
                "flags": [
                    {
                        "key": "mediainfo_unavailable",
                        "label": "MediaInfo Error",
                        "severity": "blocker",
                        "detail": "Whackamole could not read QUI MediaInfo.",
                    }
                ],
            },
        )

        errors_api = client.get("/api/items?status=error", headers=_auth_headers())
        manual_api = client.get("/api/items?status=manual_review", headers=_auth_headers())
        errors_page = client.get("/dashboard?view=errors")
        manual_page = client.get("/dashboard?view=manual")

        assert errors_api.status_code == 200
        assert errors_api.json()["items"][0]["name"] == "Example.Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP"
        assert {"key": "media_info", "label": "Media Info", "severity": "critical"} in errors_api.json()["items"][0]["alert_tags"]
        assert manual_api.json()["items"] == []
        assert "Example.Show.S01E01" in errors_page.text
        assert "Example.Show.S01E01" not in manual_page.text


def test_dashboard_suppresses_generic_manual_review_alert(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        client.app.state.db.update_status(
            item_id,
            "manual_review",
            "manual_review",
            "Needs manual review.",
            check_results={"flags": [{"key": "manual_review", "label": "Manual Review", "severity": "warning"}]},
        )

        response = client.get("/api/items?status=manual_review", headers=_auth_headers())

        labels = {tag["label"] for tag in response.json()["items"][0]["alert_tags"]}
        assert "Manual Review" not in labels


def test_dashboard_list_does_not_build_detail_release_views(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_item(client)

        def fail_detail_builder(*args, **kwargs):
            raise AssertionError("detail release views should not be built for dashboard rows")

        monkeypatch.setattr(main_module, "_arr_release_views", fail_detail_builder)

        page = client.get("/dashboard?view=candidates")

        assert page.status_code == 200
        assert "Example.Show.S01E01" in page.text


def test_dashboard_list_uses_lean_rows_without_detail_presentation_builders(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_item(client)

        def fail_detail_builder(*args, **kwargs):
            raise AssertionError("detail presentation builders should not run for dashboard rows")

        for name in ("_overview_checks", "_next_action", "_decision_label", "_coverage_status"):
            monkeypatch.setattr(main_module, name, fail_detail_builder)

        page = client.get("/dashboard?view=candidates")
        row = main_module._dashboard_row_dict(
            client.app.state.db.list_dashboard_items_filtered(["candidate"], limit=1)[0]
        )

        assert page.status_code == 200
        assert '<span class="pill status-ready">Ready</span>' in page.text
        assert "Upload" in page.text
        assert row["decision_notice"] == "Valid upload candidate on: IHD"
        assert row["source_label"] == "IHD"
        assert {"key": "IHD", "label": "IHD", "state": "valid"} in row["tracker_coverage"]
        assert {"key": "source", "label": "Source", "severity": "warning"} in row["alert_tags"]
        assert {"next_action", "decision_label", "cross_check", "coverage_status", "overview_checks"}.isdisjoint(row)


def test_dashboard_lean_row_uses_parsed_decision_for_status_and_upload(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        client.app.state.db.update_check_results(item_id, {"decision": {"status": "blocked"}})

        page = client.get("/dashboard?view=all")

        assert page.status_code == 200
        assert '<span class="pill status-covered">Blocked</span>' in page.text
        assert 'title="Upload" aria-label="Upload" disabled' in page.text


def test_dashboard_lean_row_uses_mediainfo_provider_for_source_badge(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        client.app.state.db.update_check_results(
            item_id,
            {"media": {"mediainfo_files": [{"name": "example.mkv", "traits": {"source_provider": "Netflix"}}]}},
        )

        row = main_module._dashboard_row_dict(
            client.app.state.db.list_dashboard_items_filtered(["candidate"], limit=1)[0]
        )

        assert "Source" not in {tag["label"] for tag in row["alert_tags"]}


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


def test_filtered_recheck_endpoint_requeues_errors_view(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "errored-filter",
                "name": "Errored.Show.S01E01.1080p.WEB-DL-GRP",
                "category": "tv",
                "tags": "",
                "content_path": "/media/torrents/tv/Errored.Show.S01E01.1080p.WEB-DL-GRP",
                "progress": 1,
            },
            status="error",
            baseline=False,
        )
        item_id = int(db.list_items(["error"], limit=1)[0]["id"])

        page = client.get("/dashboard?view=errors")
        response = client.post(
            "/items/recheck-filtered",
            data={"view": "errors", "media": "episode"},
            follow_redirects=False,
        )
        row = db.get_item(item_id)

        assert page.status_code == 200
        assert 'name="view" value="errors"' in page.text
        assert 'class="button secondary run-check-button" type="submit">' in page.text
        assert "Run check on found set" in page.text
        assert response.status_code == 303
        assert response.headers["location"].startswith("/dashboard?view=errors")
        assert row["status"] == "queued"
        assert row["reason"] == "Bulk recheck requested from error filtered set"


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
        assert '<th data-column-key="decision">Decision</th>' not in page.text
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
        assert "UA" in {tag["label"] for tag in api_response.json()["items"][0]["alert_tags"]}
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
        status_response = client.get("/ui-api/status")
        clear = client.post("/service-errors/clear", data={"return_to": "/"}, follow_redirects=False)

        assert page.status_code == 200
        assert "Service events" in page.text
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


def test_item_detail_renders_rename_tab_and_api_display_model(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        local = "Example.Show.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-HONE"
        remote = "Example.Series.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-HONE"
        client.app.state.db.update_status(
            item_id,
            "manual_review",
            "renamed_release_warning",
            "Arr found a same-group release on IHD in the same scope with a different release title.",
            check_results={
                "version": 1,
                "rename_detection": {
                    "version": 1,
                    "status": "manual_review",
                    "confidence": "high",
                    "reason": "Arr found a same-group release on IHD in the same scope with a different release title.",
                    "evidence": [
                        {
                            "kind": "same_group_arr_title_mismatch",
                            "scope": "arr_title",
                            "confidence": "high",
                            "source": "Discovarr",
                            "tracker": "IHD",
                            "local_title": local,
                            "remote_title": remote,
                            "release_group": "HONE",
                            "local_key": "exampleshows01e011080pamznwebdlddp51h264hone",
                            "remote_key": "exampleseriess01e011080pamznwebdlddp51h264hone",
                            "local_scope": {"season": 1, "episode": 1, "resolution": "1080p"},
                            "remote_scope": {"season": 1, "episode": 1, "resolution": "1080p"},
                            "reason": "Arr found a same-group release on IHD in the same scope with a different release title.",
                        }
                    ],
                },
            },
        )

        detail = client.get(f"/api/items/{item_id}", headers=_auth_headers())
        page = client.get(f"/items/{item_id}")

        assert detail.status_code == 200
        rename_check = detail.json()["rename_check"]
        assert rename_check["rows"][0]["tracker"] == "IHD"
        assert rename_check["rows"][0]["local_value"] == local
        assert rename_check["rows"][0]["remote_value"] == remote
        assert detail.json()["checks"]["rename_detection"]["evidence"][0]["kind"] == "same_group_arr_title_mismatch"
        assert page.status_code == 200
        assert 'data-tab-target="rename"' in page.text
        assert "Our record" in page.text
        assert "Their / expected record" in page.text
        assert "Arr found a same-group release on IHD" in page.text
        assert "Example.Show.S01E01" in page.text
        assert "Example.Series.S01E01" in page.text
        assert "rename-diff-replace" in page.text


def test_item_detail_renders_empty_title_token_rename_files(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        filename = "Louis Theroux - Inside the Manosphere (2026) (2160p NF WEB-DL Hybrid H265 DV HDR DDP Atmos 5.1 English - HONE).mkv"
        reason = f"{filename} contains an empty title token in the human-readable title area."
        client.app.state.db.update_status(
            item_id,
            "manual_review",
            "renamed_release_warning",
            reason,
            check_results={
                "version": 1,
                "rename_detection": {
                    "version": 1,
                    "status": "manual_review",
                    "confidence": "high",
                    "reason": reason,
                    "evidence": [
                        {
                            "kind": "empty_title_token",
                            "scope": "file",
                            "confidence": "high",
                            "source": "video_file",
                            "value": filename,
                            "expected": "",
                            "reason": reason,
                        }
                    ],
                },
            },
        )

        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert "Empty title token" in page.text
        assert "Doubled or mixed separators" in page.text
        assert "space + hyphen + space -> hyphen" in page.text
        assert "Suggested filename" in page.text
        assert "Louis Theroux-Inside the Manosphere" in page.text
        assert "Affected files" in page.text
        assert filename in page.text


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
        attempted = client.post(f"/api/reports/{report_id}/attempt", headers=_auth_headers())
        active_after_attempt = client.get("/api/reports", headers=_auth_headers())
        attempted_list = client.get("/api/reports?state=attempted", headers=_auth_headers())
        resolved = client.post(f"/api/reports/{report_id}/resolve", headers=_auth_headers())
        active_after_resolve = client.get("/api/reports", headers=_auth_headers())
        resolved_list = client.get("/api/reports?state=resolved", headers=_auth_headers())
        deleted = client.delete(f"/api/reports/{report_id}", headers=_auth_headers())
        missing = client.get(f"/api/reports/{report_id}", headers=_auth_headers())
        deleted_attempt = client.post(f"/api/reports/{report_id}/attempt", headers=_auth_headers())

        assert created.status_code == 201
        assert created.json()["report"]["stage"] == "MediaInfo"
        assert active.json()["count"] == 1
        assert detail.json()["report"]["notes"] == "Audio tags look wrong"
        assert attempted.status_code == 200
        assert attempted.json()["report"]["state"] == "attempted"
        assert active_after_attempt.json()["count"] == 0
        assert attempted_list.json()["count"] == 1
        assert resolved.status_code == 200
        assert active_after_resolve.json()["count"] == 0
        assert resolved_list.json()["count"] == 1
        assert deleted.status_code == 200
        assert missing.status_code == 404
        assert deleted_attempt.status_code == 404


def test_rejected_action_moves_candidate_item_and_creates_report(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)
        db = client.app.state.db

        page = client.get(f"/items/{item_id}")
        response = client.post(
            f"/items/{item_id}/reject",
            data={"stage": "Rename Check", "notes": "Moderator rejected: renamed episode title", "return_to": f"/items/{item_id}#reporting"},
            follow_redirects=False,
        )
        rejected_item_page = client.get(f"/items/{item_id}")
        rejected_page = client.get("/dashboard?view=rejected")
        rejected_api = client.get("/api/items?status=rejected", headers=_auth_headers())
        reports = client.get("/api/reports", headers=_auth_headers())

        row = db.get_item(item_id)

        assert page.status_code == 200
        assert 'data-tab-target="reporting"' in page.text
        assert f'action="/items/{item_id}/reject"' in page.text
        assert "Reject item" in page.text
        assert "Rejection stage" in page.text
        assert "Rejection reason" in page.text
        assert ">Mark rejected</button>" in page.text
        assert response.status_code == 303
        assert row["status"] == "rejected"
        assert row["verdict"] == "moderation_rejected"
        assert "renamed episode title" in row["reason"]
        assert f'action="/items/{item_id}/reject"' not in rejected_item_page.text
        assert rejected_page.status_code == 200
        assert "Example.Show.S01E01" in rejected_page.text
        assert rejected_api.json()["items"][0]["status"] == "rejected"
        assert reports.json()["reports"][0]["stage"] == "Rename Check"
        assert reports.json()["reports"][0]["notes"] == "Moderator rejected: renamed episode title"


def test_item_page_renders_reporting_tab_actions_and_removed_tabs(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        report_id = client.app.state.db.create_report(item_id, "Example Item", "MediaInfo", "Audio tags look wrong")
        client.app.state.db.mark_report_attempted(report_id)
        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert 'data-tab-target="reporting"' in page.text
        assert "Flag Error" in page.text
        assert "Processing Stage" in page.text
        assert "Attempted Reports" in page.text
        assert "Audio tags look wrong" in page.text
        assert 'data-tab-target="checks"' not in page.text
        assert 'data-tab-target="trackers"' not in page.text
        assert ">Checks<" not in page.text
        assert ">Trackers<" not in page.text
        assert 'aria-label="Upload"' in page.text
        assert 'data-submit-tick="Recheck triggered"' in page.text
        assert 'data-submit-tick="Upload queued"' in page.text
        assert "data-queue-upload-form" in page.text
        assert f'data-queue-url="/ui-api/items/{item_id}/upload-assistant/queue"' in page.text
        assert f'value="/items/{item_id}?' in page.text
        assert f'value="/items/{item_id}#upload-assistant"' not in page.text
        assert 'aria-label="Next found item"' in page.text
        assert ">Size<" not in page.text


def test_reports_page_groups_duplicates_and_sidebar_counts_open_reports(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        db = client.app.state.db
        first = db.create_report(item_id, "Example Item", "MediaInfo", "Audio tags look wrong")
        second = db.create_report(item_id, "Example Item", "MediaInfo", " Audio   tags look wrong ")
        third = db.create_report(item_id, "Example Item", "Upload Assistant", "Prompt hung")

        page = client.get("/reports")

        assert page.status_code == 200
        assert "Reports" in page.text
        assert "3" in page.text
        assert "2 duplicates" in page.text
        assert f'name="report_ids" value="{first}"' in page.text
        assert f'name="report_ids" value="{second}"' in page.text
        assert f'name="report_ids" value="{third}"' in page.text
        assert "Prompt hung" in page.text


def test_reports_page_splits_active_tracker_moderation_and_rejected_views(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        db = client.app.state.db
        db.create_report(item_id, "Example Item", "MediaInfo", "Audio tags look wrong")
        db.create_report(item_id, "Example Item", "Tracker Moderation", "Renamed file")
        db.insert_discovered(
            2,
            {
                "hash": "rejected123",
                "name": "Rejected.Movie.2026.1080p.WEB-DL.DDP5.1.H.264-GRP",
                "category": "movies",
                "tags": "upload",
                "content_path": "/media/torrents/movies/rejected.mkv",
                "size": 123456789,
            },
            status="queued",
            baseline=False,
        )
        rejected_item_id = next(
            int(row["id"]) for row in db.list_items([], limit=10) if row["name"].startswith("Rejected.Movie")
        )
        db.reject_item(rejected_item_id, "Upload Assistant", "Rejected by moderator")

        active = client.get("/reports")
        tracker = client.get("/reports?state=tracker_moderation")
        rejected = client.get("/reports?state=rejected")

        assert active.status_code == 200
        assert 'href="/reports?state=tracker_moderation"' in active.text
        assert 'href="/reports?state=rejected"' in active.text
        assert "Audio tags look wrong" in active.text
        assert "Renamed file" not in active.text
        assert "Rejected by moderator" not in active.text
        assert "Renamed file" in tracker.text
        assert "Audio tags look wrong" not in tracker.text
        assert "Rejected by moderator" in rejected.text


def test_report_group_attempt_form_marks_duplicates_attempted(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_item(client)
        db = client.app.state.db
        first = db.create_report(item_id, "Example Item", "MediaInfo", "Audio tags look wrong")
        second = db.create_report(item_id, "Example Item", "MediaInfo", "Audio tags look wrong")

        response = client.post(
            "/reports/attempt",
            data={"report_ids": [str(first), str(second)], "return_to": "/reports?state=active"},
            follow_redirects=False,
        )
        attempted = client.get("/reports?state=attempted")

        assert response.status_code == 303
        assert response.headers["location"] == "/reports?state=active"
        assert db.report_counts()["active"] == 0
        assert db.report_counts()["attempted"] == 2
        assert "2 duplicates" in attempted.text


def test_item_overview_shortens_source_not_required_status(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "bluray-source-not-required",
                "name": "Example.Movie.2026.1080p.BluRay.x264-GRP",
                "category": "movies",
                "tags": "",
                "content_path": "/media/torrents/movies/Example.Movie.2026.1080p.BluRay.x264-GRP.mkv",
                "size": 123456789,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])
        db.update_status(
            item_id,
            "candidate",
            "candidate",
            "Valid upload candidate on: IHD",
            tracker_results={"passed": ["IHD"], "dupe": [], "skipped": [], "error": []},
            increment_attempt=True,
        )

        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert "Source Detection" in page.text
        assert "Not Required" not in page.text
        assert "Source Not Required" not in page.text


def test_item_detail_recomputes_source_summary_after_title_provider_enrichment(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "amazon-title-source",
                "name": "24.Hours.in.Police.Custody.S01.1080p.Amazon.WEB-DL.DD+.2.0.x264-TrollHD",
                "category": "tv",
                "tags": "",
                "content_path": "/media/torrents/tv/24.Hours.in.Police.Custody.S01.1080p.Amazon.WEB-DL.DD+.2.0.x264-TrollHD",
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])
        db.update_status(
            item_id,
            "covered",
            "covered",
            "Covered in QUI: DP",
            tracker_results={"passed": [], "covered": ["DP"], "dupe": [], "skipped": [], "error": []},
            arr_results={
                "status": "covered",
                "local_traits": {"source": "web", "source_tag": "WEB-DL", "source_provider": "", "rip_type": "web-dl"},
                "decisions": [{"tracker": "DP", "status": "covered", "reason": "Tracker coverage is now present in QUI."}],
            },
            check_results={
                "media": {"status": "confirmed", "local_traits": {"source": "web", "source_tag": "WEB-DL", "source_provider": "", "rip_type": "web-dl"}},
                "flags": [
                    {
                        "key": "web_source_missing",
                        "label": "Source Missing",
                        "severity": "warning",
                        "detail": "Detected WEB-DL/WEBRip but no streaming service provider is known yet.",
                    }
                ],
            },
        )

        page = client.get(f"/items/{item_id}")

        assert page.status_code == 200
        assert "Source: AMZN" in page.text
        assert "Source Missing" not in page.text
        assert "Web Source" not in page.text


def test_high_quality_trackers_default_empty_and_cross_check_setting(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.secrets.set("whackamole_api_token", API_TOKEN)
        item_id = _seed_item(client)

        default_response = client.get(f"/api/items/{item_id}", headers=_auth_headers())
        save = client.post("/config", data={"high_quality_trackers": "IHD"})
        valid_only_response = client.get(f"/api/items/{item_id}", headers=_auth_headers())
        client.app.state.db.insert_discovered(
            1,
            {
                "hash": "ihd-covered",
                "name": "Example.Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP",
                "category": "uploads",
                "tags": "upload",
                "content_path": "/media/torrents/uploads/IHD/Example.Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP",
                "progress": 1,
            },
            status="inventory",
            baseline=True,
        )
        covered_response = client.get(f"/api/items/{item_id}", headers=_auth_headers())

        assert default_response.json()["cross_check"]["selected"] == []
        assert default_response.json()["cross_check"]["label"] == "Not Validated"
        assert save.status_code == 200
        assert "High Quality Trackers" in save.text
        assert valid_only_response.json()["cross_check"]["selected"] == ["IHD"]
        assert valid_only_response.json()["cross_check"]["label"] == "Not Validated"
        assert covered_response.json()["cross_check"]["selected"] == ["IHD"]
        assert covered_response.json()["cross_check"]["label"] == "Validated On High Quality Tracker"


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
        assert "Example.Show.S01E01.1080p.WEB-DL-GRP.mkv" not in page.text
        assert "Behind.The.Scenes.mp4" not in page.text
        assert "Sample.txt" not in page.text
        assert f"/items/{item_id}/rename-video-file" not in page.text


def test_item_video_file_rename_requeues_item(tmp_path, monkeypatch):
    media_dir = tmp_path / "media" / "Example.Movie.2026.1080p.WEB-DL-GRP"
    media_dir.mkdir(parents=True)
    old_file = media_dir / "3uz7j4imwRaC.mkv"
    old_file.write_bytes(b"movie")
    new_file = media_dir / "Example.Movie.2026.1080p.WEB-DL-GRP.mkv"

    with _client(tmp_path / "config", monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "rename-file",
                "name": "Example.Movie.2026.1080p.WEB-DL-GRP",
                "category": "movies",
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
            "possible_renamed_release",
            "Needs filename correction",
            mapped_path=str(media_dir),
            tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
            arr_results={},
            increment_attempt=True,
        )

        response = client.post(
            f"/items/{item_id}/rename-video-file",
            data={
                "old_path": str(old_file),
                "new_name": new_file.name,
                "return_to": f"/items/{item_id}#overview",
            },
            follow_redirects=False,
        )
        row = db.get_item(item_id)

        assert response.status_code == 303
        assert not old_file.exists()
        assert new_file.exists()
        assert row["status"] == "queued"


def test_item_video_file_rename_rejects_paths_outside_item(tmp_path, monkeypatch):
    media_dir = tmp_path / "media" / "Example.Movie.2026.1080p.WEB-DL-GRP"
    other_dir = tmp_path / "media" / "Other"
    media_dir.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    listed_file = media_dir / "Example.Movie.2026.1080p.WEB-DL-GRP.mkv"
    other_file = other_dir / "Other.mkv"
    listed_file.write_bytes(b"movie")
    other_file.write_bytes(b"other")

    with _client(tmp_path / "config", monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "reject-rename",
                "name": "Example.Movie.2026.1080p.WEB-DL-GRP",
                "category": "movies",
                "tags": "",
                "content_path": str(media_dir),
                "progress": 1,
            },
            status="queued",
            baseline=False,
        )
        item_id = int(db.list_items([], limit=1)[0]["id"])

        response = client.post(
            f"/items/{item_id}/rename-video-file",
            data={"old_path": str(other_file), "new_name": "Renamed.mkv"},
        )

        assert response.status_code == 404
        assert other_file.exists()


def test_no_video_error_item_renders_and_serializes(tmp_path, monkeypatch):
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
            "error",
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
        assert api_response.json()["status"] == "error"
        assert api_response.json()["verdict"] == "no_video_files"
        assert api_response.json()["reason"] == reason
        assert page_response.status_code == 200
        assert reason in page_response.text


def test_disallowed_item_path_is_safe_for_page_and_detail_api(tmp_path, monkeypatch):
    monkeypatch.setenv("WHACKAMOLE_ALLOWED_MEDIA_ROOTS", str(tmp_path / "allowed"))
    monkeypatch.setenv("WHACKAMOLE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("WHACKAMOLE_API_TOKEN", API_TOKEN)
    with TestClient(app, headers=_auth_headers(), raise_server_exceptions=False) as client:
        item_id = _seed_item(client)

        page_response = client.get(f"/items/{item_id}")
        api_response = client.get(f"/api/items/{item_id}", headers=_auth_headers())

        assert page_response.status_code == 200
        assert api_response.status_code == 200
        assert "outside configured media roots" in page_response.text.lower()
        assert "outside configured media roots" in str(api_response.json()).lower()


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


def test_dashboard_active_view_hides_waiting_retries_and_errors_are_terminal(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        now = int(time.time())
        for torrent_hash, name in [
            ("due-retry", "Due.Retry.Show.S01E01.1080p.WEB-DL-GRP"),
            ("future-retry", "Future.Retry.Show.S01E01.1080p.WEB-DL-GRP"),
            ("terminal-error", "Terminal.Error.Show.S01E01.1080p.WEB-DL-GRP"),
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
        db.update_status(int(rows["due-retry"]["id"]), "retry", "ua_error", "Due now", next_check_at=now - 1)
        db.update_status(int(rows["future-retry"]["id"]), "retry", "ua_error", "Waiting", next_check_at=now + 3600)
        db.update_status(int(rows["terminal-error"]["id"]), "error", "path_mapping", "Investigate")

        active = client.get("/dashboard?view=active")
        errors = client.get("/dashboard?view=errors")

        assert active.status_code == 200
        assert "Due.Retry.Show" in active.text
        assert "Future.Retry.Show" not in active.text
        assert "Terminal.Error.Show" not in active.text
        assert errors.status_code == 200
        assert "Terminal.Error.Show" in errors.text
        assert "Due.Retry.Show" not in errors.text
