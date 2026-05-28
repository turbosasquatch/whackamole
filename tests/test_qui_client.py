import asyncio

from app.clients import QuiClient
from app.config import AppConfig


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    responses = []
    calls = []
    stream_payload = b""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, path, params=None, **kwargs):
        self.calls.append((path, dict(params or {})))
        return FakeResponse(self.responses.pop(0))

    def stream(self, method, path, **kwargs):
        self.calls.append((f"{method} {path}", {}))
        return FakeStreamResponse(self.stream_payload)


class FakeStreamResponse:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield self.payload


def test_qui_client_walks_all_pages(monkeypatch):
    FakeAsyncClient.responses = [
        {
            "total": 3,
            "hasMore": True,
            "torrents": [{"hash": "1", "name": "one"}, {"hash": "2", "name": "two"}],
        },
        {
            "total": 3,
            "hasMore": False,
            "torrents": [{"hash": "3", "name": "three"}],
        },
    ]
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.clients.httpx.AsyncClient", FakeAsyncClient)

    cfg = AppConfig()
    cfg.qui.url = "http://qui.test"
    cfg.qui.instance_id = 1
    cfg.qui.page_limit = 2
    client = QuiClient(cfg, "token")

    torrents = asyncio.run(client.list_torrents())

    assert [torrent["hash"] for torrent in torrents] == ["1", "2", "3"]
    assert FakeAsyncClient.calls[0][1]["page"] == "0"
    assert FakeAsyncClient.calls[1][1]["page"] == "1"


def test_qui_client_lists_torrent_files_with_indexes(monkeypatch):
    FakeAsyncClient.responses = [
        [
            {"name": "Release/Release.nfo", "size": 100},
            {"name": "Release/Release.mkv", "size": 1000},
        ]
    ]
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.clients.httpx.AsyncClient", FakeAsyncClient)

    cfg = AppConfig()
    cfg.qui.url = "http://qui.test"
    cfg.qui.instance_id = 1
    client = QuiClient(cfg, "token")

    files = asyncio.run(client.list_torrent_files("abc123"))

    assert [file["index"] for file in files] == [0, 1]
    assert FakeAsyncClient.calls[0][0].endswith("/api/instances/1/torrents/abc123/files")


def test_qui_client_downloads_torrent_file_with_size_cap(monkeypatch):
    FakeAsyncClient.stream_payload = b"nfo text"
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.clients.httpx.AsyncClient", FakeAsyncClient)

    cfg = AppConfig()
    cfg.qui.url = "http://qui.test"
    cfg.qui.instance_id = 1
    client = QuiClient(cfg, "token")

    payload = asyncio.run(client.download_torrent_file("abc123", 4))

    assert payload == b"nfo text"
    assert FakeAsyncClient.calls[0][0].endswith("/api/instances/1/torrents/abc123/files/4/download")
