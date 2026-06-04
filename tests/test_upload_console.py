import asyncio
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
        assert "--trackers dp --unattended" in imports_page.text


def test_upload_console_full_snapshots_are_not_terminal_replacements():
    script = Path("app/static/app.js").read_text()

    assert "lastFullSnapshotText" in script
    assert 'payload.type === "html_full"' in script
    assert 'if (replace) output.innerHTML = "";' not in script


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
