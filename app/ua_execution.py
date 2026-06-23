from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Deque, Dict, Optional, Set

import httpx

from app.clients import UploadAssistantClient
from app.config import AppConfig, SecretStore


def sse_payload(kind: str, data: Any = "", **extra: Any) -> str:
    payload = {"type": kind, "data": data}
    payload.update(extra)
    return f"data: {json.dumps(payload)}\n\n"


@dataclass
class UaExecutionOwner:
    id: str
    kind: str
    label: str
    item_id: Optional[int]
    session_id: str
    started_at: int


class UaExecutionLease:
    def __init__(self, coordinator: "UaExecutionCoordinator", owner_id: str) -> None:
        self._coordinator = coordinator
        self._owner_id = owner_id
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._coordinator.release(self._owner_id)


class UaExecutionCoordinator:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._owner: Optional[UaExecutionOwner] = None

    async def acquire(
        self,
        *,
        kind: str,
        label: str,
        item_id: Optional[int] = None,
        session_id: str = "",
        wait: bool = True,
    ) -> Optional[UaExecutionLease]:
        async with self._condition:
            if not wait and self._owner is not None:
                return None
            while self._owner is not None:
                await self._condition.wait()
            owner_id = f"{kind}-{item_id or 'global'}-{int(time.time() * 1000)}"
            self._owner = UaExecutionOwner(
                id=owner_id,
                kind=kind,
                label=label,
                item_id=item_id,
                session_id=session_id,
                started_at=int(time.time()),
            )
            return UaExecutionLease(self, owner_id)

    async def release(self, owner_id: str) -> None:
        async with self._condition:
            if self._owner and self._owner.id == owner_id:
                self._owner = None
                self._condition.notify_all()

    def snapshot(self) -> Dict[str, Any]:
        owner = self._owner
        if owner is None:
            return {"busy": False}
        now = int(time.time())
        return {
            "busy": True,
            "kind": owner.kind,
            "label": owner.label,
            "item_id": owner.item_id,
            "session_id": owner.session_id,
            "started_at": owner.started_at,
            "held_seconds": max(0, now - owner.started_at),
        }


class UploadConsoleSession:
    def __init__(
        self,
        *,
        item_id: int,
        path: str,
        args: str,
        session_id: str,
        config: AppConfig,
        secrets: SecretStore,
        lease: UaExecutionLease,
    ) -> None:
        self.item_id = item_id
        self.path = path
        self.args = args
        self.session_id = session_id
        self.config = config
        self.secrets = secrets
        self.lease = lease
        self.state = "starting"
        self.started_at = int(time.time())
        self.finished_at = 0
        self._chunks: Deque[str] = deque()
        self._chunk_chars = 0
        self._subscribers: Set[asyncio.Queue[Optional[str]]] = set()
        self._lock = asyncio.Lock()
        self._finished = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def snapshot(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "path": self.path,
            "args": self.args,
            "session_id": self.session_id,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    async def subscribe(self) -> AsyncIterator[str]:
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        async with self._lock:
            replay = list(self._chunks)
            done = self._finished.is_set()
            if not done:
                self._subscribers.add(queue)
        try:
            for chunk in replay:
                yield chunk
            while not done:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def send_input(self, user_input: str) -> Dict[str, Any]:
        client = UploadAssistantClient(self.config, self.secrets.get("ua_bearer_token"))
        return await client.send_input(self.session_id, user_input)

    async def kill(self) -> Dict[str, Any]:
        if self.state not in {"complete", "error", "killed"}:
            self.state = "killing"
            client = UploadAssistantClient(self.config, self.secrets.get("ua_bearer_token"))
            try:
                result = await client.kill_session(self.session_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                result = {"success": True, "message": "Upload Assistant reported no active process."}
            self.state = "killed"
            await self._publish(sse_payload("system", "Upload Assistant session killed."))
            if self._task and not self._task.done():
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(self._task, timeout=2)
            await self._finish()
            return result
        await self._finish()
        return {"success": True, "message": "Session already finished"}

    async def _run(self) -> None:
        client = UploadAssistantClient(self.config, self.secrets.get("ua_bearer_token"))
        self.state = "running"
        await self._publish(sse_payload("system", f'Executing Upload Assistant for "{self.path}"'))
        try:
            async for chunk in client.execute_upload_stream(self.path, self.args, self.session_id):
                await self._publish(chunk)
            if self.state not in {"killing", "killed"}:
                self.state = "complete"
                await self._publish(sse_payload("complete", "Upload Assistant session finished."))
        except asyncio.CancelledError:
            if self.state not in {"killing", "killed"}:
                self.state = "cancelled"
                await self._publish(sse_payload("system", "Upload Assistant stream cancelled."))
            raise
        except httpx.HTTPStatusError as exc:
            self.state = "error"
            await self._publish(sse_payload("error", f"Upload Assistant HTTP error {exc.response.status_code}"))
        except Exception as exc:
            self.state = "error"
            await self._publish(sse_payload("error", str(exc)))
        finally:
            await self._finish()

    async def _publish(self, chunk: str) -> None:
        async with self._lock:
            self._chunks.append(chunk)
            self._chunk_chars += len(chunk)
            while self._chunk_chars > 1_000_000 and self._chunks:
                removed = self._chunks.popleft()
                self._chunk_chars -= len(removed)
            subscribers = list(self._subscribers)
        for queue in subscribers:
            await queue.put(chunk)

    async def _finish(self) -> None:
        if self._finished.is_set():
            return
        self.finished_at = int(time.time())
        self._finished.set()
        await self.lease.release()
        async with self._lock:
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for queue in subscribers:
            await queue.put(None)


class UploadConsoleManager:
    def __init__(self, coordinator: UaExecutionCoordinator) -> None:
        self.coordinator = coordinator
        self._session: Optional[UploadConsoleSession] = None
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        item_id: int,
        path: str,
        args: str,
        config: AppConfig,
        secrets: SecretStore,
    ) -> tuple[Optional[UploadConsoleSession], Dict[str, Any]]:
        async with self._lock:
            if self._session and self._session.state in {"starting", "running", "killing"}:
                return None, {"busy": True, "message": "Upload Assistant is already running.", "owner": self._session.snapshot()}
            session_id = f"whackamole-item-{item_id}-{int(time.time() * 1000)}"
            lease = await self.coordinator.acquire(
                kind="upload",
                label=f"Manual upload item {item_id}",
                item_id=item_id,
                session_id=session_id,
                wait=False,
            )
            if lease is None:
                return None, {"busy": True, "message": "Upload Assistant is busy.", "owner": self.coordinator.snapshot()}
            session = UploadConsoleSession(
                item_id=item_id,
                path=path,
                args=args,
                session_id=session_id,
                config=config,
                secrets=secrets,
                lease=lease,
            )
            self._session = session
            session.start()
            return session, {"busy": False}

    def get(self, session_id: str = "") -> Optional[UploadConsoleSession]:
        if self._session is None:
            return None
        if session_id and self._session.session_id != session_id:
            return None
        return self._session

    def snapshot(self) -> Dict[str, Any]:
        if self._session is None:
            return {"active": False}
        data = self._session.snapshot()
        data["active"] = self._session.state in {"starting", "running", "killing"}
        return data
