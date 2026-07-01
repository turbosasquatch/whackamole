import asyncio

import pytest

from app.notifications import build_candidate_embed, send_discord_notification


class FakeResponse:
    def __init__(self, status_code=204):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class FakeAsyncClient:
    calls = []
    status_code = 204

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json=None, **kwargs):
        self.calls.append((url, json))
        return FakeResponse(self.status_code)


def test_send_discord_notification_posts_embed(monkeypatch):
    FakeAsyncClient.calls = []
    FakeAsyncClient.status_code = 204
    monkeypatch.setattr("app.notifications.httpx.AsyncClient", FakeAsyncClient)

    asyncio.run(send_discord_notification("http://discord.test/webhook", {"title": "hi"}))

    assert FakeAsyncClient.calls == [("http://discord.test/webhook", {"embeds": [{"title": "hi"}]})]


def test_send_discord_notification_raises_on_error_status(monkeypatch):
    FakeAsyncClient.calls = []
    FakeAsyncClient.status_code = 500
    monkeypatch.setattr("app.notifications.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(RuntimeError):
        asyncio.run(send_discord_notification("http://discord.test/webhook", {"title": "hi"}))


def test_build_candidate_embed_includes_key_fields():
    item = {"name": "Some.Release.2024", "size": 5 * 1024 ** 3}
    tracker_groups = {"passed": ["DP"], "covered": [], "dupe": [], "skipped": [], "error": []}
    arr_result = {}
    check_results = {
        "flags": [{"key": "warn", "label": "Rename Check", "severity": "warning"}],
        "release_group_policy": {"candidate_trackers": ["ULCX"]},
    }

    embed = build_candidate_embed(
        event_title="🔍 Added to review",
        item=item,
        tracker_groups=tracker_groups,
        arr_result=arr_result,
        check_results=check_results,
        reason="Needs manual check",
    )

    assert embed["title"] == "🔍 Added to review"
    assert "Some.Release.2024" in embed["description"]
    assert "Needs manual check" in embed["description"]
    field_values = {field["name"]: field["value"] for field in embed["fields"]}
    assert field_values["Source"] == "DP"
    assert field_values["Valid For"] == "ULCX"
    assert field_values["Size"] == "5.0 GiB"
    assert "Rename Check (warning)" in field_values["Warnings / Errors"]
