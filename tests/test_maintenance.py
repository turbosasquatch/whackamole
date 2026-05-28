import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import ConfigManager, SecretStore
from app.database import Database
from app.service import WhackamoleService


LONDON = ZoneInfo("Europe/London")


def _service(tmp_path, now, monkeypatch):
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.qui.url = "http://qui.test"
    cfg.maintenance.enabled = True
    cfg.maintenance.timezone = "Europe/London"
    cfg.maintenance.start_time = "05:00"
    cfg.maintenance.lead_minutes = 30
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    secrets.set("qui_api_key", "token")
    db = Database(str(tmp_path / "whackamole.db"))
    service = WhackamoleService(manager, secrets, db)
    monkeypatch.setattr(service, "_local_now", lambda _cfg: now)
    return service, manager, db


def test_maintenance_lead_time_blocks_due_jobs(tmp_path, monkeypatch):
    service, _manager, db = _service(tmp_path, datetime(2026, 5, 28, 4, 35, tzinfo=LONDON), monkeypatch)
    db.insert_discovered(
        1,
        {
            "hash": "queued",
            "name": "Movie.2026.1080p.WEB-DL-GRP",
            "category": "movies",
            "tags": "",
            "content_path": "/media/torrents/movies/Movie.2026.1080p.WEB-DL-GRP.mkv",
            "progress": 1,
        },
        status="queued",
        baseline=False,
    )

    asyncio.run(service.run_due_jobs())

    row = db.list_items([], limit=1)[0]
    assert row["status"] == "queued"
    assert service.maintenance_snapshot()["active"] is True
    assert service.maintenance_snapshot()["state"] == "lead_time"


def test_maintenance_does_not_pause_before_qui_is_configured(tmp_path, monkeypatch):
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    manager = ConfigManager(str(tmp_path))
    cfg = manager.load()
    cfg.maintenance.enabled = True
    manager.save(cfg)
    secrets = SecretStore(str(tmp_path))
    db = Database(str(tmp_path / "whackamole.db"))
    service = WhackamoleService(manager, secrets, db)
    monkeypatch.setattr(service, "_local_now", lambda _cfg: datetime(2026, 5, 28, 5, 15, tzinfo=LONDON))

    assert asyncio.run(service._maintenance_pause_active(manager.load())) is False
    assert service.maintenance_snapshot()["active"] is False
    assert service.maintenance_snapshot()["dependency_configured"] is False


def test_maintenance_waits_for_qui_down_before_resuming(tmp_path, monkeypatch):
    service, manager, db = _service(tmp_path, datetime(2026, 5, 28, 4, 35, tzinfo=LONDON), monkeypatch)
    cfg = manager.load()

    assert asyncio.run(service._maintenance_pause_active(cfg)) is True

    monkeypatch.setattr(service, "_local_now", lambda _cfg: datetime(2026, 5, 28, 5, 5, tzinfo=LONDON))

    async def qui_still_up(_cfg):
        return True

    monkeypatch.setattr(service, "_qui_health_ok", qui_still_up)

    assert asyncio.run(service._maintenance_pause_active(cfg)) is True
    assert db.get_kv("maintenance_completed_date") is None
    assert service.maintenance_snapshot(cfg)["state"] == "waiting_for_qui_down"


def test_maintenance_resumes_after_qui_goes_down_then_up(tmp_path, monkeypatch):
    service, manager, db = _service(tmp_path, datetime(2026, 5, 28, 4, 35, tzinfo=LONDON), monkeypatch)
    cfg = manager.load()

    assert asyncio.run(service._maintenance_pause_active(cfg)) is True
    monkeypatch.setattr(service, "_local_now", lambda _cfg: datetime(2026, 5, 28, 5, 10, tzinfo=LONDON))

    async def qui_down(_cfg):
        return False

    monkeypatch.setattr(service, "_qui_health_ok", qui_down)
    assert asyncio.run(service._maintenance_pause_active(cfg)) is True
    assert db.get_kv("maintenance_seen_down") == "true"

    async def qui_up(_cfg):
        return True

    monkeypatch.setattr(service, "_qui_health_ok", qui_up)
    assert asyncio.run(service._maintenance_pause_active(cfg)) is False
    assert db.get_kv("maintenance_completed_date") == "2026-05-28"
    assert db.get_kv("maintenance_active_date") == ""
    assert service.maintenance_snapshot(cfg)["active"] is False


def test_manual_resume_suppresses_today_scheduled_pause(tmp_path, monkeypatch):
    service, _manager, _db = _service(tmp_path, datetime(2026, 5, 28, 4, 35, tzinfo=LONDON), monkeypatch)

    service.manual_pause()
    assert service.maintenance_snapshot()["active"] is True
    assert service.maintenance_snapshot()["state"] == "manual"

    service.manual_resume()
    snapshot = service.maintenance_snapshot()

    assert snapshot["active"] is False
    assert snapshot["manual_resumed_date"] == "2026-05-28"
