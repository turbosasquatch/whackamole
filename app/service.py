from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx

from app.clients import QuiClient, UploadAssistantClient
from app.config import ConfigManager, SecretStore
from app.database import Database
from app.filters import is_watchable_torrent
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

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def run(self) -> None:
        while not self._stop.is_set():
            cfg = self.config_manager.load()
            try:
                if cfg.qui.url and self.secrets.has("qui_api_key"):
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
        torrents = await client.list_torrents()
        baseline_done = self.db.get_kv("baseline_done") == "true"
        baseline_mode = not baseline_done and not cfg.watch.process_existing_on_first_run

        active_count = self.db.count_active_queue()
        for torrent in torrents:
            if not is_watchable_torrent(torrent, cfg.watch):
                continue
            torrent_hash = str(torrent.get("hash"))
            if self.db.item_exists(cfg.qui.instance_id, torrent_hash):
                continue
            if baseline_mode:
                self.db.insert_discovered(cfg.qui.instance_id, torrent, status="baseline", baseline=True)
                continue
            status = "queued" if active_count < cfg.safety.max_queue_size else "deferred"
            self.db.insert_discovered(cfg.qui.instance_id, torrent, status=status, baseline=False)
            active_count += 1

        if not baseline_done:
            self.db.set_kv("baseline_done", "true")

    async def run_due_jobs(self) -> None:
        cfg = self.config_manager.load()
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
        task_alive = self._task is not None and not self._task.done()
        return {
            "running": task_alive and not self._stop.is_set(),
            "running_jobs": self._running_jobs,
            "last_ua_job_started_at": int(self._last_ua_job_started_at or 0),
            "last_service_error": self.db.get_kv("last_service_error") or "",
            "baseline_done": self.db.get_kv("baseline_done") == "true",
        }

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

        try:
            mapped_path = map_path(item["content_path"], cfg.path_mappings)
        except ValueError as exc:
            self.db.update_status(
                item_id,
                "error",
                "path_mapping",
                str(exc),
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": ["Path mapping"]},
                increment_attempt=True,
            )
            return

        ua_args = "--site-check -ua -sda"
        ua = UploadAssistantClient(cfg, self.secrets.get("ua_bearer_token"))
        self.db.update_status(item_id, "checking", mapped_path=mapped_path, ua_args=ua_args)

        try:
            log = await ua.execute_site_check(mapped_path, ua_args, item["hash"])
        except httpx.HTTPStatusError as exc:
            next_check = self._next_error_check(item["attempt_count"], exc.response.headers.get("Retry-After"))
            self.db.update_status(
                item_id,
                "error",
                "http_error",
                f"UA HTTP error {exc.response.status_code}",
                mapped_path=mapped_path,
                ua_args=ua_args,
                ua_log=str(exc),
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": ["UA"]},
                next_check_at=next_check,
                increment_attempt=True,
            )
            return
        except Exception as exc:
            next_check = self._next_error_check(item["attempt_count"], None)
            self.db.update_status(
                item_id,
                "error",
                "ua_error",
                str(exc),
                mapped_path=mapped_path,
                ua_args=ua_args,
                ua_log=str(exc),
                tracker_results={"passed": [], "dupe": [], "skipped": [], "error": ["UA"]},
                next_check_at=next_check,
                increment_attempt=True,
            )
            return

        reduction = reduce_ua_log(log)
        self.db.update_status(
            item_id,
            reduction.status,
            reduction.verdict,
            reduction.reason,
            mapped_path=mapped_path,
            ua_args=ua_args,
            ua_log=log,
            tracker_results=reduction.tracker_results,
            next_check_at=None,
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
