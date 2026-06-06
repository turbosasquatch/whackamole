import asyncio
import json
import time

from app.config import ConfigManager, SecretStore
from app.database import Database
from app.service import WhackamoleService, _arr_local_traits_from_media_result, _local_torrent_file_path


def test_arr_local_traits_promotes_confirmed_mediainfo_file_traits():
    traits = _arr_local_traits_from_media_result(
        {
            "local_traits": {
                "title": "Greenland.2.Migration.2026.2160p.WebRip.Atmos.EAC3.5.1.HDR.x265-Lootera",
                "resolution": "2160p",
                "source": "web",
                "source_tag": "WEBRip",
                "hdr_rank": 1,
                "hdr_formats": ["HDR10"],
                "audio_format": "DD+ Atmos",
                "audio_format_rank": 13,
                "audio_channels": 5.1,
                "codec": "HEVC",
            },
            "mediainfo_files": [
                {
                    "traits": {
                        "hdr_rank": 2,
                        "hdr_formats": ["HDR10+", "HDR10"],
                        "audio_format": "DD+ Atmos",
                        "audio_format_rank": 13,
                        "audio_channels": 5.1,
                        "codec": "HEVC",
                    }
                }
            ],
        }
    )

    assert traits.hdr_rank == 2
    assert traits.hdr_formats == ("HDR10+", "HDR10")
    assert traits.audio_format == "DD+ Atmos"


def test_local_mediainfo_path_uses_file_content_path_without_duplication(tmp_path):
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    path = _local_torrent_file_path(
        cfg,
        {
            "content_path": (
                "/media/torrents/movies/Last.Night.In.Soho.2021.2160p.UHD.BluRay.Atmos.DV.x265-W4NK3R/"
                "Last.Night.In.Soho.2021.2160p.UHD.BluRay.Atmos.DV.x265-W4NK3R.mkv"
            )
        },
        {
            "name": (
                "Last.Night.In.Soho.2021.2160p.UHD.BluRay.Atmos.DV.x265-W4NK3R/"
                "Last.Night.In.Soho.2021.2160p.UHD.BluRay.Atmos.DV.x265-W4NK3R.mkv"
            )
        },
    )

    assert path == (
        "/data/torrents/movies/Last.Night.In.Soho.2021.2160p.UHD.BluRay.Atmos.DV.x265-W4NK3R/"
        "Last.Night.In.Soho.2021.2160p.UHD.BluRay.Atmos.DV.x265-W4NK3R.mkv"
    )


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


class KnownCandidateQuiClient:
    calls = []

    def __init__(self, _cfg, _api_key):
        pass

    async def list_torrents_page(self, page=0, limit=None):
        self.calls.append(page)
        if page:
            return {"total": 1, "hasMore": False, "torrents": []}
        return {
            "total": 1,
            "hasMore": False,
            "torrents": [
                {
                    "hash": "candidate",
                    "name": "Existing.Show.S01E01.1080p.WEB-DL-GRP",
                    "category": "tv",
                    "tags": "",
                    "content_path": "/media/torrents/tv/Existing.Show.S01E01.1080p.WEB-DL-GRP",
                    "progress": 1,
                }
            ],
        }


class DeletedCoverageQuiClient:
    calls = []

    def __init__(self, _cfg, _api_key):
        pass

    async def list_torrents_page(self, page=0, limit=None):
        self.calls.append(page)
        if page:
            return {"total": 1, "hasMore": False, "torrents": []}
        return {
            "total": 1,
            "hasMore": False,
            "torrents": [
                {
                    "hash": "source",
                    "name": "Existing.Show.S01E01.1080p.WEB-DL-GRP",
                    "category": "tv",
                    "tags": "",
                    "content_path": "/media/torrents/tv/Existing.Show.S01E01.1080p.WEB-DL-GRP",
                    "progress": 1,
                }
            ],
        }


class EndlessQuiClient:
    calls = []

    def __init__(self, _cfg, _api_key):
        pass

    async def list_torrents_page(self, page=0, limit=None):
        self.calls.append(page)
        return {
            "total": 9999,
            "hasMore": True,
            "torrents": [
                {
                    "hash": f"runaway-{page}",
                    "name": f"Runaway.Show.S01E{page + 1:02d}.1080p.WEB-DL-GRP",
                    "category": "tv",
                    "tags": "",
                    "content_path": f"/media/torrents/tv/Runaway.Show.S01E{page + 1:02d}.1080p.WEB-DL-GRP",
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
    mediainfo_calls = []
    download_calls = 0
    nfo_content = None

    def __init__(self, _cfg, _api_key):
        pass

    async def list_torrent_files(self, _torrent_hash):
        return self.files

    async def torrent_file_mediainfo(self, _torrent_hash, file_index):
        type(self).mediainfo_calls.append(file_index)
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
        if self.nfo_content is None:
            return b""
        return str(self.nfo_content).encode("utf-8")[:max_bytes]


class MediaInfoCliClient:
    payload = {}
    calls = []
    error = None

    def __init__(self, _cfg):
        pass

    async def file_mediainfo(self, path):
        type(self).calls.append(path)
        if self.error:
            raise self.error
        return self.payload or {
            "media": {
                "track": [
                    {"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
                ]
            }
        }


class PassingUploadAssistantClient:
    calls = 0

    def __init__(self, _cfg, _bearer_token):
        pass

    async def execute_site_check(self, _path, _args, _session_id):
        type(self).calls += 1
        return "Trackers passed all checks: DP"


class BlockedUploadAssistantClient:
    calls = 0

    def __init__(self, _cfg, _bearer_token):
        pass

    async def execute_site_check(self, _path, _args, _session_id):
        type(self).calls += 1
        return "No trackers remain after checking."


class FakeSrrdbClient:
    payload = {}
    calls = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def details(self, release_name):
        type(self).calls.append(release_name)
        return self.payload


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


def test_poll_stops_at_configured_qui_page_cap_without_completing_full_inventory(tmp_path, monkeypatch):
    EndlessQuiClient.calls = []
    monkeypatch.setattr("app.service.QuiClient", EndlessQuiClient)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.qui.url = "http://qui.test"
    cfg.safety.max_qui_poll_pages = 2
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    secrets.set("qui_api_key", "token")
    db = Database(str(tmp_path / "whackamole.db"))

    async def run_poll():
        service = WhackamoleService(manager, secrets, db)
        await service.poll_once()

    asyncio.run(run_poll())

    assert EndlessQuiClient.calls == [0, 1]
    assert db.get_kv("baseline_done") is None
    assert db.get_kv("inventory_done") is None
    assert db.get_kv("inventory_full_crawl_v2_done") is None
    assert "QUI poll stopped after 2 page(s)" in (db.get_kv("last_service_error") or "")


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
    db.set_kv("inventory_reconcile_completed_at", str(int(time.time())))

    async def run_poll():
        service = WhackamoleService(manager, secrets, db)
        await service.poll_once()

    asyncio.run(run_poll())

    hashes = {row["hash"] for row in db.list_items([], limit=10)}
    assert IncrementalQuiClient.calls == [0]
    assert "should-not-fetch" not in hashes


def test_poll_resolves_existing_candidate_from_current_inventory(tmp_path, monkeypatch):
    KnownCandidateQuiClient.calls = []
    monkeypatch.setattr("app.service.QuiClient", KnownCandidateQuiClient)
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
            "hash": "candidate",
            "name": "Existing.Show.S01E01.1080p.WEB-DL-GRP",
            "category": "tv",
            "tags": "",
            "content_path": "/media/torrents/tv/Existing.Show.S01E01.1080p.WEB-DL-GRP",
            "progress": 1,
        },
        status="candidate",
        baseline=False,
    )
    item_id = int(db.list_items(["candidate"], limit=1)[0]["id"])
    db.update_status(
        item_id,
        "candidate",
        "candidate",
        "Valid upload candidate on: IHD",
        tracker_results={"passed": ["IHD"], "dupe": [], "skipped": [], "error": []},
        arr_results={"decisions": [{"tracker": "IHD", "status": "candidate", "reason": "ok"}]},
    )
    db.insert_discovered(
        1,
        {
            "hash": "ihd-upload",
            "name": "Existing.Show.S01E01.1080p.WEB-DL-GRP",
            "category": "uploads",
            "tags": "upload",
            "save_path": "/media/torrents/uploads/IHD",
            "content_path": "/media/torrents/uploads/IHD/Existing.Show.S01E01.1080p.WEB-DL-GRP",
            "progress": 1,
        },
        status="inventory",
        baseline=True,
    )
    db.set_kv("baseline_done", "true")
    db.set_kv("inventory_done", "true")
    db.set_kv("inventory_full_crawl_v2_done", "true")
    db.set_kv("inventory_reconcile_completed_at", str(int(time.time())))

    async def run_poll():
        service = WhackamoleService(manager, secrets, db)
        await service.poll_once()

    asyncio.run(run_poll())

    row = db.get_item(item_id)
    assert KnownCandidateQuiClient.calls == [0]
    assert row["status"] == "covered"
    assert row["reason"] == "Covered in QUI: IHD"


def test_full_reconcile_removes_deleted_inventory_and_requeues_lost_coverage(tmp_path, monkeypatch):
    DeletedCoverageQuiClient.calls = []
    monkeypatch.setattr("app.service.QuiClient", DeletedCoverageQuiClient)
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
            "hash": "source",
            "name": "Existing.Show.S01E01.1080p.WEB-DL-GRP",
            "category": "tv",
            "tags": "",
            "content_path": "/media/torrents/tv/Existing.Show.S01E01.1080p.WEB-DL-GRP",
            "progress": 1,
        },
        status="covered",
        baseline=False,
    )
    item_id = int(db.list_items(["covered"], limit=1)[0]["id"])
    db.update_status(
        item_id,
        "covered",
        "covered",
        "Covered in QUI: DP",
        check_results={"coverage_resolution": {"status": "covered", "resolved_trackers": ["DP"]}},
    )
    db.insert_discovered(
        1,
        {
            "hash": "dp-cross",
            "name": "Existing.Show.S01E01.1080p.WEB-DL-GRP",
            "category": "tv.cross",
            "tags": "cross-seed",
            "save_path": "/media/torrents/cross-seeds/DarkPeers",
            "content_path": "/media/torrents/cross-seeds/DarkPeers/Existing.Show.S01E01.1080p.WEB-DL-GRP",
            "progress": 1,
        },
        status="inventory",
        baseline=True,
    )
    db.set_kv("baseline_done", "true")
    db.set_kv("inventory_done", "true")
    db.set_kv("inventory_full_crawl_v2_done", "true")
    db.set_kv("inventory_reconcile_completed_at", "0")

    async def run_poll():
        service = WhackamoleService(manager, secrets, db)
        await service.poll_once()

    asyncio.run(run_poll())

    rows = {row["hash"]: row for row in db.list_items([], limit=20)}
    row = db.get_item(item_id)

    assert DeletedCoverageQuiClient.calls == [0]
    assert "dp-cross" not in rows
    assert row["status"] == "queued"
    assert "DP" in row["reason"]


def test_interrupted_ua_log_uses_error_backoff(tmp_path, monkeypatch):
    release_title = "Interrupted.Movie.2026.1080p.NF.WEB-DL-GRP"
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
            "name": f"{release_title}.mkv",
            "category": "movies",
            "tags": "",
            "content_path": f"/media/torrents/movies/{release_title}.mkv",
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


def test_title_source_skips_nfo_when_mediainfo_passes_and_continues_to_ua(tmp_path, monkeypatch):
    release_title = "Example.Show.S01E01.1080p.NF.WEB-DL.DDP2.0.H.264-GRP"
    MediaQuiClient.files = [
        {"index": 0, "name": f"{release_title}/ignored.nfo", "size": 100},
        {"index": 1, "name": f"{release_title}/episode.mkv", "size": 1000},
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
    MediaQuiClient.nfo_content = None
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

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


def test_web_source_missing_runs_ua_and_arr_before_review_when_candidate(tmp_path, monkeypatch):
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
    MediaQuiClient.nfo_content = "No provider details here."
    PassingUploadAssistantClient.calls = 0
    FakeSrrdbClient.calls = []
    FakeSrrdbClient.payload = {}
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)
    monkeypatch.setattr("app.service.SrrdbClient", FakeSrrdbClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])
    assert row["status"] == "manual_review"
    assert row["verdict"] == "source_missing"
    assert MediaQuiClient.download_calls == 1
    assert PassingUploadAssistantClient.calls == 1
    assert checks["nfo"]["content"] == "No provider details here."
    assert [stage["stage"] for stage in checks["diagnostics"]["stages"]] == [
        "media",
        "nfo",
        "path",
        "ua",
        "arr",
        "policy",
        "srrdb",
        "review_gate",
    ]


def test_web_source_missing_stays_blocked_when_ua_has_no_tracker(tmp_path, monkeypatch):
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
    MediaQuiClient.nfo_content = "No provider details here."
    BlockedUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", BlockedUploadAssistantClient)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])

    assert row["status"] == "blocked"
    assert row["verdict"] == "no_tracker_passed"
    assert MediaQuiClient.download_calls == 1
    assert BlockedUploadAssistantClient.calls == 1
    assert "source_missing" in {flag["key"] for flag in checks["flags"]}
    assert [stage["stage"] for stage in checks["diagnostics"]["stages"]] == ["media", "nfo", "path", "ua", "arr"]


def test_web_source_missing_uses_nfo_provider_and_continues_to_ua(tmp_path, monkeypatch):
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
    MediaQuiClient.nfo_content = "Site: Netflix"
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])
    assert row["status"] == "candidate"
    assert MediaQuiClient.download_calls == 1
    assert PassingUploadAssistantClient.calls == 1
    assert checks["nfo"]["provider_abbreviation"] == "NF"
    assert [stage["stage"] for stage in checks["diagnostics"]["stages"]][:4] == ["media", "nfo", "path", "ua"]


def test_mediainfo_requests_are_limited_for_large_packs(tmp_path, monkeypatch):
    release_title = "Example.Show.S01.1080p.NF.WEB-DL.DDP2.0.H.264-GRP"
    MediaQuiClient.files = [
        {
            "index": index,
            "name": f"{release_title}/Example.Show.S01E{index + 1:02d}.1080p.NF.WEB-DL.DDP2.0.H.264-GRP.mkv",
            "size": 1000,
        }
        for index in range(12)
    ]
    MediaQuiClient.mediainfo = {
        "streams": [
            {"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"},
            {"@type": "Audio", "Format": "E-AC-3", "Channels": "2"},
        ],
    }
    MediaQuiClient.mediainfo_error = None
    MediaQuiClient.mediainfo_calls = []
    MediaQuiClient.download_calls = 0
    MediaQuiClient.nfo_content = None
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    cfg.safety.max_mediainfo_files_per_check = 3
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])

    assert MediaQuiClient.mediainfo_calls == [0, 1, 2]
    assert checks["media"]["mediainfo_limit"] == 3
    assert checks["media"]["mediainfo_truncated"] is True
    assert len(checks["media"]["raw_mediainfo_payloads"]) == 3
    assert any(issue["key"] == "mediainfo_truncated" for issue in checks["media"]["issues"])


def test_local_mediainfo_payloads_are_stored_and_can_clear_qui_atmos_miss(tmp_path, monkeypatch):
    release_title = "Example.Movie.2024.2160p.NF.WEB-DL.DDP5.1.Atmos.H.265-GRP"
    MediaQuiClient.files = [
        {"index": 0, "name": f"{release_title}/{release_title}.mkv", "size": 1000}
    ]
    MediaQuiClient.mediainfo = {
        "streams": [
            {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
            {"@type": "Audio", "Format": "E-AC-3", "Channels": "6"},
        ],
    }
    MediaQuiClient.mediainfo_error = None
    MediaQuiClient.mediainfo_calls = []
    MediaQuiClient.download_calls = 0
    MediaQuiClient.nfo_content = None
    MediaInfoCliClient.payload = {
        "media": {
            "track": [
                {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "2160", "ScanType": "Progressive"},
                {
                    "@type": "Audio",
                    "Format": "E-AC-3",
                    "Format_Commercial_IfAny": "Dolby Digital Plus with Dolby Atmos",
                    "Format_AdditionalFeatures": "JOC",
                    "Channels": "6",
                },
            ]
        }
    }
    MediaInfoCliClient.calls = []
    MediaInfoCliClient.error = None
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.LocalMediaInfoClient", MediaInfoCliClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])

    assert row["status"] == "candidate"
    assert MediaInfoCliClient.calls == [f"/data/torrents/tv/{release_title}/{release_title}.mkv"]
    assert len(checks["media"]["raw_local_mediainfo_payloads"]) == 1
    assert not any(issue["key"] == "audio_object_missing" for issue in checks["media"]["issues"])
    assert checks["media"]["resolved_mediainfo_issues"][0]["key"] == "audio_object_missing"


def test_media_error_sends_candidate_to_review(tmp_path, monkeypatch):
    release_title = "Last.Night.In.Soho.2021.2160p.UHD.BluRay.Atmos.DV.x265-W4NK3R"
    MediaQuiClient.files = [
        {"index": 0, "name": f"{release_title}/{release_title}.mkv", "size": 1000}
    ]
    MediaQuiClient.mediainfo = {
        "streams": [
            {"@type": "Video", "Format": "HEVC", "Width": "3840", "Height": "1608", "ScanType": "Progressive"},
            {"@type": "Audio", "Format": "MLP FBA", "Channels": "8"},
        ],
    }
    MediaQuiClient.mediainfo_error = None
    MediaQuiClient.mediainfo_calls = []
    MediaQuiClient.download_calls = 0
    MediaQuiClient.nfo_content = None
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    async def fake_verify_srrdb_release(**_kwargs):
        return {"status": "skipped", "reason": "skipped"}

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    monkeypatch.setattr("app.service.verify_srrdb_release", fake_verify_srrdb_release)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.mediainfo.enabled = False
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])

    assert row["status"] == "manual_review"
    assert row["verdict"] == "audio_object_missing"
    assert "object/JOC metadata" in row["reason"]
    assert any(flag["key"] == "audio_object_missing" for flag in checks["flags"])
    assert [stage["stage"] for stage in checks["diagnostics"]["stages"]] == [
        "media",
        "path",
        "ua",
        "arr",
        "policy",
        "srrdb",
        "review_gate",
    ]


def test_mediainfo_failure_runs_ua_and_arr_before_review_when_candidate(tmp_path, monkeypatch):
    MediaQuiClient.files = [
        {"index": 0, "name": "Example.Show.S01E01.1080p.WEB-DL.DDP2.0.H.264-GRP/episode.mkv", "size": 1000}
    ]
    MediaQuiClient.mediainfo = {}
    MediaQuiClient.mediainfo_error = RuntimeError("no mediainfo")
    MediaQuiClient.download_calls = 0
    MediaQuiClient.nfo_content = None
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
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
    assert PassingUploadAssistantClient.calls == 1
    checks = json.loads(row["check_results"])
    assert checks["diagnostics"]["last_error"]["stage"] == "media"
    assert [stage["stage"] for stage in checks["diagnostics"]["stages"]] == ["media", "path", "ua", "arr", "policy", "srrdb", "review_gate"]
    assert checks["diagnostics"]["stages"][0]["status"] == "error"


def test_mediainfo_pass_runs_ua_arr_and_applies_banned_group_policy(tmp_path, monkeypatch):
    release_title = "Example.Show.S01E01.1080p.NF.WEB-DL.DDP2.0.H.264-GRP"
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
    MediaQuiClient.nfo_content = None
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


def test_bloated_audio_runs_ua_arr_then_blocks_candidate(tmp_path, monkeypatch):
    release_title = "Movie.2024.1080p.BluRay.DTS-HD.MA.5.1.x264-GRP"
    MediaQuiClient.files = [
        {"index": 0, "name": f"{release_title}/{release_title}.mkv", "size": 1000},
    ]
    MediaQuiClient.mediainfo = {
        "fileIndex": 0,
        "streams": [
            {"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"},
            {"@type": "Audio", "Format": "DTS", "Format_Commercial_IfAny": "DTS-HD Master Audio", "Channels": "6"},
        ],
    }
    MediaQuiClient.mediainfo_error = None
    MediaQuiClient.download_calls = 0
    MediaQuiClient.nfo_content = None
    PassingUploadAssistantClient.calls = 0
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_title)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])

    assert row["status"] == "blocked"
    assert row["verdict"] == "bloated_audio"
    assert PassingUploadAssistantClient.calls == 1
    assert "bloated_audio" in {flag["key"] for flag in checks["flags"]}
    assert [stage["stage"] for stage in checks["diagnostics"]["stages"]] == ["media", "path", "ua", "arr", "policy", "media_policy"]


def test_srrdb_filename_mismatch_sends_candidate_to_review(tmp_path, monkeypatch):
    release_root = "The Panic in Needle Park 1971 1080p BluRay X264-AMIABLE"
    local_file = f"{release_root}.mkv"
    proper_file = "The.Panic.in.Needle.Park.1971.1080p.BluRay.X264-AMIABLE.mkv"
    MediaQuiClient.files = [
        {"index": 0, "name": f"{release_root}/{local_file}", "size": 11732671572},
    ]
    MediaQuiClient.mediainfo = {
        "fileIndex": 0,
        "streams": [
            {"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"},
            {"@type": "Audio", "Format": "AC-3", "Channels": "6"},
        ],
    }
    MediaQuiClient.mediainfo_error = None
    PassingUploadAssistantClient.calls = 0
    FakeSrrdbClient.calls = []
    FakeSrrdbClient.payload = {"archived-files": [{"name": proper_file, "size": 11732671572, "crc": "F45E29B8"}]}
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)
    monkeypatch.setattr("app.service.SrrdbClient", FakeSrrdbClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_root)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])

    assert row["status"] == "manual_review"
    assert row["verdict"] == "srrdb_filename_mismatch"
    assert proper_file in row["reason"]
    assert checks["srrdb"]["status"] == "mismatch"
    assert checks["srrdb"]["proper_filenames"] == [proper_file]
    assert checks["srrdb"]["local_video_files"] == [local_file]
    assert [stage["stage"] for stage in checks["diagnostics"]["stages"]] == ["media", "path", "ua", "arr", "policy", "srrdb"]
    assert FakeSrrdbClient.calls == ["The.Panic.in.Needle.Park.1971.1080p.BluRay.X264-AMIABLE"]


def test_folder_name_warning_sends_candidate_to_review(tmp_path, monkeypatch):
    release_root = "American Crime Story S03 1080p AMZN WEB-DL DDP5 1 H 264-NTb"
    MediaQuiClient.files = [
        {
            "index": 0,
            "name": f"{release_root}/American.Crime.Story.S03E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv",
            "size": 1000,
        },
        {
            "index": 1,
            "name": f"{release_root}/American.Crime.Story.S03E02.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb.mkv",
            "size": 1000,
        },
    ]
    MediaQuiClient.mediainfo = {
        "streams": [
            {"@type": "Video", "Format": "AVC", "Height": "1080", "ScanType": "Progressive"},
            {"@type": "Audio", "Format": "E-AC-3", "Channels": "6"},
        ],
    }
    MediaQuiClient.mediainfo_error = None
    MediaQuiClient.download_calls = 0
    MediaQuiClient.nfo_content = None
    PassingUploadAssistantClient.calls = 0
    FakeSrrdbClient.calls = []
    FakeSrrdbClient.payload = {}
    monkeypatch.setattr("app.service.QuiClient", MediaQuiClient)
    monkeypatch.setattr("app.service.UploadAssistantClient", PassingUploadAssistantClient)
    monkeypatch.setattr("app.service.SrrdbClient", FakeSrrdbClient)

    async def fake_compare_item_with_arr(**_kwargs):
        return {
            "status": "candidate",
            "reason": "Valid upload candidate on: DP",
            "decisions": [{"tracker": "DP", "status": "candidate", "reason": "ok"}],
        }

    monkeypatch.setattr("app.service.compare_item_with_arr", fake_compare_item_with_arr)
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.upload_assistant.url = "http://ua.test"
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    item_id = _insert_check_item(db, release_root)

    async def run_check():
        service = WhackamoleService(manager, secrets, db)
        await service.check_item(item_id)

    asyncio.run(run_check())

    row = db.get_item(item_id)
    checks = json.loads(row["check_results"])

    assert row["status"] == "manual_review"
    assert row["verdict"] == "folder_name_warning"
    assert row["reason"] == "Folder name would be normalised to American.Crime.Story.S03.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb."
    assert "folder_name_warning" in {flag["key"] for flag in checks["flags"]}
    assert [stage["stage"] for stage in checks["diagnostics"]["stages"]] == [
        "media",
        "path",
        "ua",
        "arr",
        "policy",
        "srrdb",
        "folder_name",
    ]
