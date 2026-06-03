from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.arr_compare import compare_item_with_arr
from app.check_results import add_stage_diagnostic
from app.clients import QuiClient, SrrdbClient, UploadAssistantClient
from app.config import ConfigManager, SecretStore
from app.database import Database
from app.filters import is_completed_torrent, is_watchable_torrent
from app.inventory import build_inventory_meta, is_inventory_support
from app.media_identity import ReleaseTraits, traits_from_payload
from app.media_policy import (
    analyze_mediainfo,
    apply_release_group_policy,
    build_media_manual_result,
    empty_check_results,
    merge_check_results,
    video_file_payloads,
)
from app.pathmap import map_path
from app.reducer import reduce_ua_log
from app.srrdb import apply_srrdb_result, verify_srrdb_release
from app.source_providers import extract_provider_abbreviation, extract_provider_from_release_title, provider_abbreviation_for_label
from app.ua_execution import UaExecutionCoordinator


INVENTORY_RECONCILE_INTERVAL_SECONDS = 15 * 60
MAX_NFO_BYTES = 262144
NFO_EXTENSIONS = {".nfo"}
SOURCE_PROVIDER_FIELD_RE = re.compile(
    r"\b(?:service|network|studio|publisher|provider|source|site)\b",
    re.IGNORECASE,
)


def _arr_local_traits_from_media_result(media_result: Mapping[str, Any]) -> ReleaseTraits:
    local = traits_from_payload(media_result.get("local_traits") if isinstance(media_result.get("local_traits"), Mapping) else {})
    files = media_result.get("mediainfo_files") if isinstance(media_result.get("mediainfo_files"), list) else []
    for file_info in files:
        if not isinstance(file_info, Mapping):
            continue
        file_traits = traits_from_payload(file_info.get("traits") if isinstance(file_info.get("traits"), Mapping) else {})
        updates: Dict[str, Any] = {}
        if file_traits.hdr_rank > local.hdr_rank or (file_traits.hdr_formats and not local.hdr_formats):
            updates["hdr_rank"] = file_traits.hdr_rank
            updates["hdr_formats"] = file_traits.hdr_formats
            updates["dv_profile"] = file_traits.dv_profile
        for field in ("audio_format", "audio_format_rank", "audio_channels", "audio_objects", "codec", "bit_depth", "chroma"):
            value = getattr(file_traits, field)
            if value:
                updates[field] = value
        if updates:
            local = replace(local, **updates)
        break
    return local


def _is_web_media_result(item_name: str, media_result: Mapping[str, Any]) -> bool:
    traits = media_result.get("local_traits") if isinstance(media_result.get("local_traits"), Mapping) else {}
    values = " ".join(
        str(value or "")
        for value in (
            item_name,
            traits.get("rip_type"),
            traits.get("source_tag"),
            traits.get("source"),
            traits.get("source_label"),
        )
    )
    return bool(re.search(r"\b(?:WEB[-_. ]?DL|WEBDL|WEBRIP|WEB[-_. ]?RIP|WEB)\b", values, re.IGNORECASE))


def _source_provider_from_media_result(media_result: Mapping[str, Any]) -> str:
    traits = media_result.get("local_traits") if isinstance(media_result.get("local_traits"), Mapping) else {}
    provider = provider_abbreviation_for_label(str(traits.get("source_provider") or ""))
    if provider:
        return provider
    fields: list[str] = []
    files = media_result.get("mediainfo_files") if isinstance(media_result.get("mediainfo_files"), list) else []
    for file_info in files:
        if not isinstance(file_info, Mapping):
            continue
        file_traits = file_info.get("traits") if isinstance(file_info.get("traits"), Mapping) else {}
        provider = provider_abbreviation_for_label(str(file_traits.get("source_provider") or ""))
        if provider:
            return provider
        _collect_source_provider_fields(file_info, fields)
    payloads = media_result.get("raw_mediainfo_payloads") if isinstance(media_result.get("raw_mediainfo_payloads"), list) else []
    for payload in payloads:
        _collect_source_provider_fields(payload, fields)
    return extract_provider_abbreviation(*fields)


def _collect_source_provider_fields(value: Any, fields: list[str]) -> None:
    if isinstance(value, Mapping):
        field_name = str(value.get("name") or value.get("@name") or "")
        if field_name and SOURCE_PROVIDER_FIELD_RE.search(field_name) and "value" in value:
            fields.append(f"{field_name}: {value.get('value')}")
        for key, nested in value.items():
            key_text = str(key or "")
            if SOURCE_PROVIDER_FIELD_RE.search(key_text) and isinstance(nested, (str, int, float)):
                fields.append(f"{key_text}: {nested}")
            elif isinstance(nested, (Mapping, list, tuple)):
                _collect_source_provider_fields(nested, fields)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _collect_source_provider_fields(nested, fields)


def _nfo_payload(content: str, path: str, source: str) -> Dict[str, Any]:
    return {
        "available": bool(content),
        "source": source,
        "path": path,
        "content": content,
        "provider_abbreviation": extract_provider_abbreviation(content),
        "message": f"NFO found at {path}." if content else "No NFO content found.",
    }


async def _grab_nfo_from_qui(qui: QuiClient, item: Mapping[str, Any], torrent_files: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    try:
        for file_info in torrent_files:
            name = str(file_info.get("name") or "")
            if Path(name).suffix.lower() not in NFO_EXTENSIONS:
                continue
            content = (
                await qui.download_torrent_file(str(item["hash"] or ""), int(file_info.get("index") or 0), MAX_NFO_BYTES)
            ).decode("utf-8", errors="replace")
            return _nfo_payload(content, name, "qui")
    except Exception as exc:
        return {"available": False, "message": f"Could not grab NFO: {str(exc)[:180]}", "source": "error"}
    return {"available": False, "message": "No NFO found in QUI files.", "source": "qui"}


def _source_provider_for_web_gate(item_name: str, media_result: Mapping[str, Any], nfo_result: Optional[Mapping[str, Any]] = None) -> str:
    provider = extract_provider_from_release_title(item_name)
    if provider:
        return provider
    if nfo_result:
        provider = str(nfo_result.get("provider_abbreviation") or "").strip()
        if provider:
            return provider
    return _source_provider_from_media_result(media_result)


def _int_kv(value: Optional[str]) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class WhackamoleService:
    def __init__(
        self,
        config_manager: ConfigManager,
        secrets: SecretStore,
        db: Database,
        ua_execution: Optional[UaExecutionCoordinator] = None,
    ) -> None:
        self.config_manager = config_manager
        self.secrets = secrets
        self.db = db
        self.ua_execution = ua_execution or UaExecutionCoordinator()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._running_jobs = 0
        self._last_ua_job_started_at = 0.0
        self._arr_lock = asyncio.Lock()
        self._srrdb_lock = asyncio.Lock()
        self._last_srrdb_request_at = 0.0
        self._maintenance_probe_at = 0.0
        self._maintenance_probe_ok: Optional[bool] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            recovered = self.db.recover_stale_checking(self._next_error_check(0, None))
            if recovered:
                self.db.set_kv("last_startup_recovered_checks", str(recovered))
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def run(self) -> None:
        while not self._stop.is_set():
            cfg = self.config_manager.load()
            try:
                maintenance_active = await self._maintenance_pause_active(cfg)
                if not maintenance_active and cfg.qui.url and self.secrets.has("qui_api_key"):
                    await self.poll_once()
                    await self.run_due_jobs()
            except Exception as exc:
                self.db.append_service_error(str(exc))

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(15, cfg.safety.poll_interval_seconds))
            except asyncio.TimeoutError:
                pass

    async def poll_once(self) -> None:
        cfg = self.config_manager.load()
        client = QuiClient(cfg, self.secrets.get("qui_api_key"))
        baseline_done = self.db.get_kv("baseline_done") == "true"
        inventory_done = self.db.get_kv("inventory_done") == "true"
        full_inventory_done = self.db.get_kv("inventory_full_crawl_v2_done") == "true"
        last_reconcile_at = _int_kv(self.db.get_kv("inventory_reconcile_completed_at"))
        reconcile_due = full_inventory_done and int(time.time()) - last_reconcile_at >= INVENTORY_RECONCILE_INTERVAL_SECONDS
        full_crawl = not full_inventory_done or reconcile_due
        baseline_mode = (not baseline_done and not cfg.watch.process_existing_on_first_run) or (
            baseline_done and not inventory_done
        ) or (
            baseline_done and full_crawl
        )

        active_count = self.db.count_active_queue()
        page = 0
        limit = max(1, cfg.qui.page_limit)
        fetched = 0
        seen_hashes = set()
        seen_completed_hashes = set()
        while True:
            data = await client.list_torrents_page(page=page, limit=limit)
            torrents = data.get("torrents", [])
            torrents = torrents if isinstance(torrents, list) else []
            hashes = [str(torrent.get("hash") or "") for torrent in torrents if str(torrent.get("hash") or "")]
            existing_hashes = self.db.existing_hashes(cfg.qui.instance_id, hashes)
            page_had_new_hash = False

            for torrent in torrents:
                torrent_hash = str(torrent.get("hash") or "")
                if not torrent_hash or torrent_hash in seen_hashes:
                    continue
                seen_hashes.add(torrent_hash)
                is_existing = torrent_hash in existing_hashes
                if not is_existing:
                    page_had_new_hash = True
                if is_existing and not full_crawl:
                    continue
                if not is_completed_torrent(torrent):
                    continue
                seen_completed_hashes.add(torrent_hash)
                content_path = torrent.get("content_path") or torrent.get("contentPath")
                if not content_path:
                    continue
                inventory_meta = build_inventory_meta(torrent)
                if is_existing:
                    self.db.sync_torrent_metadata(cfg.qui.instance_id, torrent, inventory_meta)
                    continue
                if is_inventory_support(inventory_meta) or not is_watchable_torrent(torrent, cfg.watch):
                    self.db.insert_discovered(
                        cfg.qui.instance_id,
                        torrent,
                        status="inventory",
                        baseline=True,
                        inventory_meta=inventory_meta,
                    )
                    continue
                if baseline_mode:
                    self.db.insert_discovered(
                        cfg.qui.instance_id,
                        torrent,
                        status="baseline",
                        baseline=True,
                        inventory_meta=inventory_meta,
                    )
                    continue
                status = "queued" if active_count < cfg.safety.max_queue_size else "deferred"
                self.db.insert_discovered(
                    cfg.qui.instance_id,
                    torrent,
                    status=status,
                    baseline=False,
                    inventory_meta=inventory_meta,
                )
                active_count += 1

            fetched += len(torrents)
            has_more_known = "hasMore" in data
            has_more = bool(data.get("hasMore"))
            total = int(data.get("total") or 0)
            page += 1
            if not torrents:
                break
            if not full_crawl and not page_had_new_hash:
                break
            if total and fetched >= total:
                break
            if has_more_known and not has_more:
                break
            if not has_more_known and len(torrents) < limit:
                break

        if not baseline_done:
            self.db.set_kv("baseline_done", "true")
        if not inventory_done:
            self.db.set_kv("inventory_done", "true")
        if full_crawl:
            self.db.set_kv("inventory_full_crawl_v2_done", "true")
            self.db.prune_missing_inventory(cfg.qui.instance_id, seen_completed_hashes)
            self.db.requeue_covered_with_missing_coverage()
            self.db.set_kv("inventory_reconcile_completed_at", str(int(time.time())))
        self.db.resolve_covered_candidates()

    async def run_due_jobs(self) -> None:
        cfg = self.config_manager.load()
        if self.maintenance_snapshot(cfg)["active"]:
            return
        if self._running_jobs >= max(1, cfg.safety.max_concurrent_ua_jobs):
            return

        due = self.db.get_due_items(limit=max(1, cfg.safety.max_concurrent_ua_jobs - self._running_jobs))
        for item in due:
            if self._running_jobs >= max(1, cfg.safety.max_concurrent_ua_jobs):
                return
            wait_for = cfg.safety.min_seconds_between_ua_jobs - (time.time() - self._last_ua_job_started_at)
            if wait_for > 0:
                return
            self._running_jobs += 1
            asyncio.create_task(self._run_item(item["id"]))

    def snapshot(self) -> dict:
        cfg = self.config_manager.load()
        task_alive = self._task is not None and not self._task.done()
        return {
            "running": task_alive and not self._stop.is_set(),
            "running_jobs": self._running_jobs,
            "last_ua_job_started_at": int(self._last_ua_job_started_at or 0),
            "last_service_error": self.db.get_kv("last_service_error") or "",
            "service_errors": self.db.service_error_history(),
            "baseline_done": self.db.get_kv("baseline_done") == "true",
            "inventory_done": self.db.get_kv("inventory_done") == "true",
            "inventory_count": self.db.count_items([]),
            "queue": self.db.queue_counts(),
            "whacked": self.db.whacked_stats(),
            "maintenance": self.maintenance_snapshot(cfg),
            "ua_execution": self.ua_execution.snapshot(),
        }

    def manual_pause(self) -> None:
        now = int(time.time())
        self.db.set_kv("maintenance_manual_paused", "true")
        self.db.set_kv("maintenance_manual_reason", "Manual pause")
        self.db.set_kv("maintenance_manual_updated_at", str(now))

    def manual_resume(self) -> None:
        cfg = self.config_manager.load()
        today = self._local_now(cfg).date().isoformat()
        self.db.set_kv("maintenance_manual_paused", "false")
        self.db.set_kv("maintenance_manual_reason", "")
        self.db.set_kv("maintenance_manual_resume_date", today)
        self._clear_scheduled_maintenance()

    def maintenance_snapshot(self, cfg=None) -> dict:
        cfg = cfg or self.config_manager.load()
        now = self._local_now(cfg)
        today = now.date().isoformat()
        current_start = self._scheduled_start(now, cfg)
        lead_delta = timedelta(minutes=max(0, int(cfg.maintenance.lead_minutes or 0)))
        current_lead_start = current_start - lead_delta
        active_date = self.db.get_kv("maintenance_active_date") or ""
        completed_date = self.db.get_kv("maintenance_completed_date") or ""
        manual_resumed_date = self.db.get_kv("maintenance_manual_resume_date") or ""
        manual_paused = self.db.get_kv("maintenance_manual_paused") == "true"
        seen_down = self.db.get_kv("maintenance_seen_down") == "true"
        dependency_configured = bool(cfg.qui.url and self.secrets.has("qui_api_key"))
        scheduled_active = active_date == today and completed_date != today and manual_resumed_date != today
        lead_pending = (
            bool(cfg.maintenance.enabled)
            and dependency_configured
            and manual_resumed_date != today
            and completed_date != today
            and current_lead_start <= now < current_start
        )
        active = manual_paused or scheduled_active or lead_pending
        display_start = current_start
        if not active and now >= current_start:
            display_start = current_start + timedelta(days=1)
        display_lead_start = display_start - lead_delta
        if manual_paused:
            reason = self.db.get_kv("maintenance_manual_reason") or "Manual pause"
            state = "manual"
        elif scheduled_active and seen_down:
            reason = "Maintenance active: QUI went down, waiting for it to come back healthy."
            state = "waiting_for_qui_up"
        elif scheduled_active:
            reason = "Maintenance active: waiting for QUI to go down and come back."
            state = "waiting_for_qui_down"
        elif lead_pending:
            reason = f"Maintenance lead time active until {cfg.maintenance.start_time}."
            state = "lead_time"
        else:
            reason = ""
            state = "idle"
        return {
            "enabled": bool(cfg.maintenance.enabled),
            "active": active,
            "state": state,
            "reason": reason,
            "timezone": cfg.maintenance.timezone,
            "start_time": cfg.maintenance.start_time,
            "lead_minutes": int(cfg.maintenance.lead_minutes or 0),
            "next_start_at": display_start.isoformat(),
            "lead_start_at": display_lead_start.isoformat(),
            "active_date": active_date,
            "completed_date": completed_date,
            "seen_dependency_down": seen_down,
            "manual_paused": manual_paused,
            "manual_resumed_date": manual_resumed_date,
            "dependency": "QUI",
            "dependency_configured": dependency_configured,
        }

    async def _maintenance_pause_active(self, cfg) -> bool:
        snapshot = self.maintenance_snapshot(cfg)
        if snapshot["manual_paused"]:
            return True
        if not cfg.maintenance.enabled:
            return False
        if not snapshot["dependency_configured"]:
            return False

        now = self._local_now(cfg)
        today = now.date().isoformat()
        if self.db.get_kv("maintenance_manual_resume_date") == today:
            return False
        if self.db.get_kv("maintenance_completed_date") == today:
            return False

        scheduled_start = self._scheduled_start(now, cfg)
        lead_start = scheduled_start - timedelta(minutes=max(0, int(cfg.maintenance.lead_minutes or 0)))
        if lead_start <= now < scheduled_start:
            self._start_scheduled_maintenance(today)
            return True
        if now < scheduled_start:
            return False

        active_date = self.db.get_kv("maintenance_active_date") or ""
        health_ok = await self._qui_health_ok(cfg)
        if active_date != today:
            if health_ok:
                return False
            self._start_scheduled_maintenance(today)
            self.db.set_kv("maintenance_seen_down", "true")
            return True

        if not health_ok:
            self.db.set_kv("maintenance_seen_down", "true")
            return True

        if self.db.get_kv("maintenance_seen_down") == "true":
            self.db.set_kv("maintenance_completed_date", today)
            self._clear_scheduled_maintenance()
            return False

        return True

    async def _qui_health_ok(self, cfg) -> bool:
        if not cfg.qui.url:
            return False
        if time.time() - self._maintenance_probe_at < 10 and self._maintenance_probe_ok is not None:
            return self._maintenance_probe_ok
        try:
            await QuiClient(cfg, self.secrets.get("qui_api_key")).health()
        except Exception:
            ok = False
        else:
            ok = True
        self._maintenance_probe_at = time.time()
        self._maintenance_probe_ok = ok
        return ok

    def _start_scheduled_maintenance(self, today: str) -> None:
        if self.db.get_kv("maintenance_active_date") != today:
            self.db.set_kv("maintenance_active_date", today)
            self.db.set_kv("maintenance_seen_down", "false")

    def _clear_scheduled_maintenance(self) -> None:
        self.db.set_kv("maintenance_active_date", "")
        self.db.set_kv("maintenance_seen_down", "false")

    def _local_now(self, cfg) -> datetime:
        return datetime.now(self._timezone(cfg))

    def _timezone(self, cfg) -> ZoneInfo:
        try:
            return ZoneInfo(cfg.maintenance.timezone or "Europe/London")
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def _scheduled_start(self, now: datetime, cfg) -> datetime:
        hour, minute = self._parse_start_time(cfg.maintenance.start_time)
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _parse_start_time(self, value: str) -> tuple[int, int]:
        try:
            hour_text, minute_text = str(value or "05:00").split(":", 1)
            hour = max(0, min(23, int(hour_text)))
            minute = max(0, min(59, int(minute_text)))
            return hour, minute
        except (TypeError, ValueError):
            return 5, 0

    async def _run_item(self, item_id: int) -> None:
        try:
            await self.check_item(item_id)
        finally:
            self._running_jobs -= 1

    async def check_item(self, item_id: int) -> None:
        cfg = self.config_manager.load()
        item = self.db.get_item(item_id)
        if item is None:
            return

        lease = await self.ua_execution.acquire(
            kind="check",
            label=f"Checking item {item_id}",
            item_id=item_id,
            session_id=str(item["hash"] or item_id),
            wait=True,
        )
        if lease is None:
            return
        self._last_ua_job_started_at = time.time()
        try:
            check_results = empty_check_results()
            media_result, check_results, terminal = await self._run_media_stage(item_id, item, cfg, check_results)
            if terminal:
                return

            mapped_path, check_results, terminal = self._run_path_stage(item_id, item, cfg, check_results)
            if terminal or not mapped_path:
                return

            ua_log, reduction, check_results, terminal = await self._run_ua_stage(
                item_id,
                item,
                cfg,
                mapped_path,
                check_results,
            )
            if terminal or reduction is None:
                return

            await self._run_arr_and_policy_stage(
                item_id,
                item,
                cfg,
                mapped_path,
                ua_log,
                reduction,
                media_result,
                check_results,
            )
        finally:
            await lease.release()

    async def _run_media_stage(
        self,
        item_id: int,
        item: Mapping[str, Any],
        cfg: Any,
        check_results: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any], bool]:
        started_at = time.perf_counter()
        self.db.update_check_stage(item_id, "media", "Checking QUI MediaInfo identity before UA.", check_results)
        qui = QuiClient(cfg, self.secrets.get("qui_api_key"))
        torrent_files = []
        try:
            torrent_files = await qui.list_torrent_files(str(item["hash"]))
            video_files = video_file_payloads(torrent_files)
            mediainfo_payloads = []
            for video_file in video_files:
                payload = await qui.torrent_file_mediainfo(str(item["hash"]), int(video_file["index"]))
                payload.setdefault("fileIndex", int(video_file["index"]))
                payload.setdefault("relativePath", str(video_file.get("name") or ""))
                mediainfo_payloads.append(payload)
            media_result = analyze_mediainfo(
                item_name=str(item["name"] or ""),
                files=torrent_files,
                mediainfo_payloads=mediainfo_payloads,
            )
            media_result["raw_mediainfo_payloads"] = mediainfo_payloads
            if len(video_files) > len(mediainfo_payloads):
                media_result["mediainfo_truncated"] = True
        except Exception as exc:
            media_result = build_media_manual_result(
                "mediainfo_unavailable",
                f"Whackamole could not read QUI MediaInfo: {str(exc)[:180]}",
                torrent_files,
            )
            check_results = merge_check_results(
                check_results,
                media=media_result,
                flags=media_result.get("flags", []),
            )
            check_results = add_stage_diagnostic(
                check_results,
                stage="media",
                status="error",
                reason=media_result["reason"],
                started_at=started_at,
                error=exc,
                extra={"verdict": media_result["verdict"]},
            )
            self.db.update_status(
                item_id,
                "manual_review",
                media_result["verdict"],
                media_result["reason"],
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
                arr_results={},
                check_stage="done",
                check_results=check_results,
                increment_attempt=True,
            )
            return media_result, check_results, True

        check_results = merge_check_results(
            check_results,
            media=media_result,
            flags=media_result.get("flags", []),
        )
        if media_result.get("status") != "passed":
            check_results = add_stage_diagnostic(
                check_results,
                stage="media",
                status="failed",
                reason=str(media_result.get("reason") or "MediaInfo identity check failed."),
                started_at=started_at,
                extra={"verdict": str(media_result.get("verdict") or "media_error")},
            )
            self.db.update_status(
                item_id,
                "manual_review",
                str(media_result.get("verdict") or "media_error"),
                str(media_result.get("reason") or "MediaInfo identity check failed."),
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
                arr_results={},
                check_stage="done",
                check_results=check_results,
                increment_attempt=True,
            )
            return media_result, check_results, True

        check_results = add_stage_diagnostic(
            check_results,
            stage="media",
            status="passed",
            reason=str(media_result.get("reason") or "MediaInfo confirmed."),
            started_at=started_at,
            extra={
                "verdict": str(media_result.get("verdict") or "mediainfo_passed"),
                "video_files": len(media_result.get("video_files") or []),
                "mediainfo_files": len(media_result.get("mediainfo_files") or []),
            },
        )
        item_name = str(item["name"] or "")
        if _is_web_media_result(item_name, media_result) and not _source_provider_for_web_gate(item_name, media_result):
            self.db.update_check_stage(item_id, "nfo", "Looking for WEB source provider in NFO.", check_results)
            nfo_started_at = time.perf_counter()
            nfo_result = await _grab_nfo_from_qui(qui, item, torrent_files)
            check_results = merge_check_results(check_results, nfo=nfo_result, flags=check_results.get("flags", []))
            provider = _source_provider_for_web_gate(item_name, media_result, nfo_result)
            check_results = add_stage_diagnostic(
                check_results,
                stage="nfo",
                status="passed" if provider else "failed",
                reason=str(nfo_result.get("message") or "NFO source lookup complete."),
                started_at=nfo_started_at,
                extra={"provider": provider or ""},
            )
            if not provider:
                flags = list(check_results.get("flags") or [])
                flags.append(
                    {
                        "key": "source_missing",
                        "label": "Source Missing",
                        "severity": "warning",
                        "detail": "WEB-DL/WEBRip source provider was not found in the title, MediaInfo, or NFO.",
                    }
                )
                reason = "WEB-DL/WEBRip source provider is missing; review before upload."
                check_results = merge_check_results(check_results, flags=flags)
                self.db.update_status(
                    item_id,
                    "manual_review",
                    "source_missing",
                    reason,
                    tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
                    arr_results={},
                    check_stage="done",
                    check_results=check_results,
                    increment_attempt=True,
                )
                return media_result, check_results, True
        return media_result, check_results, False

    def _run_path_stage(
        self,
        item_id: int,
        item: Mapping[str, Any],
        cfg: Any,
        check_results: Dict[str, Any],
    ) -> tuple[Optional[str], Dict[str, Any], bool]:
        started_at = time.perf_counter()
        try:
            mapped_path = map_path(item["content_path"], cfg.path_mappings)
        except ValueError as exc:
            check_results = add_stage_diagnostic(
                check_results,
                stage="path",
                status="error",
                reason=str(exc),
                started_at=started_at,
                error=exc,
            )
            self.db.update_status(
                item_id,
                "error",
                "path_mapping",
                str(exc),
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": ["Path mapping"]},
                arr_results={},
                check_stage="done",
                check_results=check_results,
                increment_attempt=True,
            )
            return None, check_results, True

        check_results = add_stage_diagnostic(
            check_results,
            stage="path",
            status="passed",
            reason="Path mapping succeeded.",
            started_at=started_at,
        )
        return mapped_path, check_results, False

    async def _run_ua_stage(
        self,
        item_id: int,
        item: Mapping[str, Any],
        cfg: Any,
        mapped_path: str,
        check_results: Dict[str, Any],
    ) -> tuple[str, Any, Dict[str, Any], bool]:
        ua_args = "--site-check -ua -sda"
        ua = UploadAssistantClient(cfg, self.secrets.get("ua_bearer_token"))
        self.db.update_status(
            item_id,
            "checking",
            mapped_path=mapped_path,
            ua_args=ua_args,
            arr_results={},
            check_stage="ua",
            check_results=check_results,
        )

        started_at = time.perf_counter()
        try:
            log = await ua.execute_site_check(mapped_path, ua_args, item["hash"])
        except httpx.HTTPStatusError as exc:
            next_check = self._next_error_check(item["attempt_count"], exc.response.headers.get("Retry-After"))
            check_results = merge_check_results(
                check_results,
                ua={"status": "error", "verdict": "http_error", "reason": f"UA HTTP error {exc.response.status_code}"},
                flags=check_results.get("flags", []),
            )
            check_results = add_stage_diagnostic(
                check_results,
                stage="ua",
                status="error",
                reason=f"UA HTTP error {exc.response.status_code}",
                started_at=started_at,
                error=exc,
            )
            self.db.update_status(
                item_id,
                "error",
                "http_error",
                f"UA HTTP error {exc.response.status_code}",
                mapped_path=mapped_path,
                ua_args=ua_args,
                ua_log=str(exc),
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": ["UA"]},
                arr_results={},
                check_stage="done",
                check_results=check_results,
                next_check_at=next_check,
                increment_attempt=True,
            )
            return str(exc), None, check_results, True
        except Exception as exc:
            next_check = self._next_error_check(item["attempt_count"], None)
            check_results = merge_check_results(
                check_results,
                ua={"status": "error", "verdict": "ua_error", "reason": str(exc)[:240]},
                flags=check_results.get("flags", []),
            )
            check_results = add_stage_diagnostic(
                check_results,
                stage="ua",
                status="error",
                reason=str(exc)[:240],
                started_at=started_at,
                error=exc,
            )
            self.db.update_status(
                item_id,
                "error",
                "ua_error",
                str(exc),
                mapped_path=mapped_path,
                ua_args=ua_args,
                ua_log=str(exc),
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": ["UA"]},
                arr_results={},
                check_stage="done",
                check_results=check_results,
                next_check_at=next_check,
                increment_attempt=True,
            )
            return str(exc), None, check_results, True

        reduction = reduce_ua_log(log)
        check_results = merge_check_results(
            check_results,
            ua={
                "status": reduction.status,
                "verdict": reduction.verdict,
                "reason": reduction.reason,
                "tracker_results": reduction.tracker_results,
            },
            flags=check_results.get("flags", []),
        )
        check_results = add_stage_diagnostic(
            check_results,
            stage="ua",
            status=reduction.status,
            reason=reduction.reason,
            started_at=started_at,
            extra={
                "verdict": reduction.verdict,
                "passed_trackers": list(reduction.tracker_results.get("passed") or []),
            },
        )
        return log, reduction, check_results, False

    async def _run_arr_and_policy_stage(
        self,
        item_id: int,
        item: Mapping[str, Any],
        cfg: Any,
        mapped_path: str,
        log: str,
        reduction: Any,
        media_result: Mapping[str, Any],
        check_results: Dict[str, Any],
    ) -> None:
        ua_args = "--site-check -ua -sda"
        arr_results = {}
        status = reduction.status
        verdict = reduction.verdict
        reason = reduction.reason
        next_check_at = None
        if reduction.status == "candidate" and reduction.tracker_results.get("passed"):
            self.db.update_check_stage(item_id, "arr", "Running Arr comparison.", check_results)
            arr_started_at = time.perf_counter()
            local_traits = _arr_local_traits_from_media_result(media_result)
            async with self._arr_lock:
                arr_results = await compare_item_with_arr(
                    item_name=item["name"],
                    ua_log=log,
                    passed_trackers=reduction.tracker_results["passed"],
                    cfg=cfg,
                    secrets=self.secrets,
                    local_traits=local_traits,
                )
            status = str(arr_results.get("status") or "manual_review")
            verdict = "candidate" if status == "candidate" else ("not_upgrade" if status == "blocked" else "manual_review")
            reason = str(arr_results.get("reason") or reduction.reason)
            check_results = merge_check_results(
                check_results,
                arr=arr_results,
                flags=check_results.get("flags", []),
            )
            check_results = add_stage_diagnostic(
                check_results,
                stage="arr",
                status=status,
                reason=reason,
                started_at=arr_started_at,
                extra={"passed_trackers": list(reduction.tracker_results.get("passed") or [])},
            )
            self.db.update_check_stage(item_id, "policy", "Applying release group policy.", check_results)
            policy_started_at = time.perf_counter()
            status, policy_verdict, policy_reason, policy_result, flags = apply_release_group_policy(
                tracker_results=reduction.tracker_results,
                arr_results=arr_results,
                release_group=str(media_result.get("release_group") or ""),
                tracker_policies=cfg.tracker_policies,
                flags=check_results.get("flags", []),
                item_name=str(item["name"] or ""),
            )
            verdict = policy_verdict or verdict
            reason = policy_reason or reason
            srrdb_result: Dict[str, Any] = {}
            check_results = merge_check_results(
                check_results,
                arr=arr_results,
                release_group_policy=policy_result,
                flags=flags,
            )
            check_results = add_stage_diagnostic(
                check_results,
                stage="policy",
                status=status,
                reason=reason,
                started_at=policy_started_at,
                extra={
                    "candidate_trackers": list(policy_result.get("candidate_trackers") or []),
                    "blocked_trackers": list(policy_result.get("blocked_trackers") or []),
                },
            )
            if status == "candidate":
                self.db.update_check_stage(item_id, "srrdb", "Checking srrDB archived filename.", check_results)
                srrdb_started_at = time.perf_counter()
                srrdb_result = await verify_srrdb_release(
                    item_name=str(item["name"] or ""),
                    media_result=media_result,
                    client=_ThrottledSrrdbClient(self),
                    cache=self.db,
                )
                status, verdict, reason, flags = apply_srrdb_result(
                    status=status,
                    verdict=verdict,
                    reason=reason,
                    flags=flags,
                    srrdb_result=srrdb_result,
                )
                check_results = add_stage_diagnostic(
                    check_results,
                    stage="srrdb",
                    status=str(srrdb_result.get("status") or "skipped"),
                    reason=str(srrdb_result.get("reason") or ""),
                    started_at=srrdb_started_at,
                    extra={
                        "queried_name": str(srrdb_result.get("queried_name") or ""),
                        "matched": srrdb_result.get("matched"),
                    },
                )
            check_results = merge_check_results(
                check_results,
                arr=arr_results,
                srrdb=srrdb_result,
                release_group_policy=policy_result,
                flags=flags,
            )
        elif reduction.status == "error":
            next_check_at = self._next_error_check(item["attempt_count"], None)
        else:
            check_results = add_stage_diagnostic(
                check_results,
                stage="arr",
                status="skipped",
                reason="UA did not produce any passed trackers for Arr comparison.",
            )

        self.db.update_status(
            item_id,
            status,
            verdict,
            reason,
            mapped_path=mapped_path,
            ua_args=ua_args,
            ua_log=log,
            tracker_results=reduction.tracker_results,
            arr_results=arr_results,
            check_stage="done",
            check_results=check_results,
            next_check_at=next_check_at,
            increment_attempt=True,
        )

    def _next_error_check(self, attempt_count: int, retry_after: Optional[str]) -> int:
        cfg = self.config_manager.load()
        now = int(time.time())
        if retry_after:
            try:
                return now + max(60, int(retry_after))
            except ValueError:
                pass
        if attempt_count >= cfg.safety.max_error_retries:
            return now + (24 * 3600)
        backoff = cfg.safety.error_backoff_minutes[min(attempt_count, len(cfg.safety.error_backoff_minutes) - 1)]
        return now + (backoff * 60)


class _ThrottledSrrdbClient:
    def __init__(self, service: WhackamoleService) -> None:
        self.service = service
        self.client = SrrdbClient()

    async def details(self, release_name: str) -> Mapping[str, Any] | Sequence[Any]:
        async with self.service._srrdb_lock:
            elapsed = time.monotonic() - self.service._last_srrdb_request_at
            if elapsed < 3:
                await asyncio.sleep(3 - elapsed)
            self.service._last_srrdb_request_at = time.monotonic()
            return await self.client.details(release_name)
