from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.arr_compare import compare_item_with_arr
from app.clients import QuiClient, UploadAssistantClient
from app.config import ConfigManager, SecretStore
from app.database import Database
from app.filters import is_completed_torrent, is_watchable_torrent
from app.inventory import build_inventory_meta, is_inventory_support
from app.nfo_policy import (
    analyze_mediainfo,
    analyze_nfo,
    apply_release_group_policy,
    build_nfo_manual_result,
    decode_nfo_bytes,
    empty_check_results,
    merge_nfo_support,
    merge_check_results,
    nfo_file_candidates,
    video_file_payloads,
)
from app.pathmap import map_path
from app.reducer import reduce_ua_log


class WhackamoleService:
    def __init__(self, config_manager: ConfigManager, secrets: SecretStore, db: Database) -> None:
        self.config_manager = config_manager
        self.secrets = secrets
        self.db = db
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._running_jobs = 0
        self._last_ua_job_started_at = 0.0
        self._arr_lock = asyncio.Lock()
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
                self.db.set_kv("last_service_error", str(exc))

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
        full_crawl = not full_inventory_done
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
            self._last_ua_job_started_at = time.time()
            asyncio.create_task(self._run_item(item["id"]))

    def snapshot(self) -> dict:
        cfg = self.config_manager.load()
        task_alive = self._task is not None and not self._task.done()
        return {
            "running": task_alive and not self._stop.is_set(),
            "running_jobs": self._running_jobs,
            "last_ua_job_started_at": int(self._last_ua_job_started_at or 0),
            "last_service_error": self.db.get_kv("last_service_error") or "",
            "baseline_done": self.db.get_kv("baseline_done") == "true",
            "inventory_done": self.db.get_kv("inventory_done") == "true",
            "inventory_count": self.db.count_items([]),
            "queue": self.db.queue_counts(),
            "maintenance": self.maintenance_snapshot(cfg),
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
        check_results = empty_check_results()

        self.db.update_check_stage(item_id, "nfo", "Checking QUI MediaInfo identity before UA.", check_results)
        qui = QuiClient(cfg, self.secrets.get("qui_api_key"))
        try:
            torrent_files = await qui.list_torrent_files(str(item["hash"]))
            video_files = video_file_payloads(torrent_files)
            mediainfo_payloads = []
            for video_file in video_files[:10]:
                payload = await qui.torrent_file_mediainfo(str(item["hash"]), int(video_file["index"]))
                payload.setdefault("fileIndex", int(video_file["index"]))
                payload.setdefault("relativePath", str(video_file.get("name") or ""))
                mediainfo_payloads.append(payload)
            nfo_result = analyze_mediainfo(
                item_name=str(item["name"] or ""),
                files=torrent_files,
                mediainfo_payloads=mediainfo_payloads,
            )
            if len(video_files) > len(mediainfo_payloads):
                nfo_result["mediainfo_truncated"] = True
            nfo_candidates = nfo_file_candidates(torrent_files)
            if len(nfo_candidates) == 1:
                nfo_file = nfo_candidates[0]
                nfo_bytes = await qui.download_torrent_file(str(item["hash"]), int(nfo_file["index"]))
                nfo_text = decode_nfo_bytes(nfo_bytes)
                nfo_support = analyze_nfo(
                    item_name=str(item["name"] or ""),
                    files=torrent_files,
                    nfo_file=nfo_file,
                    nfo_text=nfo_text,
                )
                nfo_result = merge_nfo_support(nfo_result, nfo_support)
            elif len(nfo_candidates) > 1:
                nfo_result["supporting_nfo"] = {
                    "version": 1,
                    "source": "nfo",
                    "status": "manual_review",
                    "verdict": "nfo_ambiguous",
                    "reason": "Multiple NFO files were found. QUI MediaInfo was used as the primary identity check.",
                    "candidates": [
                        {"index": candidate["index"], "name": str(candidate.get("name") or "")}
                        for candidate in nfo_candidates
                    ],
                }
        except Exception as exc:
            nfo_result = build_nfo_manual_result(
                "mediainfo_unavailable",
                f"Whackamole could not read QUI MediaInfo: {str(exc)[:180]}",
                [],
            )
            check_results = merge_check_results(check_results, nfo=nfo_result, flags=nfo_result.get("flags", []))
            self.db.update_status(
                item_id,
                "manual_review",
                nfo_result["verdict"],
                nfo_result["reason"],
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
                arr_results={},
                check_stage="done",
                check_results=check_results,
                increment_attempt=True,
            )
            return

        check_results = merge_check_results(check_results, nfo=nfo_result, flags=nfo_result.get("flags", []))
        if nfo_result.get("status") != "passed":
            self.db.update_status(
                item_id,
                "manual_review",
                str(nfo_result.get("verdict") or "nfo_mismatch"),
                str(nfo_result.get("reason") or "NFO identity check failed."),
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": []},
                arr_results={},
                check_stage="done",
                check_results=check_results,
                increment_attempt=True,
            )
            return

        try:
            mapped_path = map_path(item["content_path"], cfg.path_mappings)
        except ValueError as exc:
            check_results = merge_check_results(
                check_results,
                flags=check_results.get("flags", []),
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
            return

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

        try:
            log = await ua.execute_site_check(mapped_path, ua_args, item["hash"])
        except httpx.HTTPStatusError as exc:
            next_check = self._next_error_check(item["attempt_count"], exc.response.headers.get("Retry-After"))
            check_results = merge_check_results(
                check_results,
                ua={"status": "error", "verdict": "http_error", "reason": f"UA HTTP error {exc.response.status_code}"},
                flags=check_results.get("flags", []),
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
            return
        except Exception as exc:
            next_check = self._next_error_check(item["attempt_count"], None)
            check_results = merge_check_results(
                check_results,
                ua={"status": "error", "verdict": "ua_error", "reason": str(exc)[:240]},
                flags=check_results.get("flags", []),
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
            return

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
        arr_results = {}
        status = reduction.status
        verdict = reduction.verdict
        reason = reduction.reason
        next_check_at = None
        if reduction.status == "candidate" and reduction.tracker_results.get("passed"):
            self.db.update_check_stage(item_id, "arr", "Running Arr comparison.", check_results)
            async with self._arr_lock:
                arr_results = await compare_item_with_arr(
                    item_name=item["name"],
                    ua_log=log,
                    passed_trackers=reduction.tracker_results["passed"],
                    cfg=cfg,
                    secrets=self.secrets,
                )
            status = str(arr_results.get("status") or "manual_review")
            verdict = "candidate" if status == "candidate" else ("not_upgrade" if status == "blocked" else "manual_review")
            reason = str(arr_results.get("reason") or reduction.reason)
            check_results = merge_check_results(
                check_results,
                arr=arr_results,
                flags=check_results.get("flags", []),
            )
            self.db.update_check_stage(item_id, "policy", "Applying release group policy.", check_results)
            status, policy_verdict, policy_reason, policy_result, flags = apply_release_group_policy(
                tracker_results=reduction.tracker_results,
                arr_results=arr_results,
                release_group=str(nfo_result.get("release_group") or ""),
                tracker_policies=cfg.tracker_policies,
                flags=check_results.get("flags", []),
                item_name=str(item["name"] or ""),
            )
            verdict = policy_verdict or verdict
            reason = policy_reason or reason
            check_results = merge_check_results(
                check_results,
                arr=arr_results,
                release_group_policy=policy_result,
                flags=flags,
            )
        elif reduction.status == "error":
            next_check_at = self._next_error_check(item["attempt_count"], None)

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
