import asyncio

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
