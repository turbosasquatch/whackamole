import asyncio

from fastapi.testclient import TestClient

from app.config import AppConfig, SecretStore
from app.main import app
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
        assert '--trackers dp,ulcx --service AMZN' in response.text


def test_item_page_upload_console_does_not_duplicate_service_when_title_has_it(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        item_id = _seed_candidate(client, name="Movie.2026.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP")

        response = client.get(f"/items/{item_id}#upload-assistant")

        assert response.status_code == 200
        assert 'data-upload-args value="--trackers dp,ulcx"' in response.text


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
