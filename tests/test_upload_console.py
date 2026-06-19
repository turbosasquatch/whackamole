import asyncio
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig, SecretStore
from app.database import Database
from app.main import app
from app.service import WhackamoleService
from app.ua_execution import UaExecutionCoordinator, UaExecutionOwner, UploadConsoleManager, sse_payload


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("WHACKAMOLE_CONFIG_DIR", str(tmp_path))
    return TestClient(app)


def _seed_candidate(client: TestClient, name: str = "Movie.2026.1080p.WEB-DL.DDP5.1.H.264-GRP") -> int:
    db = client.app.state.db
    db.insert_discovered(
        1,
        {
            "hash": "upload-console-hash",
            "name": name,
            "category": "movies",
            "tags": "",
            "content_path": "/media/torrents/movies/Movie.2026.1080p.WEB-DL.DDP5.1.H.264-GRP",
            "size": 1234,
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
        "Valid upload candidate on: DP, ULCX",
        mapped_path="/ua/movies/Movie.2026.1080p.WEB-DL.DDP5.1.H.264-GRP",
        tracker_results={"passed": ["DP", "ULCX"], "dupe": [], "skipped": [], "error": []},
        arr_results={
            "status": "candidate",
            "decisions": [
                {"tracker": "DP", "status": "candidate", "reason": "ok"},
                {"tracker": "ULCX", "status": "candidate", "reason": "ok"},
            ],
        },
        check_results={
            "version": 1,
            "media": {"local_traits": {"rip_type": "web-dl", "source_tag": "WEB-DL"}},
            "nfo": {"content": "Network: Amazon Prime Video", "path": "release.nfo", "source": "test"},
            "release_group_policy": {"candidate_trackers": ["DP", "ULCX"], "blocked_trackers": []},
            "flags": [],
        },
    )
    return item_id


def test_item_page_upload_console_prefills_trackers_and_missing_service(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.config_manager.load().upload_assistant.url = "http://ua"
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)

        response = client.get(f"/items/{item_id}#upload-assistant")

        assert response.status_code == 200
        assert "Upload Assistant" in response.text
        assert "data-upload-autorun" in response.text
        assert "data-upload-queue" in response.text
        assert '--trackers dp,ulcx --service AMZN' in response.text


def test_upload_console_queue_endpoint_enqueues_unattended_import_when_lock_busy(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)
        client.app.state.ua_execution._owner = UaExecutionOwner(
            id="check-lock",
            kind="check",
            label="Checking item 99",
            item_id=99,
            session_id="check-session",
            started_at=123,
        )

        response = client.post(f"/api/items/{item_id}/upload-assistant/queue", json={"args": "--trackers dp"})
        imports_page = client.get("/imports")

        assert response.status_code == 200
        assert response.json()["args"] == "--trackers dp --unattended"
        rows = client.app.state.db.list_imports()
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        assert rows[0]["args"] == "--trackers dp --unattended"
        assert imports_page.status_code == 200
        assert "Queued Imports" in imports_page.text
        assert "Run Pending Imports" in imports_page.text
        assert "Run pending imports" not in imports_page.text
        assert "--trackers dp --unattended" in imports_page.text


def test_upload_console_queue_endpoint_waits_for_manual_import_run(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)

        async def fail_auto_run():
            raise AssertionError("queue endpoint should not auto-run pending imports")

        client.app.state.service.run_queued_import = fail_auto_run

        response = client.post(f"/api/items/{item_id}/upload-assistant/queue", json={"args": "--trackers dp"})

        assert response.status_code == 200
        rows = client.app.state.db.list_imports()
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"


def test_upload_console_queue_endpoint_reuses_active_import(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)
        existing_id = client.app.state.db.enqueue_import(
            item_id=item_id,
            item_name="Movie.2026.1080p.WEB-DL.DDP5.1.H.264-GRP",
            path="/ua/movies/Movie.2026.1080p.WEB-DL.DDP5.1.H.264-GRP",
            args="--trackers dp --unattended",
        )

        response = client.post(f"/api/items/{item_id}/upload-assistant/queue", json={"args": "--trackers ulcx"})

        assert response.status_code == 200
        assert response.json()["id"] == existing_id
        assert response.json()["already_queued"] is True
        assert len(client.app.state.db.list_imports()) == 1


def test_upload_console_queue_endpoint_uses_default_args_when_payload_omits_args(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)

        response = client.post(f"/api/items/{item_id}/upload-assistant/queue", json={})

        assert response.status_code == 200
        assert response.json()["args"] == "--trackers dp,ulcx --service AMZN --unattended"
        rows = client.app.state.db.list_imports()
        assert rows[0]["args"] == "--trackers dp,ulcx --service AMZN --unattended"


def test_upload_console_queue_endpoint_uses_default_args_when_payload_args_is_null(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)

        response = client.post(f"/api/items/{item_id}/upload-assistant/queue", json={"args": None})

        assert response.status_code == 200
        assert response.json()["args"] == "--trackers dp,ulcx --service AMZN --unattended"
        rows = client.app.state.db.list_imports()
        assert rows[0]["args"] == "--trackers dp,ulcx --service AMZN --unattended"


def test_upload_console_allows_folder_name_that_would_be_normalized(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        media_dir = tmp_path / "Dirty Business 2026 S01 1080p ALL4 WEB-DL AAC2 0 H 264-RAWR"
        media_dir.mkdir()
        (media_dir / "Dirty.Business.2026.S01E01.1080p.ALL4.WEB-DL.AAC2.0.H.264-RAWR.mkv").write_text("x", encoding="utf-8")
        (media_dir / "Dirty.Business.2026.S01E02.1080p.ALL4.WEB-DL.AAC2.0.H.264-RAWR.mkv").write_text("x", encoding="utf-8")
        item_id = _seed_candidate(client, name=media_dir.name)
        client.app.state.db.update_status(item_id, "candidate", "candidate", "Valid upload candidate on: DP", mapped_path=str(media_dir))

        page = client.get(f"/items/{item_id}#upload-assistant")
        response = client.post(f"/api/items/{item_id}/upload-assistant/queue", json={"args": "--trackers dp"})

        assert page.status_code == 200
        assert "Folder name would be normalised to Dirty.Business.2026.S01.1080p.ALL4.WEB-DL.AAC2.0.H.264-RAWR." in page.text
        assert "Rename Check" in page.text
        assert '<strong>Rename Check</strong>' in page.text
        assert '<span class="check-state pass">Pass</span>' in page.text
        assert "Review Required" not in page.text
        assert "Queue Upload" in page.text
        assert 'data-can-queue="true"' in page.text
        assert response.status_code == 200
        assert response.json()["args"] == "--trackers dp --unattended"


def test_upload_console_allows_possible_renamed_release_flag(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)
        client.app.state.db.update_status(
            item_id,
            "candidate",
            "candidate",
            "Arr found a same-group release with a different release title.",
            check_results={
                "version": 1,
                "media": {"local_traits": {"rip_type": "web-dl", "source_tag": "WEB-DL", "source_provider": "AMZN"}},
                "release_group_policy": {"candidate_trackers": ["DP"], "blocked_trackers": []},
                "flags": [
                    {
                        "key": "possible_renamed_release",
                        "label": "Possible renamed release",
                        "severity": "warning",
                        "detail": "Arr found a same-group release with a different release title.",
                    }
                ],
            },
        )

        page = client.get(f"/items/{item_id}#upload-assistant")
        response = client.post(f"/api/items/{item_id}/upload-assistant/queue", json={"args": "--trackers dp"})

        assert page.status_code == 200
        assert "Possible renamed release: review the tracker title before uploading." in page.text
        assert 'data-can-execute="true"' in page.text
        assert 'data-can-queue="true"' in page.text
        assert response.status_code == 200
        assert response.json()["args"] == "--trackers dp --unattended"


def test_item_queue_upload_form_stays_on_item_page(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)

        response = client.post(
            f"/items/{item_id}/upload-assistant/queue",
            data={"return_to": f"/items/{item_id}"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == f"/items/{item_id}"
        assert client.app.state.db.list_imports()[0]["item_id"] == item_id
        assert client.app.state.db.list_imports()[0]["args"] == "--trackers dp,ulcx --service AMZN --unattended"


def test_imports_run_pending_button_triggers_runner(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.app.state.db.enqueue_import(
            item_id=42,
            item_name="Queued.Movie.2026.1080p.NF.WEB-DL-GRP",
            path="/ua/movie.mkv",
            args="--trackers dp --unattended",
        )
        calls = []

        async def fake_run_pending():
            calls.append(True)
            return True

        client.app.state.service.request_queued_import_run = fake_run_pending

        response = client.post("/imports/run-pending", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/imports?view=queue&page=1"
        assert calls == [True]


def test_imports_page_defaults_to_queue_and_filters_statuses(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.enqueue_import(1, "Running.Movie.2026.1080p.WEB-DL-GRP", "/ua/running.mkv", "--unattended")
        db.claim_next_import("running-session")
        db.enqueue_import(2, "Pending.Movie.2026.1080p.WEB-DL-GRP", "/ua/pending.mkv", "--unattended")
        error_id = db.enqueue_import(3, "Error.Movie.2026.1080p.WEB-DL-GRP", "/ua/error.mkv", "--unattended")
        complete_id = db.enqueue_import(4, "Complete.Movie.2026.1080p.WEB-DL-GRP", "/ua/complete.mkv", "--unattended")
        cancelled_id = db.enqueue_import(5, "Cancelled.Movie.2026.1080p.WEB-DL-GRP", "/ua/cancelled.mkv", "--unattended")
        db.mark_import_error(error_id, "failed")
        db.mark_import_complete(complete_id, "complete")
        assert db.cancel_import(cancelled_id) is True

        page = client.get("/imports")

        assert page.status_code == 200
        assert "Pending.Movie.2026" in page.text
        assert "Running.Movie.2026" in page.text
        assert "Error.Movie.2026" not in page.text
        assert "Complete.Movie.2026" not in page.text
        assert "Cancelled.Movie.2026" not in page.text
        assert f'href="/items/2#upload-assistant"' in page.text
        assert f'href="/items/1#upload-assistant"' in page.text
        assert " pending</span>" not in page.text
        assert " running</span>" not in page.text


def test_import_tabs_show_expected_statuses_and_cancel_actions(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        error_id = db.enqueue_import(10, "Error.Movie.2026.1080p.WEB-DL-GRP", "/ua/error.mkv", "--unattended")
        complete_id = db.enqueue_import(11, "Complete.Movie.2026.1080p.WEB-DL-GRP", "/ua/complete.mkv", "--unattended")
        cancelled_id = db.enqueue_import(12, "Cancelled.Movie.2026.1080p.WEB-DL-GRP", "/ua/cancelled.mkv", "--unattended")
        db.mark_import_error(error_id, "failed")
        db.mark_import_complete(complete_id, "complete")
        assert db.cancel_import(cancelled_id) is True

        error_page = client.get("/imports?view=error")
        complete_page = client.get("/imports?view=complete")
        cancelled_page = client.get("/imports?view=cancelled")

        assert "Error.Movie.2026" in error_page.text
        assert f'action="/imports/{error_id}/cancel"' in error_page.text
        assert "Complete.Movie.2026" in complete_page.text
        assert f'action="/imports/{complete_id}/cancel"' not in complete_page.text
        assert "Cancelled.Movie.2026" in cancelled_page.text
        assert f'action="/imports/{cancelled_id}/cancel"' not in cancelled_page.text
        assert "Cancelled" in cancelled_page.text
        assert not re.search(r">\s*Cancelled\s*<span class=\"button-count\"", cancelled_page.text)


def test_import_cancellation_rules_and_claim_skip_cancelled(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        running_id = db.enqueue_import(3, "Running", "/ua/running.mkv", "")
        db.claim_next_import("running-session")
        pending_id = db.enqueue_import(1, "Pending", "/ua/pending.mkv", "")
        error_id = db.enqueue_import(2, "Error", "/ua/error.mkv", "")
        complete_id = db.enqueue_import(4, "Complete", "/ua/complete.mkv", "")
        db.mark_import_error(error_id, "failed")
        db.mark_import_complete(complete_id, "complete")

        assert db.cancel_import(pending_id) is True
        assert db.cancel_import(error_id) is True
        assert db.cancel_import(running_id) is False
        assert db.cancel_import(complete_id) is False
        assert db.cancel_import(999999) is False
        assert db.claim_next_import("after-cancel") is None
        assert db.queued_import_counts()["cancelled"] == 2
        assert db.count_imports(["cancelled"]) == 2


def test_cancelled_import_does_not_block_fresh_queue(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)
        first_id = client.app.state.db.enqueue_import(
            item_id=item_id,
            item_name="Movie.2026.1080p.WEB-DL.DDP5.1.H.264-GRP",
            path="/ua/movie.mkv",
            args="--trackers dp --unattended",
        )
        assert client.app.state.db.cancel_import(first_id) is True

        response = client.post(f"/api/items/{item_id}/upload-assistant/queue", json={"args": "--trackers dp"})
        rows = client.app.state.db.list_imports()

        assert response.status_code == 200
        assert response.json().get("already_queued") is not True
        assert [row["status"] for row in rows] == ["pending", "cancelled"]


def test_import_pagination_and_out_of_range_redirect(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        for index in range(51):
            db.enqueue_import(index + 1, f"Queued.Movie.{index:02d}.2026.1080p.WEB-DL-GRP", f"/ua/{index}.mkv", "--unattended")

        first_page = client.get("/imports?view=queue&page=1")
        second_page = client.get("/imports?view=queue&page=2")
        out_of_range = client.get("/imports?view=queue&page=99", follow_redirects=False)

        assert first_page.status_code == 200
        assert "Next" in first_page.text
        assert "Queued.Movie.00" in first_page.text
        assert "Queued.Movie.50" not in first_page.text
        assert "Queued.Movie.50" in second_page.text
        assert out_of_range.status_code == 303
        assert out_of_range.headers["location"] == "/imports?view=queue&page=2"


def test_cancel_import_redirects_to_valid_page_after_last_row_removed(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        for index in range(51):
            import_id = db.enqueue_import(index + 1, f"Failed.Movie.{index:02d}.2026.1080p.WEB-DL-GRP", f"/ua/{index}.mkv", "--unattended")
            db.mark_import_error(import_id, "failed")

        response = client.post("/imports/51/cancel", data={"view": "error", "page": "2"}, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/imports?view=error&page=1"


def test_notification_polling_updates_service_events():
    script = Path("app/static/app.js").read_text()
    template = Path("app/templates/base.html").read_text()

    assert "function renderNotifications" in script
    assert "renderNotifications(service.service_errors || [])" in script
    assert "[data-notification-count]" in script
    assert "data-notification-list" in template
    assert "Service events" in template
    assert "Service errors" not in template


def test_mobile_import_cards_use_two_column_fact_grid():
    styles = Path("app/static/style.css").read_text()
    template = Path("app/templates/imports.html").read_text()

    assert "imports-cards" in template
    assert "import-card-facts" in template
    assert ".imports-panel {\n  display: grid;\n  gap: 12px;" in styles
    assert ".import-tabs" in styles
    assert ".import-tabs .button" in styles
    assert "gap: 0.45rem;" in styles
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in styles
    assert ".import-card" in styles
    assert "padding: 14px;" in styles
    assert ".import-card-facts" in styles
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in styles
    assert ".imports-table-wrap {\n    display: none;" in styles


def test_upload_console_full_snapshots_are_not_terminal_replacements():
    script = Path("app/static/app.js").read_text()

    assert "lastFullSnapshotText" in script
    assert 'payload.type === "html_full"' in script
    assert 'if (replace) output.innerHTML = "";' not in script


def test_submit_buttons_have_tick_feedback_script():
    script = Path("app/static/app.js").read_text()

    assert "function setButtonTick" in script
    assert "form[data-submit-tick]" in script
    assert "&#10003;" in script


def test_queue_button_errors_do_not_replace_label_with_full_message():
    script = Path("app/static/app.js").read_text()

    assert "button.textContent = error.message" not in script
    assert "form.dataset.submitErrorLabel" in script
    assert "button.title = message" in script


def test_item_page_upload_console_does_not_duplicate_service_when_title_has_it(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_candidate(client, name="Movie.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP")

        response = client.get(f"/items/{item_id}#upload-assistant")

        assert response.status_code == 200
        assert 'data-upload-args value="--trackers dp,ulcx"' in response.text


def test_item_page_discovarr_source_uses_release_title_provider(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_candidate(client, name="Amy_Bradley_Is_Missing_S01E03_2025_2160p_NF_WEB-DL_DDP5_1-GRP")

        response = client.get(f"/items/{item_id}")

        assert response.status_code == 200
        assert "Source: NF" in response.text
        assert "Source Missing" not in response.text
        assert 'data-upload-args value="--trackers dp,ulcx"' in response.text


def test_item_page_upload_console_ignores_service_from_arr_comparison_result(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "arr-provider",
                "name": "Untold.The.Death.and.Life.of.Lamar.Odom.2026.HDR.2160p.WEB.h265-EDITH",
                "category": "movies",
                "tags": "",
                "content_path": "/media/torrents/movies/untold",
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
            "Valid upload candidate on: DP, ULCX",
            mapped_path="/media/torrents/movies/untold",
            tracker_results={"passed": ["DP", "ULCX"], "dupe": [], "skipped": [], "error": []},
            arr_results={
                "status": "candidate",
                "local_traits": {
                    "source": "web",
                    "source_tag": "WEB",
                    "source_provider": "",
                    "rip_type": "web",
                },
                "decisions": [
                    {
                        "tracker": "ULCX",
                        "status": "candidate",
                        "reason": "ok",
                        "results": [
                            {
                                "title": "Untold.The.Death.and.Life.of.Lamar.Odom.2026.1080p.NF.WEB-DL.DDP5.1.Atmos.H.264-BiOMA.mkv",
                                "traits": {
                                    "source": "web",
                                    "source_tag": "WEB-DL",
                                    "source_provider": "Netflix",
                                    "rip_type": "web-dl",
                                },
                            }
                        ],
                    }
                ],
            },
            check_results={
                "version": 1,
                "media": {"local_traits": {"source": "web", "source_tag": "WEB", "source_provider": "", "rip_type": "web"}},
                "nfo": {},
                "release_group_policy": {"candidate_trackers": ["DP", "ULCX"], "blocked_trackers": []},
                "flags": [],
            },
        )

        response = client.get(f"/items/{item_id}#upload-assistant")

        assert response.status_code == 200
        assert '--trackers dp,ulcx --service NF' not in response.text
        assert 'data-upload-args value="--trackers dp,ulcx"' in response.text
        assert "Source Missing" in response.text


def test_item_page_upload_console_prefills_service_from_mediainfo_source_field(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "mediainfo-provider",
                "name": "Movie.2026.1080p.WEB.h265-GRP",
                "category": "movies",
                "tags": "",
                "content_path": "/media/torrents/movies/movie",
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
            mapped_path="/media/torrents/movies/movie",
            tracker_results={"passed": ["DP"], "dupe": [], "skipped": [], "error": []},
            arr_results={
                "status": "candidate",
                "local_traits": {
                    "source": "web",
                    "source_tag": "WEB",
                    "source_provider": "",
                    "rip_type": "web",
                },
                "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
            },
            check_results={
                "version": 1,
                "media": {
                    "local_traits": {"source": "web", "source_tag": "WEB", "source_provider": "", "rip_type": "web"},
                    "mediainfo_files": [{"name": "movie.mkv", "traits": {}, "general": {"ServiceName": "Netflix"}}],
                },
                "nfo": {},
                "release_group_policy": {"candidate_trackers": ["DP"], "blocked_trackers": []},
                "flags": [],
            },
        )

        response = client.get(f"/items/{item_id}#upload-assistant")

        assert response.status_code == 200
        assert '--trackers dp --service NF' in response.text


def test_item_page_upload_console_prefills_service_for_plain_web_from_nfo(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        db = client.app.state.db
        db.insert_discovered(
            1,
            {
                "hash": "plain-web-nfo-provider",
                "name": "Untold.The.Death.and.Life.of.Lamar.Odom.2026.HDR.2160p.WEB.h265-EDITH",
                "category": "movies",
                "tags": "",
                "content_path": "/media/torrents/movies/untold",
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
            "Valid upload candidate on: DP, ULCX",
            mapped_path="/media/torrents/movies/untold",
            tracker_results={"passed": ["DP", "ULCX"], "dupe": [], "skipped": [], "error": []},
            arr_results={
                "status": "candidate",
                "local_traits": {
                    "source": "web",
                    "source_tag": "WEB",
                    "source_provider": "",
                    "rip_type": "web",
                },
                "decisions": [{"tracker": "ULCX", "status": "candidate", "reason": "ok"}],
            },
            check_results={
                "version": 1,
                "media": {"local_traits": {"source": "web", "source_tag": "WEB", "source_provider": "", "rip_type": "web"}},
                "nfo": {"content": "Site: Netflix\nNetwork: Netflix\n", "path": "release.nfo", "source": "qui", "provider_abbreviation": "NF"},
                "release_group_policy": {"candidate_trackers": ["DP", "ULCX"], "blocked_trackers": []},
                "flags": [],
            },
        )

        response = client.get(f"/items/{item_id}#upload-assistant")

        assert response.status_code == 200
        assert '--trackers dp,ulcx --service NF' in response.text


def test_upload_console_execute_returns_409_when_check_lock_is_busy(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        cfg = client.app.state.config_manager.load()
        cfg.upload_assistant.url = "http://ua"
        client.app.state.config_manager.save(cfg)
        client.app.state.secrets.set("ua_bearer_token", "token")
        item_id = _seed_candidate(client)
        client.app.state.ua_execution._owner = UaExecutionOwner(
            id="check-lock",
            kind="check",
            label="Checking item 99",
            item_id=99,
            session_id="check-session",
            started_at=123,
        )

        response = client.post(f"/api/items/{item_id}/upload-assistant/execute", json={"args": "--trackers dp"})

        assert response.status_code == 409
        assert response.json()["error"] == "Check running"


def test_upload_console_session_releases_lock_on_completion(tmp_path, monkeypatch):
    class FakeUploadAssistantClient:
        def __init__(self, _config, _token):
            pass

        async def execute_upload_stream(self, _path, _args, _session_id):
            yield sse_payload("system", "done")

    monkeypatch.setattr("app.ua_execution.UploadAssistantClient", FakeUploadAssistantClient)

    async def run():
        cfg = AppConfig()
        cfg.upload_assistant.url = "http://ua"
        secrets = SecretStore(str(tmp_path))
        secrets.set("ua_bearer_token", "token")
        coordinator = UaExecutionCoordinator()
        manager = UploadConsoleManager(coordinator)
        session, busy = await manager.start(item_id=1, path="/ua/movie.mkv", args="--trackers dp", config=cfg, secrets=secrets)

        assert session is not None
        assert busy["busy"] is False
        assert coordinator.snapshot()["busy"] is True
        chunks = []
        async for chunk in session.subscribe():
            chunks.append(chunk)
        assert "done" in "".join(chunks)
        assert coordinator.snapshot()["busy"] is False

    asyncio.run(run())


def test_upload_console_session_releases_lock_on_kill(tmp_path, monkeypatch):
    started = None

    class FakeUploadAssistantClient:
        def __init__(self, _config, _token):
            pass

        async def execute_upload_stream(self, _path, _args, _session_id):
            started.set()
            yield sse_payload("system", "running")
            await asyncio.Event().wait()

        async def kill_session(self, _session_id):
            return {"success": True}

    monkeypatch.setattr("app.ua_execution.UploadAssistantClient", FakeUploadAssistantClient)

    async def run():
        nonlocal started
        started = asyncio.Event()
        cfg = AppConfig()
        cfg.upload_assistant.url = "http://ua"
        secrets = SecretStore(str(tmp_path))
        secrets.set("ua_bearer_token", "token")
        coordinator = UaExecutionCoordinator()
        manager = UploadConsoleManager(coordinator)
        session, _busy = await manager.start(item_id=1, path="/ua/movie.mkv", args="--trackers dp", config=cfg, secrets=secrets)

        assert session is not None
        await asyncio.wait_for(started.wait(), timeout=1)
        assert coordinator.snapshot()["busy"] is True
        await session.kill()
        assert coordinator.snapshot()["busy"] is False

    asyncio.run(run())


def test_queued_import_runner_executes_and_notifies(tmp_path, monkeypatch):
    class FakeUploadAssistantClient:
        def __init__(self, _config, _token):
            pass

        async def execute_upload_stream(self, path, args, session_id):
            yield sse_payload("system", f"ran {path} {args} {session_id}")

    monkeypatch.setattr("app.service.UploadAssistantClient", FakeUploadAssistantClient)

    async def run():
        cfg = AppConfig()
        cfg.upload_assistant.url = "http://ua"
        secrets = SecretStore(str(tmp_path))
        secrets.set("ua_bearer_token", "token")
        db = Database(str(tmp_path / "whackamole.db"))
        import_id = db.enqueue_import(
            item_id=42,
            item_name="Queued.Movie.2026.1080p.NF.WEB-DL-GRP",
            path="/ua/movie.mkv",
            args="--trackers dp --unattended",
        )
        coordinator = UaExecutionCoordinator()
        service = WhackamoleService(AppConfigManagerStub(cfg), secrets, db, coordinator)

        await service.run_queued_import()
        assert service._import_task is not None
        await asyncio.wait_for(service._import_task, timeout=1)

        row = db.list_imports()[0]
        assert row["id"] == import_id
        assert row["status"] == "complete"
        assert "ran /ua/movie.mkv" in row["output"]
        assert coordinator.snapshot()["busy"] is False
        assert "Queued import complete" in db.service_error_history()[-1]["message"]

    asyncio.run(run())


def test_queued_import_runner_marks_ua_error_event_failed(tmp_path, monkeypatch):
    class FakeUploadAssistantClient:
        def __init__(self, _config, _token):
            pass

        async def execute_upload_stream(self, _path, _args, _session_id):
            yield sse_payload("system", "started")
            yield sse_payload("error", "Upload failed in UA")

    monkeypatch.setattr("app.service.UploadAssistantClient", FakeUploadAssistantClient)

    async def run():
        cfg = AppConfig()
        cfg.upload_assistant.url = "http://ua"
        secrets = SecretStore(str(tmp_path))
        secrets.set("ua_bearer_token", "token")
        db = Database(str(tmp_path / "whackamole.db"))
        db.enqueue_import(
            item_id=42,
            item_name="Broken.Movie.2026.1080p.NF.WEB-DL-GRP",
            path="/ua/broken.mkv",
            args="--trackers dp --unattended",
        )
        coordinator = UaExecutionCoordinator()
        service = WhackamoleService(AppConfigManagerStub(cfg), secrets, db, coordinator)

        await service.run_queued_import()
        assert service._import_task is not None
        await asyncio.wait_for(service._import_task, timeout=1)

        row = db.list_imports()[0]
        assert row["status"] == "error"
        assert "Upload failed in UA" in row["message"]
        assert "Upload failed in UA" in row["output"]
        assert coordinator.snapshot()["busy"] is False

    asyncio.run(run())


def test_queued_import_runner_marks_nonzero_exit_failed(tmp_path, monkeypatch):
    class FakeUploadAssistantClient:
        def __init__(self, _config, _token):
            pass

        async def execute_upload_stream(self, _path, _args, _session_id):
            yield sse_payload("system", "started")
            yield sse_payload("exit", "", code=2)

    monkeypatch.setattr("app.service.UploadAssistantClient", FakeUploadAssistantClient)

    async def run():
        cfg = AppConfig()
        cfg.upload_assistant.url = "http://ua"
        secrets = SecretStore(str(tmp_path))
        secrets.set("ua_bearer_token", "token")
        db = Database(str(tmp_path / "whackamole.db"))
        db.enqueue_import(
            item_id=42,
            item_name="Broken.Exit.2026.1080p.NF.WEB-DL-GRP",
            path="/ua/broken-exit.mkv",
            args="--trackers dp --unattended",
        )
        coordinator = UaExecutionCoordinator()
        service = WhackamoleService(AppConfigManagerStub(cfg), secrets, db, coordinator)

        await service.run_queued_import()
        assert service._import_task is not None
        await asyncio.wait_for(service._import_task, timeout=1)

        row = db.list_imports()[0]
        assert row["status"] == "error"
        assert "UA exited with code 2" in row["message"]
        assert coordinator.snapshot()["busy"] is False

    asyncio.run(run())


def test_queued_import_watchdog_kills_stuck_upload_and_notifies(tmp_path, monkeypatch):
    killed_sessions = []

    class FakeUploadAssistantClient:
        def __init__(self, _config, _token):
            pass

        async def execute_upload_stream(self, _path, _args, _session_id):
            yield sse_payload("system", "started")
            await asyncio.Event().wait()

        async def kill_session(self, session_id):
            killed_sessions.append(session_id)
            return {"success": True}

    monkeypatch.setattr("app.service.UploadAssistantClient", FakeUploadAssistantClient)

    async def run():
        cfg = AppConfig()
        cfg.upload_assistant.url = "http://ua"
        cfg.upload_assistant.request_timeout_seconds = 1
        secrets = SecretStore(str(tmp_path))
        secrets.set("ua_bearer_token", "token")
        db = Database(str(tmp_path / "whackamole.db"))
        db.enqueue_import(
            item_id=42,
            item_name="Stuck.Movie.2026.1080p.NF.WEB-DL-GRP",
            path="/ua/stuck.mkv",
            args="--trackers dp --unattended",
        )
        coordinator = UaExecutionCoordinator()
        service = WhackamoleService(AppConfigManagerStub(cfg), secrets, db, coordinator)

        await service.run_queued_import()
        assert service._import_task is not None
        await asyncio.wait_for(service._import_task, timeout=2)

        row = db.list_imports()[0]
        assert row["status"] == "error"
        assert "timed out" in row["message"]
        assert "started" in row["output"]
        assert killed_sessions
        assert coordinator.snapshot()["busy"] is False
        assert "timed out" in db.service_error_history()[-1]["message"]

    asyncio.run(run())


def test_mobile_notification_popout_uses_fixed_viewport_positioning():
    styles = Path("app/static/style.css").read_text()

    assert ".notification-popout" in styles
    assert "position: fixed;" in styles
    assert "max-height: calc(100dvh - 92px);" in styles


class AppConfigManagerStub:
    def __init__(self, cfg):
        self._cfg = cfg

    def load(self):
        return self._cfg
