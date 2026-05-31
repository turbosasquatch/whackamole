import asyncio
import json

from app.config import ConfigManager, SecretStore
from app.database import Database
from app.service import WhackamoleService


class FakeQuiClient:
    calls = []

    def __init__(self, _cfg, _api_key):
        pass

    async def list_torrents_page(self, page=0, limit=None):
        self.calls.append(page)
        torrents = [
            {
                "hash": "source",
                "name": "Show.S01.1080p.WEB-DL-GRP",
                "category": "tv",
                "tags": "",
                "content_path": "/media/torrents/tv/Show.S01.1080p.WEB-DL-GRP",
                "progress": 1,
            },
            {
                "hash": "cross",
                "name": "Show.S01.1080p.WEB-DL-GRP",
                "category": "tv.cross",
                "tags": "cross-seed",
                "save_path": "/media/torrents/cross-seeds/DarkPeers",
                "content_path": "/media/torrents/cross-seeds/DarkPeers/Show.S01.1080p.WEB-DL-GRP",
                "progress": 1,
            },
            {
                "hash": "upload",
                "name": "Movie.2024.1080p.WEB-DL-GRP.mkv",
                "category": "uploads",
                "tags": "upload",
                "save_path": "/media/torrents/uploads/IHD",
                "content_path": "/media/torrents/uploads/IHD/Movie.2024.1080p.WEB-DL-GRP.mkv",
                "progress": 1,
            },
        ]
        if page:
            return {"total": len(torrents), "hasMore": False, "torrents": []}
        return {"total": len(torrents), "hasMore": False, "torrents": torrents}


class IncrementalQuiClient:
    calls = []

    def __init__(self, _cfg, _api_key):
        pass

    async def list_torrents_page(self, page=0, limit=None):
        self.calls.append(page)
        if page == 0:
            return {
                "total": 2,
                "hasMore": True,
                "torrents": [
                    {
                        "hash": "known",
                        "name": "Known.Show.S01.1080p.WEB-DL-GRP",
                        "category": "tv",
                        "tags": "",
                        "content_path": "/media/torrents/tv/Known.Show.S01.1080p.WEB-DL-GRP",
                        "progress": 1,
                    }
                ],
            }
        return {
            "total": 2,
            "hasMore": False,
            "torrents": [
                {
                    "hash": "should-not-fetch",
                    "name": "Later.Show.S01.1080p.WEB-DL-GRP",
                    "category": "tv",
                    "tags": "",
                    "content_path": "/media/torrents/tv/Later.Show.S01.1080p.WEB-DL-GRP",
                    "progress": 1,
                }
            ],
        }


class InterruptedUploadAssistantClient:
    def __init__(self, _cfg, _bearer_token):
        pass

    async def execute_site_check(self, _path, _args, _session_id):
        return "Received SIGTERM, shutting down gracefully...\nWeb UI server stopped\nShutdown complete"


class MediaQuiClient:
    files = []
    mediainfo = {}
    mediainfo_error = None
    download_calls = 0

    def __init__(self, _cfg, _api_key):
        pass

    async def list_torrent_files(self, _torrent_hash):
        return self.files

    async def torrent_file_mediainfo(self, _torrent_hash, file_index):
        if self.mediainfo_error:
            raise self.mediainfo_error
        return self.mediainfo or {
            "fileIndex": file_index,
            "streams": [
                {"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"},
                {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
            ],
        }

    async def download_torrent_file(self, _torrent_hash, _file_index, max_bytes=262144):
        type(self).download_calls += 1
        raise AssertionError("NFO files should not be downloaded during MediaInfo identity checks")


class PassingUploadAssistantClient:
    calls = 0

    def __init__(self, _cfg, _bearer_token):
        pass

    async def execute_site_check(self, _path, _args, _session_id):
        type(self).calls += 1
        return "Trackers passed all checks: DP"


def _insert_check_item(db, name="Example.Show.S01E01.1080p.WEB-DL.DDP2.0.H.264-GRP"):
    db.insert_discovered(
        1,
        {
            "hash": "check-item",
            "name": name,
            "category": "tv",
            "tags": "",
            "content_path": f"/media/torrents/tv/{name}",
            "progress": 1,
        },
        status="queued",
        baseline=False,
    )
    return int(db.list_items([], limit=1)[0]["id"])


def test_poll_captures_support_inventory_without_queueing(tmp_path, monkeypatch):
    FakeQuiClient.calls = []
    monkeypatch.setattr("app.service.QuiClient", FakeQuiClient)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.qui.url = "http://qui.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    secrets.set("qui_api_key", "token")
    db = Database(str(tmp_path / "whackamole.db"))

    async def run_poll():
        service = WhackamoleService(manager, secrets, db)
        await service.poll_once()

    asyncio.run(run_poll())

    rows = {row["hash"]: row for row in db.list_items([], limit=10)}
    assert rows["source"]["status"] == "baseline"
    assert rows["cross"]["status"] == "inventory"
    assert rows["upload"]["status"] == "inventory"
    assert db.count_active_queue() == 0
    assert db.get_kv("inventory_done") == "true"
    assert db.get_kv("inventory_full_crawl_v2_done") == "true"


def test_incremental_poll_stops_after_all_known_page(tmp_path, monkeypatch):
    IncrementalQuiClient.calls = []
    monkeypatch.setattr("app.service.QuiClient", IncrementalQuiClient)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.qui.url = "http://qui.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    secrets.set("qui_api_key", "token")
    db = Database(str(tmp_path / "whackamole.db"))
    db.insert_discovered(
        1,
        {
            "hash": "known",
            "name": "Known.Show.S01.1080p.WEB-DL-GRP",
            "category": "tv",
            "tags": "",
            "content_path": "/media/torrents/tv/Known.Show.S01.1080p.WEB-DL-GRP",
            "progress": 1,
        },
        status="baseline",
        baseline=True,
    )
    db.set_kv("baseline_done", "true")
    db.set_kv("inventory_done", "true")
    db.set_kv("inventory_full_crawl_v2_done", "true")

    async def run_poll():
        service = WhackamoleService(manager, secrets, db)
        await service.poll_once()

    asyncio.run(run_poll())

    hashes = {row["hash"] for row in db.list_items([], limit=10)}
    assert IncrementalQuiClient.calls == [0]
    assert "should-not-fetch" not in hashes


def test_interrupted_ua_log_uses_error_backoff(tmp_path, monkeypatch):
    release_title = "Interrupted.Movie.2026.1080p.WEB-DL-GRP"
    MediaQuiClient.files = [
        {"index": 0, "name": f"{release_title}/{release_title}.nfo", "size": 100},
        {"index": 1, "name": f"{release_title}/{release_title}.mkv", "size": 1000},
    ]
    MediaQuiClient.mediainfo = {}
    MediaQuiClient.mediainfo_error = None
    MediaQuiClient.download_calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", InterruptedUploadAssistantClient)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    cfg.safety.error_backoff_minutes = [15, 60, 360]
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    db.insert_discovered(
        1,
        {
            "hash": "interrupted",
            "name": "Interrupted.Movie.2026.1080p.WEB-DL-GRP.mkv",
            "category": "movies",
            "tags": "",
            "content_path": "/media/torrents/movies/Interrupted.Movie.2026.1080p.WEB-DL-GRP.mkv",
            "progress": 1,
        },
        status="queued",
        baseline=False,
    )
    item_id = int(db.list_items([], limit=1)[0]["id"])

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    assert row["status"] == "error"
    assert row["verdict"] == "ua_interrupted"
    assert row["next_check_at"] is not None


def test_service_start_recovers_stale_checking_rows(tmp_path):
    manager = ConfigManager(str(tmp_path))
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    db.insert_discovered(
        1,
        {
            "hash": "stale-check",
            "name": "Stale.Check.Show.S01E01.1080p.WEB-DL-GRP",
            "category": "tv",
            "tags": "",
            "content_path": "/media/torrents/tv/Stale.Check.Show.S01E01.1080p.WEB-DL-GRP",
            "progress": 1,
        },
        status="checking",
        baseline=False,
    )
    item_id = int(db.list_items([], limit=1)[0]["id"])

    async def start_and_stop():
        service = WhackamoleService(manager, secrets, db)
        service.start()
        await service.stop()

    asyncio.run(start_and_stop())

    row = db.get_item(item_id)
    assert row["status"] == "error"
    assert row["verdict"] == "interrupted_check"
    assert row["next_check_at"] is not None
    assert db.get_kv("last_startup_recovered_checks") == "1"


def test_nfo_is_ignored_when_mediainfo_passes_and_continues_to_ua(tmp_path, monkeypatch):
    MediaQuiClient.files = [
        {"index": 0, "name": "Example.Show.S01E01.1080p.WEB-DL.DDP2.0.H.264-GRP/ignored.nfo", "size": 100},
        {"index": 1, "name": "Example.Show.S01E01.1080p.WEB-DL.DDP2.0.H.264-GRP/episode.mkv", "size": 1000},
    ]
    MediaQuiClient.mediainfo = {
        "fileIndex": 1,
        "streams": [
            {"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"},
            {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
        ],
    }
    MediaQuiClient.mediainfo_error = None
    MediaQuiClient.download_calls = 0
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    assert row["verdict"] != "nfo_missing"
    assert MediaQuiClient.download_calls == 0
    assert PassingUploadAssistantClient.calls == 1
    checks = json.loads(row["check_results"])
    stages = [stage["stage"] for stage in checks["diagnostics"]["stages"]]
    assert stages[:3] == ["media", "path", "ua"]
    assert checks["diagnostics"]["stages"][0]["status"] == "passed"


def test_mediainfo_failure_stops_before_ua(tmp_path, monkeypatch):
    MediaQuiClient.files = [
        {"index": 0, "name": "Example.Show.S01E01.1080p.WEB-DL.DDP2.0.H.264-GRP/episode.mkv", "size": 1000}
    ]
    MediaQuiClient.mediainfo = {}
    MediaQuiClient.mediainfo_error = RuntimeError("no mediainfo")
    MediaQuiClient.download_calls = 0
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    assert row["status"] == "manual_review"
    assert row["verdict"] == "mediainfo_unavailable"
    assert PassingUploadAssistantClient.calls == 0
    checks = json.loads(row["check_results"])
    assert checks["diagnostics"]["last_error"]["stage"] == "media"
    assert checks["diagnostics"]["stages"][0]["status"] == "error"


def test_mediainfo_pass_runs_ua_arr_and_applies_banned_group_policy(tmp_path, monkeypatch):
    release_title = "Example.Show.S01E01.1080p.WEB-DL.DDP2.0.H.264-GRP"
    MediaQuiClient.files = [
        {"index": 0, "name": f"{release_title}/{release_title}.nfo", "size": 100},
        {"index": 1, "name": f"{release_title}/{release_title}.mkv", "size": 1000},
    ]
    MediaQuiClient.mediainfo = {
        "fileIndex": 1,
        "streams": [
            {"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"},
            {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
        ],
    }
    MediaQuiClient.mediainfo_error = None
    MediaQuiClient.download_calls = 0
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)

    captured_arr_kwargs = {}

    async def fake_compare_item_with_arr(**kwargs):
        captured_arr_kwargs.update(kwargs)
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    cfg.tracker_policies["DP"]["banned_release_groups"] = ["GRP"]
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    assert row["status"] == "blocked"
    assert row["verdict"] == "banned_release_group"
    assert row["check_stage"] == "done"
    assert MediaQuiClient.download_calls == 0
    assert PassingUploadAssistantClient.calls == 1
    assert captured_arr_kwargs["local_traits"].audio_format == "DD+"
    checks = json.loads(row["check_results"])
    stages = [stage["stage"] for stage in checks["diagnostics"]["stages"]]
    assert stages == ["media", "path", "ua", "arr", "policy"]
    assert checks["media"]["raw_mediainfo_payloads"][0]["fileIndex"] == 1
